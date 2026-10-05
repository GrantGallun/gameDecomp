"""Stage exactly seven operand-route files against the current paused checkpoint.

This is read-only for the live campaign and frozen tree. Run in WSL only after
the controller has drained and both pause markers exist. Staged files and the
manifest are written solely in this revision directory.
"""
from __future__ import annotations

import ast
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[4]
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
BEFORE = MAIN / 'eval/results/frontier-run-20260926/before-fix'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
STAGED = HERE / 'staged'

CHANGED = (
    'eval/agentrepair.py', 'eval/completion_campaign.py', 'eval/operand_repair.py',
    'solver/regalloc_search.py', 'solver/regalloc_mutations.py',
    'solver/local_web_merge.py', 'solver/repair_queue.py',
)
NEW = {'eval/operand_repair.py', 'solver/local_web_merge.py'}
TESTS = (
    'tests/test_operand_repair_campaign.py', 'tests/test_operand_profile.py',
    'tests/test_local_web_merge.py', 'tests/test_regalloc_attempt_logging.py',
    'tests/test_regalloc_campaign.py', 'tests/test_regalloc_search.py',
    'tests/test_regalloc_mutations.py', 'tests/test_repair_queue.py',
    'tests/test_completion_campaign.py', 'tests/test_campaign_fast.py',
    'tests/test_agentrepair.py', 'tests/test_fresh_compile.py',
    'tests/test_binary_type_campaign.py', 'tests/test_binary_type_draft.py',
)
# Existing differences predate this route. Their imported names must remain
# identical to the before-fix owner source, and the copied frozen overlay must
# pass the full staged test list. They are never copied from main.
COMPATIBLE_OLD_DRIFT = {
    'solver/compile_recovery.py', 'solver/frontend_fixits.py',
    'solver/llm.py', 'solver/modelrepair.py',
}
MAP_LABEL = 'operand-map:build/snowboardkids.map'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def imports(path: Path) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.ImportFrom) and node.module in {'solver', 'eval', 'miner'}:
            result.update(f'{node.module}/{alias.name}.py' for alias in node.names)
        elif isinstance(node, ast.Import):
            result.update(name.name.replace('.', '/') + '.py' for name in node.names
                          if name.name.startswith(('solver.', 'eval.', 'miner.')))
    return result


def locked(path: Path):
    handle = path.open('a+b')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f'lock held: {path}') from exc
    return handle


