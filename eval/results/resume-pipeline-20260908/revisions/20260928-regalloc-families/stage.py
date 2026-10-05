"""Stage the register-search families amendment (five files) against the paused, drained checkpoint.

Owner approval: 2026-09-28 ("wire into the main campaign"). Evidence:
  - scalar coalescing: eval/results/coalescing-sweep-20260928 (2 exact on a one-step sweep of 831 pending
    functions), eval/results/coalescing-factorial-20260928 (2/2 through the shared search);
  - scoped_field: eval/results/scoped-field-20260928 (0/6 existing vs 2/6 with the family, 0 losses);
  - reuse hardening: docs/claude-review-followup-20260928.md §2 (certificate rechecks, seeded 2% audit,
    restart without reuse on a conclusive key violation), tested in tests/test_regalloc_search.py.
Astra's selection modes are in regalloc_search.py but OFF (default `selection='gradient'`); rank_sites and
the model diagnostic packet (solver/repair_diagnostic.py, modelrepair) are NOT in this amendment.

Scope:
  eval/agentrepair.py            reviewed/eval/agentrepair.py = frozen file with only `_regalloc_search`
                                 replaced by main's (agentrepair.diff); main's other work stays out
  solver/regalloc_search.py      main, pinned
  solver/regalloc_mutations.py   main, pinned (frozen + the two families only)
  solver/scalar_coalesce.py      new, pinned
  solver/scoped_field.py         new, pinned

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

CHANGED = ('eval/agentrepair.py', 'solver/regalloc_search.py', 'solver/regalloc_mutations.py',
           'solver/scalar_coalesce.py', 'solver/scoped_field.py')
NEW = {'solver/scalar_coalesce.py', 'solver/scoped_field.py'}
REVIEWED_MAIN = {
    'solver/regalloc_search.py': '3293b44a8a147ded7a738ace16f7ea86defccd3cef495fcd0bdbfcb8bd4be5de',
    'solver/regalloc_mutations.py': 'fe6433e8662ad8131af4fe53472136444dccfeabe4538d4fc1ed8e7849853035',
    'solver/scalar_coalesce.py': 'd957907ada2f15ba750a9bc0c97c6a218148850be0cb3e07d4af3c492ecd15d8',
    'solver/scoped_field.py': '5c3c5bebfe221c6c3cf1c5cfa73018c6638896ffc400f64a40d5f8807d90f64f',
}
REVIEWED_AGENTREPAIR = {'frozen': 'd0c05f200f46eee37e5a12063c44c00b5400a89a6c7d2ba6761b8ad46584dec1',
                        'reviewed': '2f406e13e74151cb19b89ee881f1debeb55d18a1deeaf02c5a7cd79c4aa0ab44'}
TESTS = ('tests/test_regalloc_search.py', 'tests/test_regalloc_attempt_logging.py',
         'tests/test_regalloc_mutations.py', 'tests/test_scoped_field.py', 'tests/test_scalar_coalesce.py',
         'tests/test_regalloc_evolvability.py')
# Test-only scaffolding copied into the staged TEST tree (never installed in the campaign; apply installs
# CHANGED only): the motivating fixture, and the research harness that two test modules import at top level.
TEST_FIXTURES = ('tests/fixtures/scoped_field_aerial.c',
                 *sorted(p.relative_to(MAIN).as_posix() for p in (MAIN / 'eval/research_suite').glob('*.py')))
FROZEN_TESTS = ('tests/test_regalloc_campaign.py', 'tests/test_enabling_roots_campaign.py',
                'tests/test_agentrepair.py', 'tests/test_campaign_fast.py', 'tests/test_completion_campaign.py')
NEW_DRIFT_IMPORTS = {}          # every drifted dependency is imported by the frozen owner already


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
            if rel == 'eval/agentrepair.py':
                if old != REVIEWED_AGENTREPAIR['frozen']:
                    raise RuntimeError('frozen agentrepair.py changed since the reviewed build')
                reviewed = HERE / 'reviewed' / rel
                if sha(reviewed) != REVIEWED_AGENTREPAIR['reviewed']:
                    raise RuntimeError('reviewed agentrepair.py changed since review')
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
                if main_file.is_file() and sha(main_file) == sha(frozen_file):
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
            'kind': 'dry-regalloc-families-stage' if args.dry else 'unapplied-regalloc-families-stage',
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
