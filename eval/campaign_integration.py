"""Bounded, controller-owned integration of pending function-boundary matches.

The caller holds the campaign lock and has drained all workers. This module
reuses isolated preparation/build gates and never installs canonical sources.
"""
import copy
import hashlib
import json
from pathlib import Path
import time

from eval import completion_campaign as campaign

POLICY = 'pending-boundary-union-v1'
LIMIT = 5


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_binding(node):
    return {'source_sha256': node['source_sha256'], 'attempt_id': node['attempt_id'],
            'verification_sha256': digest(node.get('verification', {}))}


def file_digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def evidence_key(node, pins, prior):
    certificate = node.get('verification', {})
    files = {node['source'], *certificate.get('build_inputs', {})}
    for item in certificate.get('function_boundary', {}).get('inputs', {}).values():
        if isinstance(item, dict) and item.get('path'):
            files.add(item['path'])
    candidate = certificate.get('function_boundary', {}).get('inputs', {}).get('candidate', {})
    if candidate.get('path'):
        files.add(str(Path(candidate['path']).with_suffix('.c')))
    return digest({'policy': POLICY, 'binding': source_binding(node), 'pins': pins,
                   'prior_union': prior, 'files': {p: file_digest(p) for p in sorted(files)}})


def entry(name, node):
    return {'function': name, **{k: copy.deepcopy(node[k])
            for k in ('source', 'attempt_id', 'verification')}}


def artifact_index(records, run_dir):
    artifacts = []
    for record in records:
        if not record.get('receipt'):
            continue
        receipt_path = Path(record['receipt']).resolve()
        if not receipt_path.is_relative_to(run_dir.resolve()):
            record['artifact_error'] = 'receipt outside campaign directory'
            continue
        try:
            receipt = json.loads(receipt_path.read_bytes())
            if not isinstance(receipt, dict):
                raise ValueError('receipt must be a JSON object')
        except (OSError, ValueError) as exc:
            record['artifact_error'] = f'{type(exc).__name__}: {exc}'
            continue
        for kind, path in [('receipt', receipt_path), ('log', receipt.get('build_log')),
                           ('rom', receipt.get('built_rom_artifact'))]:
            if isinstance(path, (str, Path)) and path:
                path = Path(path).resolve()
                if path.is_relative_to(run_dir.resolve()) and path.is_file():
                    artifacts.append({'kind': kind, 'path': path.relative_to(run_dir.resolve()).as_posix(),
                                      'sha256': file_digest(path)})
    return artifacts