def main() -> None:
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both campaign pause markers must exist before staging')
    handles = [locked(CONTROL / 'resume-supervisor.lock'), locked(STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state, completion_campaign  # noqa: E402
        pointer_bytes = STATE.read_bytes()
        pointer = json.loads(pointer_bytes)
        state = campaign_state.read(STATE)
        if state.get('fast_inflight') or state.get('inflight'):
            raise RuntimeError('campaign has work in flight')
        if state['config']['project'] != str(FROZEN):
            raise RuntimeError('wrong frozen project in campaign state')
        launch_path = CONTROL / 'launch.json'
        launch = json.loads(launch_path.read_bytes())
        command = launch['command']
        if (command[command.index('--state') + 1] != str(STATE) or
                command[command.index('--project') + 1] != str(FROZEN)):
            raise RuntimeError('launch points at another campaign/project')
        before_hashes, new_hashes = {}, {}
        for rel in CHANGED:
            source, target = MAIN / rel, FROZEN / rel
            if not source.is_file():
                raise RuntimeError(f'missing main source: {rel}')
            ast.parse(source.read_text(encoding='utf-8'))
            old = sha(target) if target.is_file() else None
            if state['pins'].get(str(target)) != old:
                raise RuntimeError(f'old code pin differs: {rel}')
            if rel in NEW:
                if target.exists() or (BEFORE / rel).exists():
                    raise RuntimeError(f'new module already existed before this change: {rel}')
            elif not (BEFORE / rel).is_file() or sha(BEFORE / rel) != old:
                raise RuntimeError(f'before-fix image differs from frozen: {rel}')
            before_hashes[rel], new_hashes[rel] = old, sha(source)
        direct = set().union(*(imports(MAIN / rel) for rel in CHANGED))
        dependency_drift = {}
        for rel in sorted(direct - set(CHANGED)):
            main_file, frozen_file = MAIN / rel, FROZEN / rel
            if not main_file.is_file() or not frozen_file.is_file():
                raise RuntimeError(f'direct dependency missing main/frozen: {rel}')
            if sha(main_file) == sha(frozen_file):
                continue
            if rel not in COMPATIBLE_OLD_DRIFT:
                raise RuntimeError(f'unreviewed direct dependency drift: {rel}')
            for owner in CHANGED:
                if owner in NEW:
                    if rel in imports(MAIN / owner):
                        raise RuntimeError(f'new module imports old-drift dependency: {owner} -> {rel}')
                elif (rel in imports(MAIN / owner)) != (rel in imports(BEFORE / owner)):
                    raise RuntimeError(f'changed import of old-drift dependency: {owner} -> {rel}')
            dependency_drift[rel] = {'main_sha256': sha(main_file),
                                     'frozen_sha256': sha(frozen_file),
                                     'reason': 'pre-existing main/frozen drift; unchanged imports; tested actual frozen overlay'}
        if set(dependency_drift) != COMPATIBLE_OLD_DRIFT:
            raise RuntimeError(f'known dependency drift set changed: {sorted(dependency_drift)}')
        changed_paths = {str(FROZEN / rel) for rel in CHANGED}
        mismatch = [p for p, expected in state['pins'].items() if p not in changed_paths and
                    (sha(Path(p)) if Path(p).is_file() else None) != expected]
        if mismatch:
            raise RuntimeError(f'{len(mismatch)} unchanged pins differ: {mismatch[:3]}')
        with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise RuntimeError('campaign database integrity check failed')
            inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
        if completion_campaign.digest(inventory) != state['inventory_sha256']:
            raise RuntimeError('function inventory changed')
        repo = Path(state['config']['repo'])
        map_path = (repo / 'build/snowboardkids.map').resolve()
        if not map_path.is_file():
            raise RuntimeError(f'operand map input missing: {map_path}')
        map_digest = sha(map_path)
        prior_map = state['pins'].get(str(map_path))
        if prior_map is not None:
            raise RuntimeError('operand map already pinned; review amendment scope again')
        additional = {str(map_path): {'label': MAP_LABEL, 'sha256': map_digest}}
        if pointer_bytes != STATE.read_bytes():
            raise RuntimeError('checkpoint changed during stage preparation')
        for rel in CHANGED + TESTS:
            source = MAIN / rel
            if not source.is_file():
                raise RuntimeError(f'missing stage file: {rel}')
            target = STAGED / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if any(sha(STAGED / rel) != new_hashes[rel] for rel in CHANGED):
            raise RuntimeError('main code changed while staging')
        manifest = {
            'kind': 'unapplied-operand-delivery-stage',
            'source_commit': pointer['commit'],
            'source_pointer_sha256': hashlib.sha256(pointer_bytes).hexdigest(),
            'source_launch_sha256': sha(launch_path),
            'state_path': str(STATE), 'frozen_project': str(FROZEN),
            'changed': {rel: {'old_sha256': before_hashes[rel], 'new_sha256': new_hashes[rel]}
                        for rel in CHANGED},
            'additional_input_pins': additional,
            'tests': {rel: sha(STAGED / rel) for rel in TESTS},
            'dependency_drift_compatibility': dependency_drift,
            'unchanged_pins_verified': len(state['pins']) - len(CHANGED) + len(NEW),
            'inventory_sha256': state['inventory_sha256'],
            'model_digest': state['model_digest'],
            'baseline_exact': pointer['summary']['object_exact_or_integrated'],
            'baseline_nodes_sha256': completion_campaign.digest(state['nodes']),
            'database_quick_check': 'ok',
        }
        (HERE / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({'commit': pointer['commit'], 'changed': list(manifest['changed']),
                          'additional_input_pins': additional,
                          'unchanged_pins_verified': manifest['unchanged_pins_verified']}, indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
