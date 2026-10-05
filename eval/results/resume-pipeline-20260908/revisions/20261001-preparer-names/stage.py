"""Stage the 20261001-preparer-names amendment against the paused, drained checkpoint.

Owner, 2026-10-01: item 2 of the ranked amendment list ("2 seems pretty good"). Evidence: README.md;
eval/results/hidden-object-20260930/RESULTS.md (linking follow-up and batch), linking_probe.jsonl.

Every changed file comes from reviewed/ (build_reviewed.py): main's copy for the three files whose only drift from the
frozen copy is this amendment, and the frozen repair_queue.py plus one digest hunk.

  eval/prepare_integration.py  carries candidate-local brace typedefs, object-like #define aliases and plain
                               `const char NAME[N] = "literal";` objects into the destination TU
  eval/operand_repair.py       relocation_names.variants in the proposal list (literal_names filtered out)
  solver/relocation_names.py   field_names: struct field -> the target's separate global
  solver/repair_queue.py       operand-repair digest covers relocation_names.py (existing nodes revisited once)

`--dry` skips the pause-marker and in-flight checks and writes a manifest apply_amendment.py refuses.
"""
from __future__ import annotations

import argparse
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
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
STAGED = HERE / 'staged'
KIND = 'preparer-names'

CHANGED = ('eval/prepare_integration.py', 'eval/operand_repair.py', 'solver/relocation_names.py',
           'solver/repair_queue.py')
NEW: set = set()
# build_reviewed.out: frozen sha and reviewed sha, pinned at review time.
REVIEWED = {
    'eval/prepare_integration.py': ('6151e8da06442d02cf9329b4a33c1eb7677d93afd34e3263acc521977cf79450',
                                    '8c00573ae21e24466e72b0a0c1f4781f95e99a5d248ee22376b3ca6eb380c9e9'),
    'eval/operand_repair.py': ('229c7bd9198444f8f198eb47e483c81fa8fbf74b6288eb2fd4fbee50cc423f17',
                               '1003e6c656e15ba0f24ee31eaca59bc7c2c9f4fe6b8e512638bdd6955f571ccd'),
    'solver/relocation_names.py': ('6616db587019879d854b7ce37c0676f996a257d93880b8a29ab854021831e8c0',
                                   '8ec6353fb4498de8df9f0afb936e5f57cf954d997d7832800fa5ca5b05ee53ed'),
    'solver/repair_queue.py': ('b83e7ce347951ea474c49ed46e5b0413c508d2c1ec2196235512b529f666a73f',
                               'c39babd1b6f77589dcb7976858ea3c923a754c73788a90fad4593153ae3fc2ed'),
}
TESTS = ('tests/test_prepare_integration.py', 'tests/test_relocation_names.py', 'tests/test_operand_repair_campaign.py',
         'tests/test_integration_declarations.py')
# Test-only inputs copied into the test tree, never deployed.
TEST_FIXTURES: tuple = ()
FROZEN_TESTS = ('tests/test_repair_queue.py', 'tests/test_census_campaign.py', 'tests/test_completion_campaign.py',
                'tests/test_campaign_integration.py', 'tests/test_integration_gate.py',
                'tests/test_function_boundary.py', 'tests/test_function_boundary_v3.py')
