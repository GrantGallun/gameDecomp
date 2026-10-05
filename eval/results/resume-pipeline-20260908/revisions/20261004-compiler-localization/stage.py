"""Scoped active-header object-conflict amendment; preserves all retained nodes.
Use stage.py, verify_stage.py, then apply_amendment.py --apply after a drained pause.
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
KIND = 'compiler-localization'

CHANGED = ('solver/modelrepair.py', 'eval/agentrepair.py', 'eval/completion_campaign.py', 'solver/repair_queue.py', 'solver/compiler_localization.py', 'solver/line_map.py')
NEW = {'solver/line_map.py', 'solver/compiler_localization.py'}
# build_reviewed.out: frozen sha and reviewed sha, pinned at review time.
REVIEWED = {'solver/modelrepair.py': ('701b2f23fca11af1bfad913981b8ff045cd42755ce54837585906cf40cf65543', '2f4699ca4c3305f077e7a63967a64d8b95daaefa2cf018472e2a0ef29d12f666'), 'eval/agentrepair.py': ('2f406e13e74151cb19b89ee881f1debeb55d18a1deeaf02c5a7cd79c4aa0ab44', 'df312e77f9b1ece8a290c6741a577cdee13d5cbb89387698a1b71e1bf2bb5060'), 'eval/completion_campaign.py': ('90ae822bba91b070dd1a23712a392870e15d45e119dd7ad14d3100da01fa7e12', '7261d6bc9961394d959f760ae996569f4a0118f9ac89b04c83be1015247d2319'), 'solver/repair_queue.py': ('c39babd1b6f77589dcb7976858ea3c923a754c73788a90fad4593153ae3fc2ed', 'd67e8ddd138467b26e98ff86671c0ceaef22b0fb0474587c8b8d4f56933a79c9'), 'solver/compiler_localization.py': (None, '8d16eb4ac9213c1f0041bdcb0c24895a616e59b858eba890c87812e03b9dacd0'), 'solver/line_map.py': (None, 'e1f046f5ac3e8af6b5baafd5fd15f31b5ea8d807cac4116f0fc67bfda17e7884')}
TESTS = ('tests/test_compiler_localization.py', 'tests/test_line_map.py')
# Test-only inputs copied into the test tree, never deployed.
TEST_FIXTURES = ('tests/test_completion_campaign.py',)
FROZEN_TESTS = ('tests/test_modelrepair.py', 'tests/test_repair_queue.py', 'tests/test_completion_campaign.py', 'tests/test_campaign_fast.py')
NEW_DRIFT_IMPORTS = {('solver/compiler_localization.py', 'solver/source_attribution.py'): 'Frozen instructions_of/sha/parse_dump API verified by staged tests and compiler fire.', ('solver/compiler_localization.py', 'solver/regalloc_mutations.py'): 'Use only frozen lexer/type proposals; measured by actual candidate compiler.', ('solver/compiler_localization.py', 'solver/workspace.py'): 'Frozen score/repair_complete oracle retained; staged compiler proof logs private probes.', ('solver/compiler_localization.py', 'solver/code_shapes.py'): 'Existing masked balanced body API exercised on raw production source.', ('solver/compiler_localization.py', 'solver/edit_locality.py'): 'Only existing edited_lines spans are consumed, verified in staged tests.', ('solver/line_map.py', 'tools/context_closure.py'): 'Canonical teaching helpers are not called by the campaign adapter; import is deferred.'}


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
            shutil.copy2((HERE / 'reviewed-tests' / rel) if (HERE / 'reviewed-tests' / rel).is_file() else MAIN / rel, target)
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
