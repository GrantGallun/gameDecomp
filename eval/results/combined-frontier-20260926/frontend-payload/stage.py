"""Pin the reviewed frontend payload against the post-branch frozen boundary.

This stage is read-only with respect to the campaign. Run inside WSL after the
branch amendment and before any campaign job is launched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[3]
CONTROL = MAIN / 'eval/results/resume-pipeline-20260908'
FROZEN = CONTROL / 'code'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
LAUNCH = CONTROL / 'launch.json'
STAGED = HERE / 'staged'
BRANCH_AMENDMENT = HERE.parent / 'deployment/amendment.json'
EXPECTED_PIN_COUNT = 3315
EXPECTED_EXACT = 1003
EXPECTED_PENDING = 24
CHANGED = (
    'solver/frontend_diagnostics.py', 'solver/global_scalar_view.py',
    'solver/global_field_view.py', 'solver/stack_scalar_arrays.py',
    'solver/header_signature_view.py', 'solver/call_arity_repair.py',
    'solver/modelrepair.py', 'solver/frontend_fixits.py',
    'solver/compile_recovery.py', 'solver/compile_obligations.py',
    'solver/typedecl.py', 'solver/void_field_repair.py',
)
NEW = set(CHANGED[:6])
TESTS = (
    'tests/test_global_scalar_view.py', 'tests/test_global_field_view.py',
    'tests/test_stack_scalar_arrays.py', 'tests/test_header_signature_view.py',
    'tests/test_call_arity_repair.py', 'tests/test_modelrepair_frontend_routes.py',
    'tests/test_frontend_fixits.py', 'tests/test_frontend_full_diagnostics.py',
    'tests/test_intake_self_header.py', 'tests/test_header_alias_recovery.py',
    'tests/test_modelrepair.py', 'tests/test_opaque_declarations.py',
    'tests/test_compile_obligations_alias.py', 'tests/test_typedecl.py',
    'tests/test_void_field_repair.py',
)
FROZEN_TEST = 'tests/test_compile_recovery_placeholder.py'


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def pin_digest(pins: dict) -> str:
    return digest_bytes(json.dumps(pins, sort_keys=True).encode())


def lock(path: Path):
    import fcntl
    handle = path.open('a+b')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def expected_branch() -> dict:
    branch = json.loads(BRANCH_AMENDMENT.read_bytes())
    result = branch['result']
    if (branch.get('kind') != '20260926-branch-defaults-main-solver' or
            result['object_exact_or_integrated'] != EXPECTED_EXACT or
            result['function_exact_pending_integration'] != EXPECTED_PENDING):
        raise RuntimeError('branch amendment receipt differs from reviewed boundary')
    return result


def boundary(campaign_state, completion_campaign):
    branch = expected_branch()
    if not (CONTROL / 'service.pause').is_file() or not (NATIVE / 'service.pause').is_file():
        raise RuntimeError('both durable pause markers required')
    pointer_bytes, launch_bytes = STATE.read_bytes(), LAUNCH.read_bytes()
    pointer, launch = json.loads(pointer_bytes), json.loads(launch_bytes)
    state = campaign_state.read(STATE)
    if (pointer['commit'] != branch['commit'] or
            digest_bytes(pointer_bytes) != branch['pointer_sha256'] or
            digest_bytes(launch_bytes) != branch['launch_sha256']):
        raise RuntimeError('checkpoint or launch moved since branch amendment')
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('campaign has in-flight work')
    if (state['summary']['object_exact_or_integrated'] != EXPECTED_EXACT or
            state['summary']['function_exact_pending_integration'] != EXPECTED_PENDING or
            len(state['pins']) != EXPECTED_PIN_COUNT or
            pin_digest(state['pins']) != branch['new_pin_digest']):
        raise RuntimeError('post-branch counts or pins differ')
    if state['config']['project'] != str(FROZEN):
        raise RuntimeError('wrong frozen project')
    command = launch['command']
    if (command[command.index('--state') + 1] != str(STATE) or
            command[command.index('--project') + 1] != str(FROZEN)):
        raise RuntimeError('launch points at another campaign')
    for path, expected in state['pins'].items():
        p = Path(path)
        if not p.is_file() or sha(p) != expected:
            raise RuntimeError(f'old pinned file differs: {path}')
    with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('campaign database integrity check failed')
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    if completion_campaign.digest(inventory) != state['inventory_sha256']:
        raise RuntimeError('function inventory changed')
    if STATE.read_bytes() != pointer_bytes or LAUNCH.read_bytes() != launch_bytes:
        raise RuntimeError('boundary moved during inspection')
    return pointer_bytes, launch_bytes, state, launch


def main() -> None:
    payload = json.loads((HERE / 'manifest.json').read_bytes())
    if (payload.get('kind') != 'staged-frontend-header-capability-v1' or
            set(payload['changed']) != set(CHANGED)):
        raise RuntimeError('frontend payload scope differs')
    handles = [lock(CONTROL / 'resume-supervisor.lock'), lock(STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state, completion_campaign
        pointer_bytes, launch_bytes, state, launch = boundary(campaign_state, completion_campaign)
        changed = {}
        for rel, pinned in payload['changed'].items():
            if not rel.startswith('solver/') or Path(rel).is_absolute() or '..' in Path(rel).parts:
                raise RuntimeError(f'unsafe staged path: {rel}')
            staged, target = STAGED / rel, FROZEN / rel
            old = sha(target) if target.is_file() else None
            if sha(staged) != pinned['new_sha256'] or old != pinned['old_sha256']:
                raise RuntimeError(f'payload bytes differ: {rel}')
            if rel in NEW:
                if old is not None or str(target) in state['pins'] or str(target) in launch.get('code_hashes', {}):
                    raise RuntimeError(f'new module already exists: {rel}')
            elif state['pins'].get(str(target)) != old:
                raise RuntimeError(f'old code pin differs: {rel}')
            changed[rel] = {**pinned, 'old_launch_sha256': launch.get('code_hashes', {}).get(str(target))}
        if any(not (STAGED / rel).is_file() for rel in TESTS):
            raise RuntimeError('missing focused test')
        test_hashes = {rel: sha(STAGED / rel) for rel in TESTS}
        manifest = {
            'kind': 'unapplied-combined-frontier-frontend',
            'source_commit': json.loads(pointer_bytes)['commit'],
            'source_pointer_sha256': digest_bytes(pointer_bytes),
            'source_launch_sha256': digest_bytes(launch_bytes),
            'state_path': str(STATE), 'frozen_project': str(FROZEN),
            'changed': changed, 'tests': test_hashes,
            'frozen_test_sha256': sha(FROZEN / FROZEN_TEST),
            'old_pin_digest': pin_digest(state['pins']),
            'old_pin_count': len(state['pins']),
            'unchanged_pins_verified': len(state['pins']) - 6,
            'inventory_sha256': state['inventory_sha256'],
            'model_digest': state.get('model_digest'),
            'baseline_nodes_sha256': completion_campaign.digest(state['nodes']),
            'baseline_summary': state['summary'],
            'baseline_fast_metrics': state.get('fast_metrics'),
            'database_quick_check': 'ok',
            'payload_manifest_sha256': sha(HERE / 'manifest.json'),
        }
        (HERE / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({'commit': manifest['source_commit'], 'changed': len(changed),
                          'old_pin_count': len(state['pins'])}, indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
