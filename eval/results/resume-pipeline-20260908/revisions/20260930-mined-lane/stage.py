"""Stage the mined-lane amendment (site edits shape + rule-miner lane) against the paused, drained checkpoint.

Owner approval: PENDING. Evidence: README.md, stage-test.json, fires_check.

Scope:
  9 new files (rule miner, rewrite library, term rewrite, three IDO-shape generators, residual classes,
  equivalences, mined_rules.json), 4 changed files copied from main, repair_queue.py = frozen + two hunks.

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

NEW = {'solver/rewrite_library.py', 'solver/rule_miner.py', 'solver/term_rewrite.py', 'solver/unaligned_copy.py',
       'solver/temp_copyback.py', 'solver/counted_loop.py', 'solver/residual_classes.py', 'patterns/equivalences.py',
       'patterns/mined_rules.json'}
# Whole-file copies of main (additions only over frozen; reviewed in README.md).
REVIEWED_MAIN = {
    'solver/site_edits.py': '6f0a54d5b17b6d73727da333485704beaf46756b27cf7933237657bc721f3d2c',
    'solver/residual_sites.py': '38cdb73a979e9e1b3a32dc7106b71cabf2ad675f8e757d23f34707cccfc76795',
    'solver/branch_shape.py': '75926d12e70988246a6fdac3807cb15a0860d7d820d467bcb5b8cc5a883ae61d',
    'eval/site_edit_repair.py': '764ccf9ed384c7eb36cf5c3202f36e72afa6c444b5d47f49a87705b94feb5865',
    'solver/rewrite_library.py': 'c9baa3616f27cac6e10f40878d066942641ed24e3fc364012e4aab03b61b655d',
    'solver/rule_miner.py': '070f6eb323deb9ecb10a9070bff9ddda8d8665f2cad211100b28e3d46dccf3d7',
    'solver/term_rewrite.py': '17e205eabe249e5eb185831fa556bebf849fab4cf8ba970b2a0ae2eecb00b559',
    'solver/unaligned_copy.py': 'ed077c5eb3daef542bc597fcf096b1cc0496a6340e7a617654ae72f22b1eb316',
    'solver/temp_copyback.py': '66f81998fc2c8110690e7fb4b445116553d45f34d3f6d877be40391573b1af15',
    'solver/counted_loop.py': '6f858afd732c5c5521a5d3cff22578577d336d6cd67a24c8b6c9c8e332c8d09b',
    'solver/residual_classes.py': '89320799d7b63aff73efb643d8d2fe3516c52e37b9c39f76e856db23a775ae42',
    'patterns/equivalences.py': '9edc97e9cd07c0659865eae498abd94bcfb42e4465eb29673748df9aff1b8ec7',
    'patterns/mined_rules.json': 'd27f95f44c2553017fa8110485730507de2148d1bafa551e167d1379b493aab5',
}
# Frozen + two hunks only (build_reviewed.py): main's repair_queue also has unrelated investigation-policy edits.
REVIEWED_PATCHED = {
    'solver/repair_queue.py': {'frozen': '9d2bfe5b874f2d600df7e08d34ebe7185e18272c1bc33e1ea70c41912a2d2e56',
                               'reviewed': '51562e076553da0da01d6bd742b084f1349d4c4bfd1738a4cb6475cba9577de5'},
    # frozen + signals.distances only (24 added lines); main's analyse() change is not deployed
    'solver/signals.py': {'frozen': 'd103a43db2017372d9a393d6df20bae6926a6a1a3fb16d7a592465f1387172d5',
                          'reviewed': '571ec91b427e3b4a3a51dccd45015ed6faeb193fb2bad0276d9f3225c3f34cfb'},
}
CHANGED = tuple(REVIEWED_MAIN) + tuple(REVIEWED_PATCHED)
TESTS = ('tests/test_rule_miner.py', 'tests/test_rewrite_library.py', 'tests/test_term_rewrite.py',
         'tests/test_residual_classes.py', 'tests/test_counted_loop.py', 'tests/test_temp_copyback.py',
         'tests/test_unaligned_copy.py', 'tests/test_equivalences.py', 'tests/test_site_edits.py',
         'tests/test_branch_shape.py', 'tests/test_site_edit_repair.py')
TEST_FIXTURES = ('tests/fixtures/site_edits_fires.json', 'tests/fixtures/unaligned_copy_osMotorStart.c',
                 'tests/fixtures/unaligned_copy_osMotorStart.diff', 'tests/fixtures/unaligned_copy_osContGetInitData.c',
                 'tests/fixtures/unaligned_copy_osContGetInitData.diff')
FROZEN_TESTS = ('tests/test_repair_queue.py', 'tests/test_residual_sites.py')
# Reviewed: every module a changed file newly imports that differs between main and frozen. Lazy imports of
# solver.branch_points / eval.repair_dataset in patterns/equivalences.py are CLI-only (campaign_rows, main); the live
# path reads only equivalences._TOKEN and _COMMENT.
# CLI-only lazy imports (campaign_rows / main in patterns/equivalences.py); the live path never reaches them.
CLI_ONLY_IMPORTS = {('patterns/equivalences.py', 'eval/repair_dataset.py'),
                    ('patterns/equivalences.py', 'solver/branch_points.py')}
C89_REASON = ('main adds strip_register_bindings (called only from to_c89); the files use only c89._mask, '
              'identical in both')
NEW_DRIFT_IMPORTS: dict = {
    ('solver/rewrite_library.py', 'solver/c89.py'): C89_REASON,
    ('solver/term_rewrite.py', 'solver/c89.py'): C89_REASON,
    ('solver/temp_copyback.py', 'solver/c89.py'): C89_REASON,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nsha(path: Path) -> str:
    """Hash with CRLF folded to LF: main's working tree is rewritten to CRLF by tooling (seen 14:25 on 9/30), which
    changes bytes and not content. Staged copies are written LF, like the frozen tree."""
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


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
                if nsha(MAIN / rel) != REVIEWED_MAIN[rel]:
                    raise RuntimeError(f'main file changed since review: {rel}')
                out.write_bytes((MAIN / rel).read_bytes().replace(b'\r\n', b'\n'))
            ast.parse(out.read_text(encoding='utf-8'))
            before_hashes[rel], new_hashes[rel] = old, sha(out)
        drift = {}
        for owner in CHANGED:
            now = imports(STAGED / owner)
            before = imports(FROZEN / owner) if (FROZEN / owner).is_file() else set()
            for rel in sorted(now - set(CHANGED)):
                if (owner, rel) in CLI_ONLY_IMPORTS:
                    continue
                main_file, frozen_file = MAIN / rel, FROZEN / rel
                if not frozen_file.is_file():
                    raise RuntimeError(f'dependency missing from frozen tree: {owner} -> {rel}')
                if main_file.is_file() and nsha(main_file) == nsha(frozen_file):
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
            'kind': 'dry-mined-lane-stage' if args.dry else 'unapplied-mined-lane-stage',
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