NEW_DRIFT_IMPORTS: dict = {}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def imports(path: Path) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.ImportFrom) and node.module in {'solver', 'eval', 'miner'}:
            result.update(f'{node.module}/{alias.name}.py' for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(('solver.', 'eval.')):
            result.add(node.module.replace('.', '/') + '.py')
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry', action='store_true')
    args = parser.parse_args()
    if not args.dry and (not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists()):
        raise RuntimeError('both campaign pause markers must exist before staging')
    handles = [] if args.dry else [locked(CONTROL / 'resume-supervisor.lock'), locked(STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state, completion_campaign  # noqa: E402
        pointer_bytes = STATE.read_bytes()
        pointer = json.loads(pointer_bytes)
        state = campaign_state.read(STATE)
        inflight = bool(state.get('fast_inflight') or state.get('inflight'))
        if inflight and not args.dry:
            raise RuntimeError('campaign has work in flight')
        if state['config']['project'] != str(FROZEN):
            raise RuntimeError('wrong frozen project in campaign state')
        launch_path = CONTROL / 'launch.json'
        command = json.loads(launch_path.read_bytes())['command']
        if (command[command.index('--state') + 1] != str(STATE) or
                command[command.index('--project') + 1] != str(FROZEN)):
            raise RuntimeError('launch points at another campaign/project')
        if STAGED.exists():
            shutil.rmtree(STAGED)
        before_hashes, new_hashes = {}, {}
        for rel in CHANGED:
            target, out = FROZEN / rel, STAGED / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            old = sha(target) if target.is_file() else None
            if state['pins'].get(str(target)) != old:
                raise RuntimeError(f'old code pin differs: {rel}')
            if (rel in NEW) != (old is None):
                raise RuntimeError(f'new/existing status differs from review: {rel}')
            frozen_expected, reviewed_expected = REVIEWED[rel]
            if old != frozen_expected:
                raise RuntimeError(f'frozen {rel} changed since the reviewed build; rebuild reviewed/')
            reviewed = HERE / 'reviewed' / rel
            if sha(reviewed) != reviewed_expected:
                raise RuntimeError(f'reviewed {rel} changed since review')
            shutil.copy2(reviewed, out)
            ast.parse(out.read_text(encoding='utf-8'))
            before_hashes[rel], new_hashes[rel] = old, sha(out)
        drift = {}
        for owner in CHANGED:
            now = imports(STAGED / owner)
            before = imports(FROZEN / owner) if (FROZEN / owner).is_file() else set()
            for rel in sorted(now - set(CHANGED)):
                main_file, frozen_file = MAIN / rel, FROZEN / rel
                if not frozen_file.is_file():
                    raise RuntimeError(f'dependency missing from frozen tree: {owner} -> {rel}')
                if main_file.is_file() and (sha(main_file) == sha(frozen_file) or
                        main_file.read_bytes().replace(b'\r\n', b'\n') == frozen_file.read_bytes().replace(b'\r\n', b'\n')):
                    continue
                if rel in before:
                    continue
                reason = NEW_DRIFT_IMPORTS.get((owner, rel))
                if reason is None:
                    raise RuntimeError(f'unreviewed new import of a drifted module: {owner} -> {rel}')
                drift[f'{owner} -> {rel}'] = {'frozen_sha256': sha(frozen_file), 'reason': reason}
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
        if pointer_bytes != STATE.read_bytes():
            raise RuntimeError('checkpoint changed during stage preparation')
        for rel in TESTS + TEST_FIXTURES:
            target = STAGED / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MAIN / rel, target)
        manifest = {
            'kind': f'dry-{KIND}-stage' if args.dry else f'unapplied-{KIND}-stage',
            'dry': args.dry, 'inflight_at_stage': inflight,
            'source_commit': pointer['commit'],
            'source_pointer_sha256': hashlib.sha256(pointer_bytes).hexdigest(),
            'source_launch_sha256': sha(launch_path),
            'state_path': str(STATE), 'frozen_project': str(FROZEN),
            'changed': {rel: {'old_sha256': before_hashes[rel], 'new_sha256': new_hashes[rel]} for rel in CHANGED},
            'tests': {rel: sha(STAGED / rel) for rel in TESTS},
            'test_fixtures': {rel: sha(STAGED / rel) for rel in TEST_FIXTURES},
            'frozen_tests': list(FROZEN_TESTS),
            'new_drift_imports': drift,
            'unchanged_pins_verified': len(state['pins']) - len(CHANGED) + len(NEW),
            'inventory_sha256': state['inventory_sha256'],
            'model_digest': state['model_digest'],
            'baseline_exact': pointer['summary']['object_exact_or_integrated'],
            'baseline_nodes_sha256': completion_campaign.digest(state['nodes']),
            'database_quick_check': 'ok',
        }
        (HERE / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({k: manifest[k] for k in ('kind', 'source_commit', 'inflight_at_stage',
                                                    'unchanged_pins_verified', 'new_drift_imports')}
                         | {'changed': list(manifest['changed'])}, indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
