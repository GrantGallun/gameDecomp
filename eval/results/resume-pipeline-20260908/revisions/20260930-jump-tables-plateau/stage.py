"""Stage the jump-table certificate + plateau-search amendment against the paused, drained checkpoint.

Owner approval to prepare: 2026-09-30 ("please do", in reply to the proposal to deploy the jump-table certificate and
the plateau search). Evidence: README.md; eval/results/loop-shape-20260930/RESULTS.md (jtbl_integration_dry.jsonl:
28 of 36 newly function-exact candidates rom_exact when integrated alone; plateau_near.jsonl: 5 exact of 219).

Scope:
  solver/function_boundary.py   main == reviewed: schema 3 admits jump tables verified against the ROM
  solver/repair_queue.py        reviewed/ = frozen + plateau_digest/plateau_profile and its scheduling
  eval/completion_campaign.py   reviewed/ = frozen + the plateau dispatch branch
  solver/plateau_search.py      new (main)
  eval/plateau_repair.py        new (main)
  solver/nearmiss_llm.py        new (main); opt-in, never called by the campaign (plateau_repair passes no model)

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

CHANGED = ('solver/function_boundary.py', 'solver/repair_queue.py', 'eval/completion_campaign.py',
           'solver/plateau_search.py', 'eval/plateau_repair.py', 'solver/nearmiss_llm.py')
NEW = {'solver/plateau_search.py', 'eval/plateau_repair.py', 'solver/nearmiss_llm.py'}
REVIEWED_MAIN = {
    'solver/plateau_search.py': 'bf536c5589d7394428015065655897a209ff3e2698b17e291ad2ff208a861c06',
    'eval/plateau_repair.py': '43f8121b779b9c8dead6c3340ab7e2095238ceb3182ad87111a2306cb144d99d',
    'solver/nearmiss_llm.py': '43421a86b9635e468eb4d4d98def6c467225dd8b7763f9716d6f27c67e3cf4ae',
}
REVIEWED_PATCHED = {
    'solver/function_boundary.py': {'frozen': '3fe054bd7d05d0d3d5b0768b1aaeaa858d704e56cd9cc34c09cc51c6ff9bedf8',
                                    'reviewed': '729f9617b7eb39a511be3ddb981f41ee643b655aaf66839b662e13ce9189e036'},
    'solver/repair_queue.py': {'frozen': '51562e076553da0da01d6bd742b084f1349d4c4bfd1738a4cb6475cba9577de5',
                               'reviewed': 'b3a3274e06fee5c6c05be6428aadd5c2074bc1d025714867ea15ad2a6dd45ab9'},
    'eval/completion_campaign.py': {'frozen': '5ed86e00b60d9bb8f992949f6d8a0617e1a4be63dada117413b14ff103cc7088',
                                    'reviewed': '90ae822bba91b070dd1a23712a392870e15d45e119dd7ad14d3100da01fa7e12'},
}
TESTS = ('tests/test_function_boundary_jump_tables.py', 'tests/test_plateau_repair.py', 'tests/test_plateau_search.py',
         'tests/test_site_edit_repair.py')
# Test-only support, copied into the test tree and never deployed: the jump-table fixture source and the probe
# helper the jump-table test compiles it with.
TEST_FIXTURES = ('tests/fixtures/jtbl_updateRaceSplitscreenSelectOption1Frame.c', 'eval/probe_source.py')
FROZEN_TESTS = ('tests/test_function_boundary.py', 'tests/test_function_boundary_v3.py',
                'tests/test_repair_queue.py', 'tests/test_completion_campaign.py', 'tests/test_campaign_integration.py',
                'tests/test_prepare_integration.py')
NEW_DRIFT_IMPORTS = {
    ('eval/plateau_repair.py', 'solver/residual.py'):
        'residual.build(...).to_dict() exactly as the frozen eval/site_edit_repair.py already calls it',
    ('solver/nearmiss_llm.py', 'solver/llm.py'):
        'only reached when plateau_search.search gets llm_repo; eval/plateau_repair.py never passes it',
    # Main workspace.py gained an object_discrepancy diagnostic at 16:37 (another session). The new owners use
    # only bootstrap/score/repair_complete/target_asm, present unchanged in the frozen copy, exactly as the frozen
    # eval/site_edit_repair.py uses them.
    ('solver/plateau_search.py', 'solver/workspace.py'): 'uses workspace.repair_complete only (frozen copy has it)',
    ('eval/plateau_repair.py', 'solver/workspace.py'):
        'bootstrap/score/repair_complete/target_asm, as frozen eval/site_edit_repair.py',
    ('solver/nearmiss_llm.py', 'solver/workspace.py'):
        'assert_uncontaminated only, and only on the opt-in model path the campaign never takes',
}

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
            if rel in REVIEWED_PATCHED:
                if old != REVIEWED_PATCHED[rel]['frozen']:
                    raise RuntimeError(f'frozen {rel} changed since the reviewed build')
                reviewed = HERE / 'reviewed' / rel
                if sha(reviewed) != REVIEWED_PATCHED[rel]['reviewed']:
                    raise RuntimeError(f'reviewed {rel} changed since review')
                shutil.copy2(reviewed, out)
            else:
                if sha(MAIN / rel) != REVIEWED_MAIN[rel]:
                    raise RuntimeError(f'main file changed since review: {rel}')
                shutil.copy2(MAIN / rel, out)
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
                # Same content with CRLF vs LF line endings is not drift (the Windows-side checkout writes CRLF).
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
            'kind': 'dry-jump-tables-plateau-stage' if args.dry else 'unapplied-jump-tables-plateau-stage',
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
