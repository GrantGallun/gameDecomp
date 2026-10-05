"""Stage the site-edits amendment (four files) against the paused, drained checkpoint.

Owner approval: 2026-09-29 ("alright give it a shot" to wiring site_edits into the campaign as a repair
profile through a recorded amendment). Evidence: eval/results/site-edits-20260929/README.md (13 exact of 77
small near-miss functions in round 3, 0 errors; 0 of 13 larger argument-register functions, so only small
residuals are scheduled); register protocol census eval/results/register-protocol-20260929/.

Scope:
  eval/site_edit_repair.py       new, main, pinned: the bounded, logged campaign route
  solver/site_edits.py           new, main, pinned: localizer, typed edits, beam search
  solver/repair_queue.py         reviewed/ = frozen + site_edit_digest/site_edit_profile, band -1
  eval/completion_campaign.py    reviewed/ = frozen + the `site_edits` dispatch (6 lines)
The reviewed files come from build_reviewed.py; main's other work on those two files stays out.

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

CHANGED = ('eval/site_edit_repair.py', 'solver/site_edits.py', 'solver/repair_queue.py',
           'eval/completion_campaign.py')
NEW = {'eval/site_edit_repair.py', 'solver/site_edits.py'}
REVIEWED_MAIN = {
    'eval/site_edit_repair.py': 'eab6de0d7a41a50d86a831e0b363a42405bfa4af3e787f946a4b7fea52f9fa8e',
    'solver/site_edits.py': '5f065da7fd954dda6de563e70de09d0f49d6c4cb6dfe179c3576fcbbecf2b6ea',
}
REVIEWED_PATCHED = {
    'solver/repair_queue.py': {'frozen': 'bc166a1d2e00c8aa08558ed825aae44cd9fae3280a23d0fbbb1121cc1cc35618',
                               'reviewed': '9d2bfe5b874f2d600df7e08d34ebe7185e18272c1bc33e1ea70c41912a2d2e56'},
    'eval/completion_campaign.py': {'frozen': '614267cf5fa8b72655fe8b6a12dbc1c191c4f2e0256c10618dc4475a364b178c',
                                    'reviewed': '5ed86e00b60d9bb8f992949f6d8a0617e1a4be63dada117413b14ff103cc7088'},
}
TESTS = ('tests/test_site_edits.py', 'tests/test_site_edit_repair.py')
TEST_FIXTURES = ('tests/fixtures/site_edits_fires.json',)
FROZEN_TESTS = ('tests/test_repair_queue.py', 'tests/test_completion_campaign.py', 'tests/test_campaign_fast.py')
# Drifted modules the new owners import. Each was read: the frozen copy has what site_edits uses.
NEW_DRIFT_IMPORTS = {
    ('solver/site_edits.py', 'solver/c89.py'):
        'main adds strip_register_bindings (and calls it in one sanitizer); site_edits uses only _mask, '
        'identical in both',
    ('solver/site_edits.py', 'solver/residual_sites.py'):
        'main changes source_map to read packed attribution; site_edits uses only edit_region, identical',
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
            'kind': 'dry-site-edits-stage' if args.dry else 'unapplied-site-edits-stage',
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