def verify_union_receipt(records, survivors, bindings, repo):
    """Recheck durable bytes and source lineage before accepting a gate result."""
    names = {e['function'] for e in survivors}
    record = next((r for r in reversed(records) if r.get('status') == 'rom_exact'
                   and set(r.get('functions', [])) == names), None)
    if record is None:
        raise ValueError('combined survivor ROM receipt missing')
    path = Path(record['receipt'])
    receipt = json.loads(path.read_bytes())
    manifest_path = path.parent / (path.stem.removesuffix('-integration') + '-prepared') / 'manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    lineage = {r['function']: r for r in manifest['lineage']}
    if set(lineage) != names or any(
            lineage[n]['source_sha256'] != bindings[n]['source_sha256']
            or lineage[n]['attempt_id'] != bindings[n]['attempt_id']
            or digest(lineage[n]['verification']) != bindings[n]['verification_sha256'] for n in names):
        raise ValueError('combined manifest source lineage changed')
    reference = campaign.prepare_integration.inside(repo, manifest['reference_rom'])
    archive = Path(receipt['built_rom_artifact'])
    if not archive.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError('combined ROM archive outside receipt directory')
    target_hash = file_digest(reference)
    if (receipt.get('status') != 'rom_exact' or receipt.get('whole_rom_verified') is not True
            or receipt.get('manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest()
            or target_hash is None or manifest.get('reference_sha256') != target_hash
            or receipt.get('target_sha256') != target_hash or receipt.get('candidate_sha256') != target_hash
            or file_digest(archive) != target_hash or receipt.get('first_difference_offset') is not None
            or receipt.get('target_bytes') != reference.stat().st_size
            or receipt.get('candidate_bytes') != archive.stat().st_size):
        raise ValueError('combined whole-ROM receipt or archive changed')
    for replacement in manifest['replacements']:
        base = campaign.prepare_integration.inside(repo, replacement['path'])
        prepared = campaign.prepare_integration.inside(manifest_path.parent, replacement['replacement'])
        if (file_digest(base) != replacement['base_sha256']
                or file_digest(prepared) != replacement['replacement_sha256']):
            raise ValueError('combined manifest build input changed')


def stale_inputs(node):
    """Pinned certificate build inputs whose current bytes differ (or that no longer exist)."""
    return sorted(p for p, d in (node.get('verification') or {}).get('build_inputs', {}).items()
                  if file_digest(p) != d)


def _score_for_recertify(repo, db, name, source, parent_attempt_id, run_id):
    from solver import workspace
    import sqlite3
    ws = workspace.bootstrap(Path(repo), name)
    with sqlite3.connect(db, timeout=600) as conn:
        att = workspace.score(ws, Path(repo), f'{name}_recertify_{time.time_ns()}', source, conn=conn, func=name,
                              strategy='integration-recertify', run_id=run_id, relation='recertify',
                              parent_attempt_id=parent_attempt_id, action='re-certify under current build inputs')
        conn.commit()
    return att


def recertify(state, *, repo, db, score=_score_for_recertify):
    """Re-certify integrated and pending-integration nodes whose pinned build inputs changed.

    Why (2026-09-29): every sweep re-prepares the whole integrated union and requires each certificate's pinned
    build inputs to match byte for byte. Five members' certificates pinned a workspace build.sh and a per-function
    compiler recipe that later maintenance replaced (the recipe code deletes superseded .compiler-* files by
    design), so every sweep since checkpoint 22879 halted before trying anything new, while all 31 pending nodes
    stayed marked as already attempted. Nothing re-certified integrated nodes: `next_profile` skips them.

    A node keeps its place only when the SAME source (sha256 unchanged) re-scores under the current inputs as
    object-exact or ROM-backed function-exact with a passing frontend. Its certificate and attempt are replaced,
    and a matching `verified_source_bindings` entry moves to the new binding with a recorded receipt. Anything else
    is recorded as a named failure and changes nothing, so the union check still halts, but visibly.
    """
    nodes = state['nodes']
    history = state.setdefault('integration_sweep', {'schema_version': 1, 'policy': POLICY,
                                                    'attempted': {}, 'verified_union': []})
    records = []
    run_id = f'integration-recertify-{time.time_ns()}'
    for name in sorted(n for n, node in nodes.items()
                       if node.get('status') in ('integrated', 'function_exact_pending_integration')):
        node = nodes[name]
        stale = stale_inputs(node)
        if not stale:
            continue
        record = {'function': name, 'status': node['status'], 'stale_inputs': stale,
                  'old_binding': source_binding(node)}
        try:
            source = Path(node['source']).read_text(encoding='utf-8')
            if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
                raise ValueError('source file no longer matches the node binding')
            att = score(repo, db, name, source, node['attempt_id'], run_id)
            verification = att.verification or {}
            boundary = (verification.get('function_boundary') or {}).get('function_exact') is True
            frontend_ok = att.frontend is None or att.frontend.get('passed') is True
            if not (att.compiled and (att.exact or boundary) and frontend_ok and att.receipt_id is not None):
                raise ValueError(f'current inputs do not re-certify: compiled={att.compiled} exact={att.exact} '
                                 f'function_exact={boundary} frontend_ok={frontend_ok}')
            if stale_inputs({'verification': verification}):
                raise ValueError('new certificate is already stale')
            old = source_binding(node)
            node.update(attempt_id=att.receipt_id, verification=verification)
            new = source_binding(node)
            bound = history.setdefault('verified_source_bindings', {})
            if bound.get(name) == old:
                bound[name] = new
            record.update(result='recertified', new_binding=new, exact=att.exact, function_exact=boundary)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            record.update(result='failed', error=f'{type(exc).__name__}: {exc}')
        records.append(record)
    if records:
        history.setdefault('recertifications', []).append({'at': time.time(), 'records': records})
    return records


def sweep(state, *, repo, db, artifacts, checkpoint=None, on_started=None):
    """Return changed node names; suppressed/empty selections do no build work.

    Re-certification runs first: renewing stale members is what makes pending nodes eligible again (their
    evidence key includes the union's bindings). Renewed nodes are returned as changed on every path: the
    checkpoint store rewrites only nodes named in `changed`, so an unlisted renewal would be lost on resume.
    """
    if state.get('fast_inflight') or state.get('inflight'):
        raise ValueError('integration requires drained workers under the controller lock')
    renewed = [r['function'] for r in recertify(state, repo=repo, db=db) if r.get('result') == 'recertified']
    changed = _sweep(state, repo=repo, db=db, artifacts=artifacts, checkpoint=checkpoint, on_started=on_started)
    return sorted(set(changed) | set(renewed))


def _sweep(state, *, repo, db, artifacts, checkpoint=None, on_started=None):
    nodes = state['nodes']
    history = state.get('integration_sweep', {})
    old_names = sorted(n for n, node in nodes.items() if node['status'] == 'integrated')
    prior = {name: source_binding(nodes[name]) for name in old_names}
    pending = sorted(n for n, node in nodes.items() if node['status'] == 'function_exact_pending_integration')
    keys = {name: evidence_key(nodes[name], state['pins'], prior) for name in pending}
    selected = [name for name in pending if history.get('attempted', {}).get(name) != keys[name]][:LIMIT]
    if not selected:
        return []
    campaign.frozen_wavefront.verify_files(state['pins'])
    names = old_names + selected
    bindings = {name: source_binding(nodes[name]) for name in names}
    batch = [entry(name, nodes[name]) for name in names]
    tag = str(time.time_ns()) + '-integration-sweep'
    folder = Path(artifacts) / tag
    folder.mkdir(parents=True)
    latest = {'status': 'running', 'checkpoint': checkpoint, 'selected': selected,
              'prior_union': old_names, 'verified_union': [], 'source_bindings': bindings,
              'records': [], 'artifacts': [], 'scope': 'isolated whole-ROM verification; canonical sources unchanged',
              'complete_c_decompilation': False}
    if on_started is not None:
        progress = state.setdefault('integration_sweep', {'schema_version': 1, 'policy': POLICY,
                                                        'attempted': {}, 'verified_union': []})
        progress['latest'] = latest
        on_started()
    changed = []
    try:
        if not set(history.get('verified_union', [])) <= set(old_names):
            raise ValueError('previously verified union missing from current integrated nodes')
        if any(prior.get(n) != b for n, b in history.get('verified_source_bindings', {}).items()):
            raise ValueError('previously verified union source binding changed')
        for name in names:
            if file_digest(nodes[name]['source']) != bindings[name]['source_sha256']:
                raise ValueError('selected source hash changed: ' + name)
        eligible, blocked = campaign.preflight_integration(repo=repo, db=db, entries=batch,
                                                          artifacts=folder, tag='preflight')
        latest['records'].extend(blocked)
        eligible_names = {e['function'] for e in eligible}
        if not set(old_names) <= eligible_names:
            latest['status'] = 'integration_halted'
            latest['error'] = 'previously integrated union failed current preparation'
        elif not eligible_names.intersection(selected):
            latest['status'] = 'preparation_blocked'
        else:
            survivors, records = campaign.integrate_candidates(repo=repo, db=db, entries=eligible, artifacts=folder)
            latest['records'].extend(records)
            survived = {e['function'] for e in survivors}
            if not set(old_names) <= survived:
                latest['status'] = 'integration_halted'
                latest['error'] = 'new combined verification did not preserve previous union'
            elif not survived:
                latest['status'] = records[-1]['status'] if records else 'integration_halted'
            else:
                # Reconciliation uses current nodes/files, not cached preflight
                # identities. Revalidate certificates/lineage after the build.
                campaign.frozen_wavefront.verify_files(state['pins'])
                verify_union_receipt(records, survivors, bindings, repo)
                for name in names:
                    expected_status = 'integrated' if name in prior else 'function_exact_pending_integration'
                    if (nodes[name]['status'] != expected_status or source_binding(nodes[name]) != bindings[name]
                            or file_digest(nodes[name]['source']) != bindings[name]['source_sha256']):
                        raise ValueError('candidate changed during integration: ' + name)
                checked, stale = campaign.preflight_integration(repo=repo, db=db, entries=survivors,
                                                               artifacts=folder, tag='reconcile')
                if stale or {e['function'] for e in checked} != survived:
                    raise ValueError('post-build certificate/lineage revalidation failed')
                latest['status'] = 'rom_exact'
                latest['verified_union'] = sorted(survived)
                changed = sorted(survived.intersection(selected))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        latest.update(status='stale', error=f'{type(exc).__name__}: {exc}')
        changed = []
    latest['artifacts'] = artifact_index(latest['records'], Path(artifacts).parent)
    # Persist the auditable result before changing any node status. A crash
    # before the controller checkpoint leaves only an unimported private result.
    sweep_path = folder / 'sweep.json'
    sweep_path.write_text(json.dumps(latest, indent=2) + '\n')
    latest['artifacts'].append({'kind': 'receipt', 'path': sweep_path.relative_to(Path(artifacts).parent).as_posix(),
                                'sha256': file_digest(sweep_path)})
    history = state.setdefault('integration_sweep', {'schema_version': 1, 'policy': POLICY,
                                                   'attempted': {}, 'verified_union': []})
    history['attempted'].update({name: keys[name] for name in selected})
    history['latest'] = latest
    state.setdefault('integrations', []).extend(latest['records'])
    if latest['status'] == 'rom_exact':
        history['verified_union'] = latest['verified_union']
        history['verified_source_bindings'] = {n: bindings[n] for n in latest['verified_union']}
        history['latest_success'] = copy.deepcopy(latest)
        for name in changed:
            nodes[name]['status'] = 'integrated'
    return changed
