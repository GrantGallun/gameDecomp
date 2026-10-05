"""Stage keyed resolution for the campaign's register search (four files) against the paused checkpoint.

Evidence: eval/results/regalloc-keyed-20260927/RESULTS.md (40 functions, budget 200: 61% of evaluations
resolved by key, 0 key violations, better best gradient in 11/40, worse in 0; the preregistered
path-identity prediction failed only through the phase-1/phase-2 budget split, measured in phase_check.out).
Owner approval to enable: 2026-09-28.

Scope:
  solver/regalloc_search.py      main's file, pinned to the reviewed hash (frozen + key/rank_sites only)
  solver/ido_stages.py           new; main's file, pinned
  solver/compiler_experiment.py  new; main's file, pinned (ido_stages calls _direct_command/_compile_source)
  eval/agentrepair.py            the FROZEN file plus one reviewed hunk (main's copy carries unreviewed work)
`rank_sites` is not passed by the campaign; it stays off.

Read-only for the live campaign and frozen tree; writes only this revision directory. `--dry` skips the
pause-marker and in-flight checks (so the package can be tested while the run is not drained) and writes a
manifest that apply_amendment.py refuses.
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

CHANGED = ('eval/agentrepair.py', 'solver/regalloc_search.py', 'solver/ido_stages.py',
           'solver/compiler_experiment.py')
NEW = {'solver/ido_stages.py', 'solver/compiler_experiment.py'}
REVIEWED_MAIN = {   # main files copied verbatim; any later edit to them needs a new review
    'solver/regalloc_search.py': '67b186c08b96b52b576f7f20f2a6c1e6cfc398a8736d49e6ebee3ea6fbb82071',
    'solver/ido_stages.py': 'dbafb5ab8bd4da0d6e541c89f1a8da6488cf4aeb427205dc63969205b472e910',
    'solver/compiler_experiment.py': 'b756825c43b8d70fae48d4c3761b18353d87f4d07368551fbb230a556a24465d',
}
TESTS = ('tests/test_regalloc_search.py', 'tests/test_regalloc_attempt_logging.py', 'tests/test_ido_stages.py')
# The frozen tree's own copies, run unchanged against the overlay: they cover the callers.
FROZEN_TESTS = ('tests/test_regalloc_campaign.py', 'tests/test_enabling_roots_campaign.py',
                'tests/test_agentrepair.py', 'tests/test_campaign_fast.py',
                'tests/test_completion_campaign.py', 'tests/test_regalloc_mutations.py')
# A changed module newly importing a module whose main and frozen copies differ. Reviewed case by case.
NEW_DRIFT_IMPORTS = {
    ('solver/regalloc_search.py', 'solver/residual_sites.py'):
        'imported only inside rank_by_sites, reached only with rank_sites=True, which the campaign does not '
        'pass; the frozen copy defines source_map, edit_region and _overlap (the only names used)',
}
OLD_BLOCK = r"""    outcome = regalloc_search.search(function, source, compile_with_parent, target.read_text(),
                                     budget=budget, enable=True,
                                     compile_with_parent=compile_with_parent)
"""
NEW_BLOCK = r"""    from kb.attempts import record_model_proposal
    from solver import ido_stages

    def optimizer_key(candidate):
        return ido_stages.optimizer_key(repo, ws, function, candidate)

    def resolved(candidate, label, parent_source, same_as):
        # Never compiled, so no attempts row: the candidate is still logged, with the compile it reused.
        parent_hash = hashlib.sha256((parent_source if parent_source is not None else source).encode()).hexdigest()
        record_model_proposal(conn, run_id=run_id, parent_attempt_id=attempt_by_source.get(parent_hash),
                              prompt='', raw_response=candidate, status='duplicate', model='zero-model',
                              kind='optimizer-key:regalloc', hypothesis=label,
                              edits=[{'same_object_as_attempt': attempt_by_source.get(
                                  hashlib.sha256(same_as.encode()).hexdigest())}])

    # Keyed resolution (eval/results/regalloc-keyed-20260927/RESULTS.md): 61% of evaluations resolved, 0 key
    # violations, 73% more candidates per budget, better gradient in 11/40 and worse in 0.
    outcome = regalloc_search.search(function, source, compile_with_parent, target.read_text(),
                                     budget=budget, enable=True,
                                     compile_with_parent=compile_with_parent,
                                     key=optimizer_key, resolved=resolved)
"""


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


def staged_agentrepair() -> str:
    old = (FROZEN / 'eval/agentrepair.py').read_text(encoding='utf-8')
    if old.count(OLD_BLOCK) != 1:
        raise RuntimeError('frozen agentrepair.py no longer has exactly one reviewed call site')
    return old.replace(OLD_BLOCK, NEW_BLOCK)


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
                out.write_text(staged_agentrepair(), encoding='utf-8', newline='')
            else:
                if sha(MAIN / rel) != REVIEWED_MAIN[rel]:
                    raise RuntimeError(f'main file changed since review: {rel}')
                shutil.copy2(MAIN / rel, out)
            ast.parse(out.read_text(encoding='utf-8'))
            before_hashes[rel], new_hashes[rel] = old, sha(out)
        # Direct project imports of the staged code must be unchanged in the frozen tree, or reviewed above.
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
                    continue                    # pre-existing import of an already-drifted module
                reason = NEW_DRIFT_IMPORTS.get((owner, rel))
                if reason is None:
                    raise RuntimeError(f'unreviewed new import of a drifted module: {owner} -> {rel}')
                drift[f'{owner} -> {rel}'] = {'frozen_sha256': sha(frozen_file), 'reason': reason}
        for name in ('source_map', 'edit_region', '_overlap'):
            if f'def {name}(' not in (FROZEN / 'solver/residual_sites.py').read_text(encoding='utf-8'):
                raise RuntimeError(f'frozen residual_sites lacks {name}')
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
        for rel in TESTS:
            target = STAGED / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MAIN / rel, target)
        manifest = {
            'kind': 'dry-regalloc-keyed-stage' if args.dry else 'unapplied-regalloc-keyed-stage',
            'dry': args.dry, 'inflight_at_stage': inflight,
            'source_commit': pointer['commit'],
            'source_pointer_sha256': hashlib.sha256(pointer_bytes).hexdigest(),
            'source_launch_sha256': sha(launch_path),
            'state_path': str(STATE), 'frozen_project': str(FROZEN),
            'changed': {rel: {'old_sha256': before_hashes[rel], 'new_sha256': new_hashes[rel]}
                        for rel in CHANGED},
            'tests': {rel: sha(STAGED / rel) for rel in TESTS},
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
