"""Drain checkpoint 29289 under old pins, then amend one frozen solver file.

Default is read-only validation. --apply is explicit and must be operator-run
only after reviewing RECOVERY.md and the staged test receipt. Never resumes the
controller or removes either pause marker.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

from inspect_checkpoint import CONTROL, FROZEN, NATIVE, STATE, sha
from stage_recovery import EXPECTED_JOBS, EXPECTED_OLD_SOLVER, EXPECTED_PIN, STAGE, delta, encode
from verify_stage import main as verify_stage

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[4]
LAUNCH = CONTROL / 'launch.json'
OLD_SOLVER = FROZEN / 'solver/mips_differential.py'
NEW_SOLVER = MAIN / 'solver/mips_differential.py'
NEW_SOLVER_SHA = '74ebe435b7bf0fa62aca16784e0df77e3ec366f28a9a88942a5ea0e64a7d7552'
READY = ('1790461389096471499-drawRaceSetupSaveChoicePrompts',
         '1790461392479571347-updateRaceScoreAttackRings')
CRASHED = '1790461381633671072-initAudioSynthesizer'
EXACT = {'object_exact', 'integrated'}
PROTECTED = EXACT | {'function_exact_pending_integration'}


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name('.' + path.name + '.recovery-new')
    with temporary.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_canonical_receipt(path: Path, result: dict) -> None:
    if path.exists():
        # JSON turns integer remap keys into strings. Compare the serialized
        # shape so an interrupted import can safely replay its original map.
        if json.loads(path.read_bytes()) != json_normalized(result):
            raise RuntimeError(f'conflicting canonical receipt: {path}')
        return
    atomic_bytes(path, json.dumps(result, separators=(',', ':'), ensure_ascii=True).encode())


def json_normalized(value):
    """Compare objects in the form campaign_state actually persists."""
    return json.loads(json.dumps(value, separators=(',', ':'), ensure_ascii=True))


def assert_persisted_metadata(persisted: dict, expected: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if persisted.get(field) != json_normalized(expected.get(field)):
            raise RuntimeError(f'checkpoint metadata round-trip failed: {field}')


def assert_job_set(state: dict, expected: set[str] | None = None) -> None:
    expected = expected if expected is not None else set(EXPECTED_JOBS)
    jobs = state.get('fast_inflight', [])
    ids = [job['id'] for job in jobs]
    if len(ids) != len(set(ids)) or not set(ids) <= expected or state.get('inflight'):
        raise RuntimeError('unexpected in-flight jobs')


def acquire(path: Path):
    import fcntl
    handle = path.open('a+b')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f'campaign lock held: {path}') from exc
    return handle


def pin_digest(pins: dict) -> str:
    return hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()


def current_metadata() -> tuple[dict, dict, bytes]:
    raw = STATE.read_bytes()
    pointer = json.loads(raw)
    if pointer.get('kind') != 'campaign-checkpoint-index-v1':
        raise RuntimeError('unexpected checkpoint format')
    with sqlite3.connect((NATIVE / pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
            raise RuntimeError('checkpoint manifest missing or corrupt')
        manifest = json.loads(row[0])
        stored = conn.execute('SELECT payload FROM objects WHERE hash=?',
                              (manifest['metadata'],)).fetchone()
        if stored is None:
            raise RuntimeError('checkpoint metadata missing')
        import zlib
        payload = zlib.decompress(stored[0])
        if hashlib.sha256(payload).hexdigest() != manifest['metadata']:
            raise RuntimeError('checkpoint metadata corrupt')
        metadata = json.loads(payload)
    if STATE.read_bytes() != raw:
        raise RuntimeError('checkpoint pointer moved during read')
    return pointer, metadata, raw


def manifest_for(pointer: dict) -> dict:
    with sqlite3.connect((NATIVE / pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
    if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
        raise RuntimeError('checkpoint manifest missing or corrupt')
    return json.loads(row[0])


def original_refs_and_jobs() -> tuple[dict, dict]:
    archived = json.loads((STAGE / 'campaign.pointer.before.json').read_bytes())
    original = manifest_for(archived)
    with sqlite3.connect((NATIVE / archived['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT payload FROM objects WHERE hash=?',
                           (original['metadata'],)).fetchone()
    if row is None:
        raise RuntimeError('original metadata object missing')
    import zlib
    payload = zlib.decompress(row[0])
    if hashlib.sha256(payload).hexdigest() != original['metadata']:
        raise RuntimeError('original metadata object corrupt')
    metadata = json.loads(payload)
    return original['nodes'], {job['id']: job for job in metadata['fast_inflight']}


def assert_original_node_refs(state: dict) -> None:
    """No resumed partial checkpoint may hide changes to unrelated nodes."""
    original, original_jobs = original_refs_and_jobs()
    current = manifest_for(json.loads(STATE.read_bytes()))['nodes']
    if set(current) != set(original):
        raise RuntimeError('campaign node inventory changed since checkpoint 29289')
    imported = {event['job_id'] for event in state.get('fast_recovery_events', [])
                if event.get('source_commit') == 29289
                and event.get('kind') == 'old-pin-raw-import'}
    if not imported <= set(READY):
        raise RuntimeError('unexpected accepted recovery job')
    allowed = {original_jobs[job_id]['function'] for job_id in imported}
    if any(current[name] != key for name, key in original.items() if name not in allowed):
        raise RuntimeError('unrelated or crashed node differs from immutable checkpoint 29289')
    # All three dispatch nodes were pending at the immutable boundary. If an
    # accepted node changed on a partial retry, it therefore cannot have
    # removed any original exact/function-exact member. Every other original
    # node reference must remain identical above.
    archived_pointer = json.loads((STAGE / 'campaign.pointer.before.json').read_bytes())
    with sqlite3.connect((NATIVE / archived_pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        import zlib
        for job_id in EXPECTED_JOBS:
            function = original_jobs[job_id]['function']
            key = original[function]
            row = conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()
            if row is None:
                raise RuntimeError(f'original ready node missing: {function}')
            payload = zlib.decompress(row[0])
            if hashlib.sha256(payload).hexdigest() != key:
                raise RuntimeError(f'original ready node corrupt: {function}')
            original_status = json.loads(payload)['status']
            if original_status != 'pending':
                raise RuntimeError(f'original in-flight node was not pending: {function}')


def checkpoint_view(function: str) -> tuple[dict, dict, dict, dict]:
    """Verify metadata and one node without hydrating the entire campaign."""
    pointer_bytes = STATE.read_bytes()
    pointer = json.loads(pointer_bytes)
    with sqlite3.connect((NATIVE / pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
            raise RuntimeError('saved checkpoint manifest missing or corrupt')
        manifest = json.loads(row[0])
        import zlib

        def load_object(key):
            stored = conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()
            if stored is None:
                raise RuntimeError(f'saved checkpoint object missing: {key}')
            payload = zlib.decompress(stored[0])
            if hashlib.sha256(payload).hexdigest() != key:
                raise RuntimeError(f'saved checkpoint object corrupt: {key}')
            return json.loads(payload)

        metadata = load_object(manifest['metadata'])
        node = load_object(manifest['nodes'][function])
    if STATE.read_bytes() != pointer_bytes:
        raise RuntimeError('checkpoint pointer moved during saved-state read')
    return pointer, metadata, node, manifest['nodes']


def assert_archived_delta(job: dict, archive: Path) -> None:
    with gzip.open(archive, 'rt', encoding='utf-8') as stream:
        expected = json.load(stream)
    if delta(job) != expected:
        raise RuntimeError(f'private database rows differ from staged archive: {job["id"]}')


def stage_manifest() -> dict:
    verify_stage()
    return json.loads((STAGE / 'manifest.json').read_bytes())


def validate_stage_test() -> dict:
    path = HERE / 'solver-stage.json'
    if not path.is_file():
        raise RuntimeError('missing staged frozen-package solver test receipt')
    receipt = json.loads(path.read_bytes())
    if (receipt.get('kind') != 'overflow-solver-stage-v1'
            or receipt.get('old_sha256') != EXPECTED_OLD_SOLVER
            or receipt.get('new_sha256') != NEW_SOLVER_SHA
            or receipt.get('main_test_sha256') != sha(MAIN / 'tests/test_mips_differential.py')
            or [r.get('label') for r in receipt.get('runs', [])] != ['frozen', 'main-regression']
            or any(r.get('returncode') != 0 for r in receipt['runs'])
            or not receipt.get('passed') or receipt.get('returncode') != 0):
        raise RuntimeError('staged solver test receipt invalid')
    return receipt


def validate_apply_receipt() -> None:
    receipt = json.loads((HERE / 'apply-validation.json').read_bytes())
    if (receipt.get('kind') != 'overflow-recovery-apply-validation-v1'
            or receipt.get('applied') is not False
            or receipt.get('source_commit') != 29289
            or receipt.get('apply_recovery_sha256') != sha(Path(__file__))
            or receipt.get('solver_stage_receipt_sha256') != sha(HERE / 'solver-stage.json')):
        raise RuntimeError('reviewed apply code or staged test receipt changed')


def validate_light() -> tuple[dict, dict, bytes]:
    archived = stage_manifest()
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both durable pause markers required')
    pointer, metadata, raw_pointer = current_metadata()
    if pin_digest(metadata['pins']) != EXPECTED_PIN or len(metadata['pins']) != 3314:
        raise RuntimeError('original pin set changed')
    if (metadata['pins'].get(str(OLD_SOLVER)) != EXPECTED_OLD_SOLVER
            or sha(OLD_SOLVER) != EXPECTED_OLD_SOLVER
            or sha(NEW_SOLVER) != NEW_SOLVER_SHA):
        raise RuntimeError('old/new solver file hash changed')
    if metadata['config']['project'] != str(FROZEN):
        raise RuntimeError('wrong campaign project')
    assert_job_set(metadata)
    events = metadata.get('fast_recovery_events', [])
    imported = {event['job_id'] for event in events
                if event.get('source_commit') == 29289}
    if pointer['commit'] == 29289:
        if (hashlib.sha256(raw_pointer).hexdigest() != archived['source_pointer_sha256']
                or imported or {j['id'] for j in metadata['fast_inflight']} != set(EXPECTED_JOBS)):
            raise RuntimeError('initial recovery boundary changed')
    elif not imported or imported | {j['id'] for j in metadata['fast_inflight']} != set(EXPECTED_JOBS):
        raise RuntimeError('checkpoint moved outside this recovery')
    assert_original_node_refs(metadata)
    if sha(LAUNCH) != archived['launch_sha256']:
        raise RuntimeError('launch changed before amendment')
    for job in metadata['fast_inflight']:
        if job['pin_sha256'] != EXPECTED_PIN or job['profile'].get('model'):
            raise RuntimeError(f'original job pin/model changed: {job["id"]}')
        expected_raw, _ = EXPECTED_JOBS[job['id']]
        archive = STAGE / (job['id'] + '.private-delta.json.gz')
        assert_archived_delta(job, archive)
        raw = Path(job['raw'])
        if raw.is_file() != expected_raw:
            raise RuntimeError(f'raw existence changed: {job["id"]}')
        if expected_raw and sha(raw) != archived['jobs'][job['id']]['raw_sha256']:
            raise RuntimeError(f'raw receipt changed: {job["id"]}')
        if job['id'] == CRASHED and Path(job['receipt']).exists():
            raise RuntimeError('crashed job unexpectedly has canonical receipt')
    _, original_jobs = original_refs_and_jobs()
    with sqlite3.connect((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        mapped = {row[0] for row in conn.execute(
            'SELECT job_id FROM campaign_worker_imports WHERE job_id IN (?,?,?)',
            tuple(EXPECTED_JOBS))}
    if mapped:
        campaign_workers = frozen_modules()[1]
        for job_id in mapped:
            check_import_mapping(original_jobs[job_id], EXPECTED_JOBS[job_id][1], campaign_workers)
    for job_id in set(EXPECTED_JOBS) - {j['id'] for j in metadata['fast_inflight']}:
        job = original_jobs[job_id]
        if job_id not in mapped:
            raise RuntimeError(f'checkpoint imported job lacks database lineage: {job_id}')
        if job_id in READY:
            if not Path(job['receipt']).is_file():
                raise RuntimeError(f'checkpoint imported job lacks canonical receipt: {job_id}')
            if sha(Path(job['raw'])) != archived['jobs'][job_id]['raw_sha256']:
                raise RuntimeError(f'original imported raw changed: {job_id}')
            amap, pmap = check_import_mapping(job, EXPECTED_JOBS[job_id][1], campaign_workers)
            event = next((row for row in events if row.get('job_id') == job_id
                          and row.get('kind') == 'old-pin-raw-import'), None)
            if (event is None or event.get('pin_sha256') != EXPECTED_PIN
                    or event.get('raw_sha256') != archived['jobs'][job_id]['raw_sha256']
                    or event.get('canonical_receipt') != job['receipt']
                    or event.get('attempt_ids') != json_normalized(amap)
                    or event.get('proposal_ids') != json_normalized(pmap)):
                raise RuntimeError(f'accepted recovery event differs from imported lineage: {job_id}')
            raw = json.loads(Path(job['raw']).read_bytes())
            expected = campaign_workers.remap(raw, amap, pmap)
            expected['private_lineage'] = {
                'raw_receipt': job['raw'], 'attempt_ids': amap,
                'proposal_ids': pmap, 'dispatch_profile': job['profile']}
            if json.loads(Path(job['receipt']).read_bytes()) != json_normalized(expected):
                raise RuntimeError(f'canonical receipt differs from original raw import: {job_id}')
            _, _, node, _ = checkpoint_view(job['function'])
            if not any(row.get('receipt') == job['receipt'] for row in node['jobs']):
                raise RuntimeError(f'checkpoint node lacks accepted job: {job_id}')
        else:
            aborted = Path(job['receipt']).with_suffix('.aborted.json')
            if not aborted.is_file():
                raise RuntimeError('checkpoint lacks crashed-job abort receipt')
            event = next((row for row in events if row.get('job_id') == job_id), None)
            amap, pmap = check_import_mapping(job, 5, campaign_workers)
            if (event is None or event.get('recovery_receipt_sha256') != sha(aborted)
                    or event.get('attempt_ids') != json_normalized(amap)
                    or event.get('proposal_ids') != json_normalized(pmap)):
                raise RuntimeError('crashed-job abort lineage differs from checkpoint')
    return pointer, metadata, raw_pointer


def frozen_modules():
    sys.path.insert(0, str(FROZEN))
    from eval import campaign_state, campaign_workers, completion_campaign, fast_campaign, repair_yield
    for module in (campaign_state, campaign_workers, completion_campaign, fast_campaign, repair_yield):
        if not Path(module.__file__).resolve().is_relative_to(FROZEN.resolve()):
            raise RuntimeError(f'nonfrozen campaign module: {module.__file__}')
    return campaign_state, campaign_workers, completion_campaign, fast_campaign, repair_yield


def verify_live(state: dict, campaign, *, integrity: bool = False) -> None:
    campaign.frozen_wavefront.verify_files(state['pins'])
    repo = Path(state['config']['repo'])
    actual = campaign._pins(FROZEN, repo)
    actual.update(campaign.frozen_wavefront.file_hashes([
        Path(p) for p in state['pins'] if Path(p).is_relative_to(repo / 'nonmatchings')]))
    if actual != state['pins']:
        raise RuntimeError('current frozen inputs differ from checkpoint pins')
    with sqlite3.connect((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        if integrity and conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('campaign database quick_check failed')
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    if campaign.digest(inventory) != state['inventory_sha256']:
        raise RuntimeError('campaign inventory changed')
    if integrity:
        with sqlite3.connect((NATIVE / 'campaign.state.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise RuntimeError('checkpoint object database quick_check failed')
    launch = json.loads(LAUNCH.read_bytes())
    if launch['model_digest'] != state['model_digest']:
        raise RuntimeError('model digest changed')


def check_import_mapping(job: dict, expected_count: int, campaign_workers=None) -> tuple[dict, dict]:
    with sqlite3.connect((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT mapping FROM campaign_worker_imports WHERE job_id=?',
                           (job['id'],)).fetchone()
    if row is None:
        raise RuntimeError(f'missing idempotency mapping for {job["id"]}')
    mapping = json.loads(row[0])
    if len(mapping['attempts']) != expected_count or mapping['proposals']:
        raise RuntimeError(f'wrong imported ID mapping for {job["id"]}')
    amap = {int(old): new for old, new in mapping['attempts'].items()}
    pmap = {int(old): new for old, new in mapping['proposals'].items()}
    if campaign_workers is not None:
        attest_main_rows(job, amap, pmap, campaign_workers)
    return amap, pmap


def attest_main_rows(job: dict, amap: dict, pmap: dict, campaign_workers) -> None:
    """Compare imported rows and edges with the staged private before-image."""
    with gzip.open(STAGE / (job['id'] + '.private-delta.json.gz'), 'rt', encoding='utf-8') as stream:
        archived = json.load(stream)
    with sqlite3.connect((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in archived['attempts']:
            if row.get('source_code') and row.get('source_sha256'):
                if hashlib.sha256(row['source_code'].encode()).hexdigest() != row['source_sha256']:
                    raise RuntimeError(f'archived attempt source hash mismatch: {row["id"]}')
            expected = campaign_workers.remap(row, amap, pmap)
            expected['id'] = amap[row['id']]
            if expected.get('sampling'):
                expected['sampling'] = json.dumps(
                    campaign_workers.remap(json.loads(expected['sampling']), amap, pmap), sort_keys=True)
            actual = conn.execute('SELECT * FROM attempts WHERE id=?', (expected['id'],)).fetchone()
            if actual is None or encode(dict(actual)) != expected:
                raise RuntimeError(f'imported attempt differs from archived private row: {row["id"]}')
        for row in archived['attempt_runs']:
            actual = conn.execute('SELECT * FROM attempt_runs WHERE id=?', (row['id'],)).fetchone()
            if actual is None or encode(dict(actual)) != row:
                raise RuntimeError(f'imported run differs from archived private row: {row["id"]}')
        expected_edges = [campaign_workers.remap(row, amap, pmap)
                          for row in archived['attempt_edges']]
        children = {row['child_attempt_id'] for row in expected_edges}
        actual_edges = [encode(dict(actual)) for child in children for actual in conn.execute(
            'SELECT * FROM attempt_edges WHERE child_attempt_id=?', (child,))]
        canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
        if canonical(actual_edges) != canonical(expected_edges):
            raise RuntimeError(f'imported edges differ from archived private rows: {job["id"]}')


def account_result(metrics: dict, result: dict) -> None:
    metrics['completed_items'] = metrics.get('completed_items', 0) + 1
    metrics['improved_items'] = metrics.get('improved_items', 0) + int(bool(result.get('best_score_improved')))
    metrics['exact_items'] = metrics.get('exact_items', 0) + int(bool(result.get('exact')))
    metrics['worker_seconds'] = metrics.get('worker_seconds', 0) + result['wall_seconds']
    performance = result['performance']
    for key in ('model_queue_seconds', 'model_seconds'):
        metrics[key] = metrics.get(key, 0) + performance[key]
    for key, value in performance.items():
        if key.startswith(('layout_', 'compile_', 'semantic_', 'panel_init_', 'target_work_')) or key in {
                'model_calls', 'model_load_seconds', 'model_prompt_seconds',
                'model_generation_seconds', 'model_prompt_tokens', 'model_generated_tokens',
                'worker_setup_seconds'}:
            metrics[key] = metrics.get(key, 0) + value


def save_import(state: dict, job: dict, result: dict, modules, mapping: dict) -> None:
    campaign_state, campaign_workers, campaign, fast_campaign, repair_yield = modules
    pointer_before, _, _, refs_before = checkpoint_view(job['function'])
    node = state['nodes'][job['function']]
    before_status = {'status': node['status']}
    campaign.accept(node, job['profile'], result, Path(job['receipt']))
    repair_yield.record(state['fast_metrics'], before_status, node, job['profile'], result)
    account_result(state['fast_metrics'], result)
    state['fast_inflight'] = [item for item in state['fast_inflight'] if item['id'] != job['id']]
    state.setdefault('fast_recovery_events', []).append({
        'kind': 'old-pin-raw-import', 'source_commit': 29289, 'job_id': job['id'],
        'pin_sha256': EXPECTED_PIN, 'raw_sha256': sha(Path(job['raw'])),
        'canonical_receipt': job['receipt'], 'attempt_ids': mapping['attempts'],
        'proposal_ids': mapping['proposals']})
    selected = fast_campaign.project(state)
    fast_campaign.summary(state, selected)
    campaign_state.Store(STATE).save(state, changed=(job['function'],))
    pointer_after, metadata, saved_node, refs_after = checkpoint_view(job['function'])
    assert_persisted_metadata(metadata, state, ('fast_inflight', 'fast_recovery_events',
                                                'summary', 'fast_metrics', 'pins'))
    if (pointer_after['commit'] <= pointer_before['commit']
            or saved_node != json_normalized(node)
            or any(refs_after[name] != ref for name, ref in refs_before.items()
                   if name != job['function'])
            or json.loads(Path(job['receipt']).read_bytes()) != json_normalized(result)):
        raise RuntimeError(f'accepted job checkpoint/receipt round-trip failed: {job["id"]}')
    assert_original_node_refs(state)
    check_import_mapping(job, EXPECTED_JOBS[job['id']][1], campaign_workers)


def import_ready(state: dict, modules, job_id: str) -> None:
    campaign_state, campaign_workers, campaign, fast_campaign, _ = modules
    job = next((j for j in state['fast_inflight'] if j['id'] == job_id), None)
    if job is None:
        return
    campaign.frozen_wavefront.verify_files(state['pins'])
    assert_archived_delta(job, STAGE / (job_id + '.private-delta.json.gz'))
    node = state['nodes'][job['function']]
    raw = json.loads(Path(job['raw']).read_bytes())
    fast_campaign.validate_job(node, job, raw)
    amap, pmap = campaign_workers.merge(NATIVE / 'campaign.sqlite', job['db'],
                                        job['cutoffs'], job['id'])
    if len(amap) != EXPECTED_JOBS[job_id][1] or pmap:
        raise RuntimeError(f'unexpected imported lineage for {job_id}')
    attest_main_rows(job, amap, pmap, campaign_workers)
    result = campaign_workers.remap(raw, amap, pmap)
    result['private_lineage'] = {
        'raw_receipt': job['raw'], 'attempt_ids': amap,
        'proposal_ids': pmap, 'dispatch_profile': job['profile']}
    write_canonical_receipt(Path(job['receipt']), result)
    save_import(state, job, result, modules, {'attempts': amap, 'proposals': pmap})


def import_crashed(state: dict, modules) -> None:
    campaign_state, campaign_workers, campaign, fast_campaign, _ = modules
    job = next((j for j in state['fast_inflight'] if j['id'] == CRASHED), None)
    if job is None:
        return
    if Path(job['raw']).exists():
        raise RuntimeError('crashed job acquired a raw receipt; stop and review')
    campaign.frozen_wavefront.verify_files(state['pins'])
    assert_archived_delta(job, STAGE / (CRASHED + '.private-delta.json.gz'))
    pointer_before, _, _, refs_before = checkpoint_view(job['function'])
    node = state['nodes'][job['function']]
    before = copy.deepcopy(node)
    if (node.get('source_sha256') != job['node'].get('source_sha256')
            or campaign.repair_queue.evidence_key(node) != job['profile']['evidence_key']
            or sha(Path(node['source'])) != node['source_sha256']):
        raise RuntimeError('crashed job source/evidence changed')
    amap, pmap = campaign_workers.merge(NATIVE / 'campaign.sqlite', job['db'],
                                        job['cutoffs'], job['id'])
    if len(amap) != 5 or pmap:
        raise RuntimeError('crashed job lineage count changed')
    attest_main_rows(job, amap, pmap, campaign_workers)
    log = MAIN / 'eval/results/frontier-run-20260926/after-fix/batch-0005/canary-controller.log'
    aborted = Path(job['receipt']).with_suffix('.aborted.json')
    event = {
        'kind': 'old-pin-crashed-job-abandoned-for-retry', 'source_commit': 29289,
        'job_id': job['id'], 'function': job['function'], 'profile': job['profile'],
        'pin_sha256': EXPECTED_PIN, 'raw_receipt': None,
        'failure': 'OverflowError: cannot convert float infinity to integer',
        'failure_log': str(log), 'failure_log_sha256': sha(log),
        'private_delta_sha256': json.loads((STAGE / 'manifest.json').read_bytes())['jobs'][job['id']]['delta_sha256'],
        'attempt_ids': amap, 'proposal_ids': pmap,
        'disposition': 'abandoned-before-raw; retry-after-amendment',
    }
    write_canonical_receipt(aborted, event)
    event['recovery_receipt'] = str(aborted)
    event['recovery_receipt_sha256'] = sha(aborted)
    state.setdefault('fast_recovery_events', []).append(event)
    state['fast_inflight'] = [item for item in state['fast_inflight'] if item['id'] != job['id']]
    selected = fast_campaign.project(state)
    fast_campaign.summary(state, selected)
    if state['nodes'][job['function']] != before:
        raise RuntimeError('crashed node changed during abort')
    campaign_state.Store(STATE).save(state)
    pointer_after, metadata, saved_node, refs_after = checkpoint_view(job['function'])
    assert_persisted_metadata(metadata, state, ('fast_inflight', 'fast_recovery_events', 'pins'))
    if (pointer_after['commit'] <= pointer_before['commit'] or refs_after != refs_before
            or saved_node != json_normalized(before)
            or sha(aborted) != event['recovery_receipt_sha256']):
        raise RuntimeError('crashed job checkpoint/receipt round-trip failed')
    assert_original_node_refs(state)
    check_import_mapping(job, 5, campaign_workers)


def protected_names(state: dict) -> set[str]:
    return {name for name, node in state['nodes'].items() if node['status'] in PROTECTED}


def node_digest(node: dict) -> str:
    return hashlib.sha256(json.dumps(node, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def drain(modules) -> dict:
    campaign_state, _, campaign, _, _ = modules
    state = campaign_state.read(STATE)
    initial_protected = protected_names(state)
    unchanged_nodes = {name: node_digest(node) for name, node in state['nodes'].items()
                       if name not in {'drawRaceSetupSaveChoicePrompts', 'updateRaceScoreAttackRings'}}
    failed_node = copy.deepcopy(state['nodes']['initAudioSynthesizer'])
    verify_live(state, campaign, integrity=True)
    for job_id in READY:
        import_ready(state, modules, job_id)
        if not initial_protected <= protected_names(state):
            raise RuntimeError('exact or function-exact membership decreased during raw import')
    import_crashed(state, modules)
    if state['nodes']['initAudioSynthesizer'] != failed_node:
        raise RuntimeError('crashed node changed')
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('recovery did not drain all jobs')
    if not initial_protected <= protected_names(state):
        raise RuntimeError('exact or function-exact membership decreased during drain')
    if any(node_digest(state['nodes'][name]) != digest for name, digest in unchanged_nodes.items()):
        raise RuntimeError('unrelated campaign node changed during drain')
    _, original_jobs = original_refs_and_jobs()
    for job_id in EXPECTED_JOBS:
        check_import_mapping(original_jobs[job_id], EXPECTED_JOBS[job_id][1], modules[1])
    return state


def require_archived_launch_entry(current: dict, archived: dict, key: str) -> str:
    original = archived.get('code_hashes', {}).get(key)
    if not isinstance(original, str) or current.get('code_hashes', {}).get(key) != original:
        raise RuntimeError('launch solver entry changed since archive')
    return original


def amend(state: dict, modules) -> None:
    campaign_state, _, campaign, _, _ = modules
    stage = validate_stage_test()
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('cannot amend with in-flight jobs')
    if pin_digest(state['pins']) != EXPECTED_PIN or sha(OLD_SOLVER) != EXPECTED_OLD_SOLVER:
        raise RuntimeError('old solver pin changed before amendment')
    previous_protected = protected_names(state)
    pointer_before = STATE.read_bytes()
    launch_before = LAUNCH.read_bytes()
    old_code = OLD_SOLVER.read_bytes()
    if sha(LAUNCH) != json.loads((STAGE / 'manifest.json').read_bytes())['launch_sha256']:
        raise RuntimeError('launch moved before amendment')
    launch = json.loads(launch_before)
    old_launch_sha = require_archived_launch_entry(
        launch, json.loads((STAGE / 'launch.before.json').read_bytes()), str(OLD_SOLVER))
    if old_launch_sha != '5ed8383bb11589ddbc23bd868595ba59dd005c1e289bde87114ac3a0a1b1f5ed':
        raise RuntimeError('archived launch solver entry differs from reviewed value')
    # These are before-images of the drained boundary; never overwrite them.
    for path, payload in ((HERE / 'campaign.drained.before.json', pointer_before),
                          (HERE / 'launch.drained.before.json', launch_before),
                          (HERE / 'solver.before.py', old_code)):
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f'conflicting drained before-image: {path}')
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(payload)
    installed_pointer = None
    installed_launch = None
    try:
        shutil.copy2(NEW_SOLVER, OLD_SOLVER)
        if sha(OLD_SOLVER) != NEW_SOLVER_SHA:
            raise RuntimeError('installed solver differs from reviewed bytes')
        changed = {str(OLD_SOLVER): {'old_sha256': EXPECTED_OLD_SOLVER,
                                     'old_launch_sha256': old_launch_sha,
                                     'new_sha256': NEW_SOLVER_SHA}}
        state['pins'][str(OLD_SOLVER)] = NEW_SOLVER_SHA
        amendment = {
            'kind': 'cvt-w-s-overflow-guard', 'applied_at': time.time(),
            'source_checkpoint': json.loads(pointer_before)['commit'],
            'source_pointer_sha256': hashlib.sha256(pointer_before).hexdigest(),
            'changed': changed, 'unchanged_pins_verified': len(state['pins']) - 1,
            'stage_test_sha256': sha(HERE / 'solver-stage.json'),
            'stage_test': stage,
            'limits': 'One frozen solver file only; no candidate, receipt, model, input, or ledger mutation.'}
        state.setdefault('runtime_amendments', []).append(amendment)
        campaign_state.Store(STATE).save(state)
        installed_pointer = STATE.read_bytes()
        launch['code_hashes'][str(OLD_SOLVER)] = NEW_SOLVER_SHA
        atomic_bytes(LAUNCH, json.dumps(launch, separators=(',', ':')).encode())
        installed_launch = LAUNCH.read_bytes()
        verify_live(state, campaign)
        restored = campaign_state.read(STATE)
        if (restored['pins'] != state['pins'] or protected_names(restored) != previous_protected
                or restored['nodes'] != json_normalized(state['nodes']) or restored.get('fast_inflight')):
            raise RuntimeError('amendment changed nodes or failed checkpoint round-trip')
        amendment['result'] = {'commit': json.loads(STATE.read_bytes())['commit'],
                               'pointer_sha256': sha(STATE), 'launch_sha256': sha(LAUNCH),
                               'new_pin_digest': pin_digest(state['pins']),
                               'object_exact_or_integrated': restored['summary']['object_exact_or_integrated']}
        write_canonical_receipt(HERE / 'amendment.json', amendment)
    except BaseException as exc:
        if (STATE.read_bytes() not in (pointer_before, installed_pointer)
                or LAUNCH.read_bytes() not in (launch_before, installed_launch)
                or sha(OLD_SOLVER) not in (EXPECTED_OLD_SOLVER, NEW_SOLVER_SHA)):
            raise RuntimeError('amendment state moved unexpectedly; manual locked recovery required') from exc
        atomic_bytes(OLD_SOLVER, old_code)
        atomic_bytes(STATE, pointer_before)
        atomic_bytes(LAUNCH, launch_before)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='mutate the paused campaign explicitly')
    args = parser.parse_args()
    validate_apply_receipt()
    if not args.apply:
        pointer, metadata, _ = validate_light()
        print(json.dumps({'mode': 'read-only', 'commit': pointer['commit'],
                          'inflight': [j['id'] for j in metadata['fast_inflight']],
                          'old_pin_digest': EXPECTED_PIN,
                          'staged_solver_tests': (HERE / 'solver-stage.json').is_file()}, indent=2))
        return
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both durable pause markers required')
    handles = [acquire(CONTROL / 'resume-supervisor.lock'), acquire(STATE.with_suffix('.lock'))]
    try:
        validate_light()
        validate_stage_test()
        modules = frozen_modules()
        state = drain(modules)
        amend(state, modules)
        print(json.dumps({'applied': True, 'commit': json.loads(STATE.read_bytes())['commit'],
                          'solver_sha256': sha(OLD_SOLVER), 'inflight': []}, indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
