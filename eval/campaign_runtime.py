"""Fresh, bounded emulator entry captures owned by a drained campaign controller.

Capture inputs remain diagnostic observations. No runtime result promotes or
demotes an exact candidate, and no canonical source or live attempt DB is edited.
"""
import copy
import errno
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time

from eval import campaign_workers, completion_campaign as campaign
from solver import evidence_schedule, project64_capture, runtime_capture, workspace

POLICY = 'fresh-project64-entry-v1'
LIMIT = 2
EXACT = {'object_exact', 'integrated', 'function_exact_pending_integration'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def binding(node):
    return {'source_sha256': node['source_sha256'], 'attempt_id': node['attempt_id'],
            'verification_sha256': evidence_schedule.fingerprint(node.get('verification', {}))}


def write(path, value):
    campaign.agentrepair._atomic_json(Path(path), value)


def read_manifest(path, expected):
    if sha(path) != expected:
        raise ValueError('runtime capture manifest changed; explicit amendment required')
    manifest = json.loads(Path(path).read_bytes())
    if manifest.get('schema_version') != 1:
        raise ValueError('unsupported runtime capture manifest')
    plans = manifest.get('plans', [])
    if not isinstance(plans, list) or not 1 <= len(plans) <= 16:
        raise ValueError('runtime capture requires one to sixteen static plans')
    if not 3600 <= manifest.get('refresh_seconds', 86400) <= 30 * 86400:
        raise ValueError('capture refresh cadence must be one hour to thirty days')
    seen = set()
    for item in plans:
        name = item.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in seen:
            raise ValueError('capture plan IDs must be distinct safe identifiers')
        seen.add(name)
        runtime_capture.validate_plan(item['plan'])
        if item.get('selector', 'first') not in {'first', 'a0_nonzero'}:
            raise ValueError('unsupported capture entry selector')
        if not 5 <= item.get('timeout_seconds', 60) <= 120:
            raise ValueError('capture timeout must be five to 120 seconds')
    for field in ('windows_python', 'portable_dir'):
        if not re.match(r'^[A-Za-z]:[\\/]', manifest.get(field, '')):
            raise ValueError(field + ' must name an explicit Windows absolute path')
    return manifest


def windows_path(path):
    return subprocess.check_output(['wslpath', '-w', str(Path(path).resolve())], text=True).strip()


def linux_path(path):
    return Path(subprocess.check_output(['wslpath', '-u', str(path)], text=True).strip())


def launch(manifest, item, *, rom, folder):
    """Fixed Windows module invocation; plans contain data, never commands."""
    output = folder / 'emulator'
    job = {'schema_version': 1, 'plan': item['plan'], 'rom_path': windows_path(rom),
           'portable_dir': manifest['portable_dir'], 'output_dir': windows_path(output),
           'selector': item.get('selector', 'first'), 'timeout_seconds': item.get('timeout_seconds', 60)}
    job_path = folder / 'launch-job.json'
    write(job_path, job)
    with (folder / 'launcher.log').open('wb') as stream:
        executable = str(linux_path(manifest['windows_python']))
        command = [executable, '-m', 'eval.project64_runner', '--job', windows_path(job_path)]
        options = dict(cwd=Path(__file__).resolve().parents[1], stdout=stream,
                       stderr=subprocess.STDOUT, timeout=job['timeout_seconds'] + 45)
        try:
            process = subprocess.run(command, **options)
        except OSError as exc:
            if exc.errno != errno.ENOEXEC or not Path('/init').is_file():
                raise
            # Explicit WSL interop when the binfmt handler is unavailable.
            # /init consumes the executable then the preserved argv[0]. No
            # kernel registration or system configuration is modified.
            process = subprocess.run(['/init', executable, *command], **options)
    receipt_path = output / 'receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    if process.returncode or receipt.get('status') != 'captured':
        raise ValueError('emulator capture unavailable: ' + str(receipt.get('status')) + ': ' + str(receipt.get('error', '')))
    raw_path = linux_path(receipt['raw_path']).resolve()
    if not raw_path.is_relative_to(output.resolve()):
        raise ValueError('emulator raw capture escaped its private output directory')
    # Reimport raw observations independently rather than trust a launcher pass.
    record = project64_capture.import_export(json.loads(raw_path.read_bytes()), item['plan'], rom)
    runtime_capture.verify(record, rom)
    return record


def compile_candidate(*, repo, db, function, node, folder):
    """Fresh selected-C build in a native private workspace; private attempt log."""
    from solver import compiler_recipe, modelrepair
    native = Path(tempfile.mkdtemp(prefix='decomp-runtime-candidate-'))
    isolated = campaign_workers.isolate(repo, native / 'game', function)
    ws = isolated / 'nonmatchings' / function
    with sqlite3.connect(f'file:{Path(db).as_posix()}?mode=ro', uri=True) as source_db:
        row = source_db.execute('SELECT f.addr,f.size,t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?',
                                (function,)).fetchone()
    if row is None:
        raise ValueError('runtime candidate lacks function/TU metadata')
    local_db = folder / 'attempts.sqlite'
    with sqlite3.connect(local_db) as conn:
        conn.executescript((Path(__file__).resolve().parents[1] / 'kb/schema.sql').read_text())
        conn.execute('INSERT INTO tus(id,name) VALUES(1,?)', (row[2],))
        conn.execute('INSERT INTO functions(addr,name,size,tu_id) VALUES(?,?,?,1)', (row[0], function, row[1]))
        conn.commit()
        source = Path(node['source']).read_text()
        if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
            raise ValueError('runtime selected C source hash mismatch')
        compiler_recipe._resolve.cache_clear()
        name = function + '_runtime_capture'
        try:
            attempt = workspace.score(ws, isolated, name, source, conn=conn, func=function,
                                      strategy='runtime-capture-selected-source', model='zero-model',
                                      run_id='runtime-capture-' + str(time.time_ns()),
                                      run_config={'source_binding': binding(node), 'canonical_parent_attempt_id': node['attempt_id']})
        finally:
            compiler_recipe._resolve.cache_clear()
    write(folder / 'compile.json', asdict(attempt))
    if not attempt.compiled or (attempt.frontend or {}).get('passed') is not True:
        raise ValueError('current selected C failed compilation or project frontend checks')
    obj = ws / (name + '.o')
    target = workspace.semantic_assembly(workspace.target_asm(ws, function), ws / 'target.o')
    candidate = workspace.semantic_assembly(obj.with_name(obj.stem + '_object_dump_normalized.s').read_text(), obj)
    (folder / 'selected.c').write_text(source)
    (folder / 'target.s').write_text(target)
    (folder / 'candidate.s').write_text(candidate)
    return isolated, ws, modelrepair.CandidateState(source, attempt, obj), target, candidate


def evaluate(*, compiled, record, captures, repo, rom, node, config):
    """Always compare the real compiled candidate; never substitute target self."""
    from eval import captured_panel, semantic_lane
    isolated, ws, candidate, target_asm, candidate_asm = compiled
    direct = runtime_capture.replay(record, rom, target_asm, candidate_asm, repo=repo,
                                    return_registers=tuple(record['plan'].get('return_registers', ['v0'])),
                                    max_steps=config.get('semantic_steps', 10000))
    combined = None
    if node['status'] not in EXACT:
        base = semantic_lane.DeferredPanel(isolated, ws, record['plan']['function'],
                                          config.get('semantic_cases', 64), config.get('semantic_steps', 10000))
        combined = captured_panel.Panel(base, isolated, ws, record['plan']['function'], captures)(candidate)
    return direct, combined


def index_artifacts(folder, root):
    rows = []
    # Deliberately exclude copied emulator settings/ROM and large private DBs.
    paths = [*folder.glob('*.json'), *folder.glob('*.log')]
    paths += [folder / 'emulator' / name for name in ('receipt.json', 'project64-entry-raw.json', 'capture.json', 'capture-plan.json')]
    for path in sorted(set(paths)):
        if path.is_file() and path.resolve().is_relative_to(root.resolve()):
            rows.append({'kind': 'log' if path.suffix == '.log' else 'receipt',
                         'path': path.relative_to(root).as_posix(), 'sha256': sha(path)})
    return rows


def sweep(state, *, repo, artifacts, plan_path, checkpoint=None, on_progress=None):
    if state.get('fast_inflight') or state.get('inflight'):
        raise ValueError('runtime capture requires drained workers under controller lock')
    manifest = read_manifest(plan_path, state['config'].get('runtime_capture_plan_sha256'))
    campaign.frozen_wavefront.verify_files(state['pins'])
    rom = Path(repo) / 'snowboardkids.z64'
    history = state.setdefault('runtime_capture', {'schema_version': 1, 'policy': POLICY, 'attempted': {},
                                                  'active': {}, 'results': {}})
    # An amended plan must not leave its previous managed inputs in worker
    # configuration. Unmanaged preexisting captures retain their own scope.
    current_plans = {item['id']: evidence_schedule.fingerprint(item) for item in manifest['plans']}
    removed = {name: row for name, row in history['active'].items()
               if current_plans.get(name) != row.get('plan_sha256')}
    history['active'] = {name: row for name, row in history['active'].items() if name not in removed}
    retained_hashes = {row['capture_sha256'] for row in history['active'].values()}
    for row in removed.values():
        captures = state['config'].get('runtime_captures', {}).get(row['function'], [])
        state['config'].setdefault('runtime_captures', {})[row['function']] = [c for c in captures
            if c['sha256'] != row['capture_sha256'] or c['sha256'] in retained_hashes]
    now = time.time()
    changed = set()
    completed = 0
    for item in manifest['plans']:
        function = item['plan']['function']
        if function not in state['nodes']:
            continue
        node = state['nodes'][function]
        source_binding = binding(node)
        key = evidence_schedule.fingerprint({'policy': POLICY, 'plan': item, 'source': source_binding,
                                              'pins': state['pins'], 'manifest_sha256': sha(plan_path)})
        prior = history['attempted'].get(item['id'], {})
        if (prior.get('evidence_key') == key
                and now - prior.get('at', 0) < manifest.get('refresh_seconds', 86400)):
            continue
        if completed >= LIMIT:
            break
        completed += 1
        folder = Path(artifacts) / (str(time.time_ns()) + '-runtime-' + item['id'])
        folder.mkdir(parents=True)
        latest = {'status': 'capturing', 'function': function, 'checkpoint': checkpoint,
                  'source_binding': source_binding, 'plan_id': item['id'], 'capture_count': 0,
                  'counts': {'passed': 0, 'failed': 0, 'inconclusive': 0}, 'artifacts': [],
                  'started_at': time.time(), 'updated_at': time.time(), 'authoritative': False,
                  'scope': 'captured integer-leaf entry; not whole-game equivalence'}
        history['latest'] = latest
        if on_progress:
            on_progress()
        record = None
        try:
            if sha(node['source']) != source_binding['source_sha256']:
                raise ValueError('runtime selected source changed before capture')
            record = launch(manifest, item, rom=rom, folder=folder)
            runtime_capture.verify(record, rom)
            if record['plan'] != item['plan']:
                raise ValueError('returned capture belongs to another plan')
            write(folder / 'capture.json', record)
            latest.update(status='replaying', capture_count=1, updated_at=time.time())
            if on_progress:
                on_progress()
            captures = list(state['config'].get('runtime_captures', {}).get(function, []))
            old = history['active'].get(item['id'], {})
            other_owners = {row['capture_sha256'] for name, row in history['active'].items() if name != item['id']}
            captures = [r for r in captures if (r['sha256'] != old.get('capture_sha256') or r['sha256'] in other_owners)
                        and r['sha256'] != record['sha256']]
            captures.append(record)
            compiled = compile_candidate(repo=Path(repo), db=Path(state['config']['db']), function=function,
                                         node=node, folder=folder)
            direct, combined = evaluate(compiled=compiled, record=record, captures=captures, repo=Path(repo),
                                        rom=rom, node=node, config=state['config'])
            write(folder / 'replay.json', {'source_binding': source_binding, 'direct': direct, 'combined': combined})
            campaign.frozen_wavefront.verify_files(state['pins'])
            if binding(node) != source_binding or sha(node['source']) != source_binding['source_sha256']:
                raise ValueError('selected source changed during capture/replay')
            runtime_capture.verify(record, rom)
            status = direct['comparison']['status']
            if status not in latest['counts']:
                raise ValueError('unknown runtime replay outcome')
            if combined is not None and combined.get('source_sha256') != source_binding['source_sha256']:
                raise ValueError('combined runtime panel has stale source identity')
            latest['counts'][status] = 1
            latest['status'] = status if status in {'passed', 'failed'} else 'unavailable'
            latest['capture_sha256'] = record['sha256']
            state['config'].setdefault('runtime_captures', {})[function] = captures
            history['active'][item['id']] = {'function': function, 'capture_sha256': record['sha256'],
                                           'plan_sha256': evidence_schedule.fingerprint(item),
                                           'path': str(folder / 'capture.json')}
            if combined is not None and node['status'] not in EXACT:
                write(folder / 'previous-semantic.json', node.get('semantic_validation'))
                node['semantic_validation'] = combined
            changed.add(function)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            latest.update(status='unavailable', error=f'{type(exc).__name__}: {exc}')
            latest['counts'] = {'passed': 0, 'failed': 0, 'inconclusive': 1}
        latest['updated_at'] = time.time()
        write(folder / 'runtime.json', latest)
        latest['artifacts'] = index_artifacts(folder, Path(artifacts).parent)
        history['attempted'][item['id']] = {'evidence_key': key, 'at': time.time()}
        history['results'][item['id']] = copy.deepcopy(latest)
        node['runtime_capture_validation'] = copy.deepcopy(latest)
        changed.add(function)
        if on_progress:
            on_progress(changed=sorted(changed))
    return sorted(changed)
