"""Prepare, but never install, the scoped September 26 campaign amendment.

Run in WSL after main-tree tests pass. Re-running refreshes the staged files from
the current main tree. A mismatch in the frozen baseline or any unchanged pin
fails closed. This script never writes the campaign, launch, or frozen project.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[4]
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
BEFORE = MAIN / 'eval/results/delivery-20260926/before-main'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
STAGED = HERE / 'staged'

CHANGED = (
    'eval/completion_campaign.py',
    'eval/fast_campaign.py',
    'solver/repair_queue.py',
    'solver/regalloc_mutations.py',
    'solver/m2c_placeholders.py',
    'solver/binary_type_facts.py',
    'solver/binary_type_identity.py',
    'solver/binary_type_context.py',
    'solver/binary_type_draft.py',
)
TESTS = (
    'tests/test_binary_type_identity.py',
    'tests/test_binary_type_context.py',
    'tests/test_binary_type_draft.py',
    'tests/test_binary_type_campaign.py',
    'tests/test_campaign_fast.py',
    'tests/test_completion_campaign.py',
    'tests/test_regalloc_mutations.py',
    'tests/test_m2c_placeholders.py',
)
SHARED_DEPENDENCIES = (
    'solver/m2c_input.py', 'solver/m2c_byte_view.py',
    'solver/repair_context.py', 'miner/evidence.py', 'solver/c89.py',
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expect_equal(left: Path, right: Path, reason: str) -> None:
    if not left.is_file() or not right.is_file() or left.read_bytes() != right.read_bytes():
        raise RuntimeError(f'{reason}: {left} != {right}')


def local_web_only(main: str, frozen: str) -> bool:
    """Prove that the main mutation file adds exactly the two approved families."""
    start = main.index('PURE_DECL =')
    stop = main.index('WORD_COPY =', start)
    old = main[:start] + main[stop:]
    for entry in (
        '                (("pure_inline",), pure_local_inlines(source, function)),\n',
        '                (("operand_local",), operand_locals(source, function)),\n',
    ):
        if old.count(entry) != 1:
            raise RuntimeError(f'missing or duplicated local-web entry: {entry.strip()}')
        old = old.replace(entry, '')
    if old != frozen:
        raise RuntimeError('regalloc remaining diff: ' + ''.join(list(difflib.unified_diff(
            frozen.splitlines(True), old.splitlines(True)))[:35])[:2500])
    return True


def named_placeholder_only(main: str, frozen: str) -> bool:
    """Ignore comments; the only executable difference may be DECL_LINE regex."""
    current = ast.parse(main)
    old = ast.parse(frozen)
    def key(node):
        return isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'DECL_LINE' for target in node.targets)
    current_decl = [node for node in current.body if key(node)]
    old_decl = [node for node in old.body if key(node)]
    if len(current_decl) != 1 or len(old_decl) != 1:
        return False
    current.body[current.body.index(current_decl[0])] = old_decl[0]
    return ast.dump(current, include_attributes=False) == ast.dump(old, include_attributes=False)


def solver_imports(path: Path) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.ImportFrom) and node.module == 'solver':
            result.update(f'solver/{alias.name}.py' for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module == 'miner':
            result.update(f'miner/{alias.name}.py' for alias in node.names)
    return result


def binary_inputs(repo: Path) -> dict[str, Path]:
    # Import the MAIN API in a fresh interpreter so the frozen regular solver
    # package already loaded for checkpoint reading cannot shadow it.
    code = ('import json,sys; from pathlib import Path; '
            'from solver import binary_type_draft; '
            'print(json.dumps({k:str(v) for k,v in '
            'binary_type_draft.input_paths(Path(sys.argv[1])).items()}))')
    result = subprocess.run([sys.executable, '-c', code, str(repo)], cwd=MAIN,
                            env=dict(os.environ, PYTHONPATH=str(MAIN)),
                            capture_output=True, text=True, check=True)
    return {label: Path(path) for label, path in json.loads(result.stdout).items()}


def main() -> None:
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both campaign pause markers must exist before staging')
    sys.path.insert(0, str(FROZEN))
    from eval import campaign_state, completion_campaign  # noqa: E402
    pointer_before = STATE.read_bytes()
    pointer = json.loads(pointer_before)
    state = campaign_state.read(STATE)
    if pointer.get('commit') != 28385:
        raise RuntimeError(f'unexpected checkpoint commit {pointer.get("commit")}')
    if state.get('fast_inflight') or state.get('inflight'):
        raise RuntimeError('campaign has work in flight')
    if state['config']['project'] != str(FROZEN):
        raise RuntimeError('frozen project path changed')
    launch = json.loads((CONTROL / 'launch.json').read_bytes())
    command = launch['command']
    if command[command.index('--state') + 1] != str(STATE):
        raise RuntimeError('launch points at another checkpoint')

    # These were byte-identical when the task began. Main's current delta is
    # therefore the scoped delivery change; do not import earlier main edits.
    for rel in ('eval/completion_campaign.py', 'solver/repair_queue.py', 'eval/fast_campaign.py'):
        expect_equal(BEFORE / rel, FROZEN / rel, 'frozen baseline drift')
    main_regalloc = (MAIN / 'solver/regalloc_mutations.py').read_text()
    frozen_regalloc = (FROZEN / 'solver/regalloc_mutations.py').read_text()
    if not local_web_only(main_regalloc, frozen_regalloc):
        raise RuntimeError('regalloc main/frozen difference exceeds pure_inline and operand_local: ' +
                           ''.join(list(difflib.unified_diff(frozen_regalloc.splitlines(True),
                                                              main_regalloc.splitlines(True)))[:35])[:2500])
    expect_equal(BEFORE / 'solver/regalloc_mutations.py',
                 MAIN / 'solver/regalloc_mutations.py', 'main local-web source drift')
    if not named_placeholder_only((MAIN / 'solver/m2c_placeholders.py').read_text(),
                                  (FROZEN / 'solver/m2c_placeholders.py').read_text()):
        raise RuntimeError('m2c placeholder diff exceeds named-parameter DECL_LINE fix')

    # The new draft's existing helpers must come from the same bytes in both
    # projects; they are dependencies, not extra deployment payload.
    for rel in SHARED_DEPENDENCIES:
        expect_equal(MAIN / rel, FROZEN / rel, 'binary draft dependency drift')
    for rel in CHANGED:
        source = MAIN / rel
        if not source.is_file():
            raise RuntimeError(f'missing source {source}')
        for dependency in solver_imports(source):
            if not (FROZEN / dependency).is_file() and dependency not in CHANGED:
                raise RuntimeError(f'undeployed dependency {dependency} from {rel}')

    before_hashes = {}
    staged_hashes = {}
    for rel in CHANGED:
        target = FROZEN / rel
        pinned = state['pins'].get(str(target))
        observed = sha(target) if target.is_file() else None
        if pinned != observed:
            raise RuntimeError(f'changed file pin mismatch: {rel}: {pinned} != {observed}')
        if target.exists() and rel.startswith('solver/binary_type_'):
            raise RuntimeError(f'new binary module already exists frozen: {rel}')
        before_hashes[rel] = observed
        source = MAIN / rel
        ast.parse(source.read_text(encoding='utf-8'))
        staged_hashes[rel] = sha(source)
    changed_paths = {str(FROZEN / rel) for rel in CHANGED}
    mismatch = [path for path, expected in state['pins'].items()
                if path not in changed_paths and
                (sha(Path(path)) if Path(path).is_file() else None) != expected]
    if mismatch:
        raise RuntimeError(f'{len(mismatch)} unchanged frozen pins differ, e.g. {mismatch[:3]}')
    with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('campaign DB quick_check failed')
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    if completion_campaign.digest(inventory) != state['inventory_sha256']:
        raise RuntimeError('inventory pin changed')
    inputs = binary_inputs(Path(state['config']['repo']))
    additional = {}
    for label, path in inputs.items():
        path = path.resolve()
        if not path.is_file():
            raise RuntimeError(f'missing binary draft input {label}: {path}')
        digest = sha(path)
        previous = state['pins'].get(str(path))
        if previous is not None and previous != digest:
            raise RuntimeError(f'existing binary draft input pin changed: {label}')
        if previous is None:
            additional[str(path)] = {'label': label, 'sha256': digest}
    if not any(label.lower().endswith('elf') or 'elf' in label.lower()
               for label in (row['label'] for row in additional.values())):
        raise RuntimeError('binary draft ELF is not newly pinned')
    if pointer_before != STATE.read_bytes():
        raise RuntimeError('checkpoint changed during stage')

    for rel in CHANGED + TESTS:
        source = MAIN / rel
        if not source.is_file():
            raise RuntimeError(f'missing staged source/test {source}')
        target = STAGED / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    drift = [rel for rel in CHANGED if sha(STAGED / rel) != staged_hashes[rel]]
    if drift:
        raise RuntimeError(f'main code changed during staging: {drift}')
    manifest = {
        'kind': 'unapplied-campaign-delivery-stage', 'source_commit': pointer['commit'],
        'source_pointer_sha256': hashlib.sha256(pointer_before).hexdigest(),
        'source_launch_sha256': sha(CONTROL / 'launch.json'),
        'state_path': str(STATE), 'frozen_project': str(FROZEN),
        'changed': {rel: {'old_sha256': before_hashes[rel], 'new_sha256': staged_hashes[rel]}
                    for rel in CHANGED},
        'additional_input_pins': additional,
        'tests': {rel: sha(STAGED / rel) for rel in TESTS},
        'unchanged_pins_verified': len(state['pins']) - len([r for r in CHANGED if before_hashes[r]]),
        'inventory_sha256': state['inventory_sha256'], 'model_digest': state['model_digest'],
        'database_quick_check': 'ok',
        'baseline_verified': list(('eval/completion_campaign.py', 'solver/repair_queue.py',
                                   'eval/fast_campaign.py', 'solver/regalloc_mutations.py')),
        'shared_dependencies_verified': list(SHARED_DEPENDENCIES),
    }
    (HERE / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'commit': pointer['commit'], 'changed': list(manifest['changed']),
                      'unchanged_pins_verified': manifest['unchanged_pins_verified']}, indent=2))


if __name__ == '__main__':
    main()
