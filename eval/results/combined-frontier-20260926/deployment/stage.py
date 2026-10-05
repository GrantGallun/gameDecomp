"""Freeze the reviewed branch-defaults payload at a drained campaign boundary.

Run under WSL. This writes only this deployment directory; it never amends the
frozen project or campaign. The checkpoint and launch identities are captured
at staging time and must still match when apply.py is run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
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

CHANGED = ('solver/branch_defaults.py', 'solver/regalloc_mutations.py',
           'solver/repair_queue.py')
NEW = {'solver/branch_defaults.py'}
TESTS = ('tests/test_branch_defaults.py', 'tests/test_branch_defaults_stream.py',
         'tests/test_regalloc_mutations.py', 'tests/test_regalloc_search.py',
         'tests/test_repair_queue.py', 'tests/test_campaign_fast.py',
         'tests/test_completion_campaign.py')
FIXTURES = ('tests/fixtures/branch_defaults/baseline.c',
            'tests/fixtures/branch_defaults/defaults_in_arms.c')
EXPECTED_OLD_PIN_DIGEST = 'af461f0352b89984c2b64cf27ef3e4ca2004b8cca681b71e592f1b5262165302'
EXPECTED_OLD_PIN_COUNT = 3314
EXPECTED_COMMIT = 30123
EXPECTED_OBJECT_EXACT = 1003
EXPECTED_FUNCTION_EXACT_PENDING = 24


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def boundary(campaign_state, completion_campaign) -> tuple[bytes, bytes, dict, dict]:
    if not (CONTROL / 'service.pause').is_file() or not (NATIVE / 'service.pause').is_file():
        raise RuntimeError('both durable pause markers required')
    pointer_bytes, launch_bytes = STATE.read_bytes(), LAUNCH.read_bytes()
    pointer, launch = json.loads(pointer_bytes), json.loads(launch_bytes)
    state = campaign_state.read(STATE)
    if pointer.get('kind') != 'campaign-checkpoint-index-v1' or pointer.get('commit') != EXPECTED_COMMIT:
        raise RuntimeError('source checkpoint changed')
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('campaign has in-flight work')
    if (state['summary']['object_exact_or_integrated'] != EXPECTED_OBJECT_EXACT or
            state['summary']['function_exact_pending_integration'] != EXPECTED_FUNCTION_EXACT_PENDING):
        raise RuntimeError('match baseline differs from reviewed boundary')
    if state['config']['project'] != str(FROZEN):
        raise RuntimeError('wrong frozen project in checkpoint')
    command = launch['command']
    if (command[command.index('--state') + 1] != str(STATE) or
            command[command.index('--project') + 1] != str(FROZEN)):
        raise RuntimeError('launch points at another campaign')
    if len(state['pins']) != EXPECTED_OLD_PIN_COUNT or pin_digest(state['pins']) != EXPECTED_OLD_PIN_DIGEST:
        raise RuntimeError('old pin set differs from reviewed boundary')
    for path, expected in state['pins'].items():
        p = Path(path)
        if not p.is_file() or sha(p) != expected:
            raise RuntimeError(f'old pinned file differs: {path}')
    if str(FROZEN / 'solver/branch_defaults.py') in state['pins']:
        raise RuntimeError('new module already pinned')
    with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('campaign database integrity check failed')
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    if completion_campaign.digest(inventory) != state['inventory_sha256']:
        raise RuntimeError('function inventory changed')
    if STATE.read_bytes() != pointer_bytes or LAUNCH.read_bytes() != launch_bytes:
        raise RuntimeError('checkpoint or launch changed while inspecting boundary')
    return pointer_bytes, launch_bytes, state, launch


def main() -> None:
    handles = [lock(CONTROL / 'resume-supervisor.lock'), lock(STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state, completion_campaign
        pointer_bytes, launch_bytes, state, launch = boundary(campaign_state, completion_campaign)
        changed = {}
        for rel in CHANGED:
            source, target = MAIN / rel, FROZEN / rel
            if not source.is_file():
                raise RuntimeError(f'missing reviewed source: {rel}')
            old = sha(target) if target.is_file() else None
            if rel in NEW:
                if old is not None or str(target) in launch.get('code_hashes', {}):
                    raise RuntimeError(f'new module already exists: {rel}')
            elif state['pins'].get(str(target)) != old:
                raise RuntimeError(f'old code pin differs: {rel}')
            old_launch = launch.get('code_hashes', {}).get(str(target))
            if rel not in NEW and not isinstance(old_launch, str):
                raise RuntimeError(f'missing old launch code entry: {rel}')
            changed[rel] = {'old_sha256': old, 'new_sha256': sha(source),
                            'old_launch_sha256': old_launch}
        for rel in CHANGED + TESTS + FIXTURES:
            source, target = MAIN / rel, STAGED / rel
            if not source.is_file():
                raise RuntimeError(f'missing staged payload: {rel}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for rel, row in changed.items():
            if sha(STAGED / rel) != row['new_sha256'] or sha(MAIN / rel) != row['new_sha256']:
                raise RuntimeError(f'source moved during staging: {rel}')
        manifest = {
            'kind': 'unapplied-combined-frontier-branch-defaults',
            'source_commit': json.loads(pointer_bytes)['commit'],
            'source_pointer_sha256': digest_bytes(pointer_bytes),
            'source_launch_sha256': digest_bytes(launch_bytes),
            'state_path': str(STATE), 'frozen_project': str(FROZEN),
            'changed': changed,
            'tests': {rel: sha(STAGED / rel) for rel in TESTS},
            'test_only_fixtures': {rel: sha(STAGED / rel) for rel in FIXTURES},
            'old_pin_digest': pin_digest(state['pins']),
            'old_pin_count': len(state['pins']),
            'unchanged_pins_verified': len(state['pins']) - len(CHANGED) + len(NEW),
            'inventory_sha256': state['inventory_sha256'],
            'model_digest': state.get('model_digest'),
            'baseline_nodes_sha256': completion_campaign.digest(state['nodes']),
            'baseline_summary': state['summary'],
            'baseline_fast_metrics': state.get('fast_metrics'),
            'database_quick_check': 'ok',
        }
        (HERE / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({'commit': manifest['source_commit'], 'changed': changed,
                          'old_pin_count': manifest['old_pin_count']}, indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
