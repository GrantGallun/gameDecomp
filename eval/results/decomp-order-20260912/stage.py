"""Stage only layout/scheduler changes against the existing frozen worker."""
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LIVE = ROOT / 'eval/results/resume-pipeline-20260908/code'
STAGE = OUT / 'staged-code'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def definition(source, name):
    node = next(n for n in ast.parse(source).body if getattr(n, 'name', None) == name)
    return ''.join(source.splitlines(keepends=True)[node.lineno-1:node.end_lineno]) + '\n'


def main():
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'results'))
    relative = 'solver/repair_queue.py'
    current = (ROOT / relative).read_text()
    original = (LIVE / relative).read_text()
    prefix = original[:original.index('def project(')]
    prefix = prefix.replace('    priority: tuple\n', '    priority: tuple\n    unit: str | None = None\n    dependency_depth: int = 0\n')
    project = definition(current, 'project')
    investigation = """        if state['config'].get('scheduler') == 'investigation-v1':
            from solver.investigation import profile as investigation_profile
            profile = investigation_profile(node, state['config']['model_calls'], legacy_profiles)
        else:
            profile = next_profile(node, state['config']['model_calls'], legacy_profiles)
"""
    assert investigation in project
    project = project.replace(investigation, "        profile = next_profile(node, state['config']['model_calls'], legacy_profiles)\n")
    start = project.index("    if state['config'].get('scheduler') == 'investigation-v1':", project.index('    heap ='))
    end = project.index('    heapq.heapify(heap)', start)
    project = project[:start] + project[end:]
    (STAGE / relative).write_text(prefix + definition(current, 'remaining_by_unit') + '\n\n' +
        definition(current, 'dependency_depths') + '\n\n' + project)

    relative = 'eval/completion_campaign.py'
    current = (ROOT / relative).read_text()
    original = (LIVE / relative).read_text()
    original = original.replace('from solver import repair_queue\n', 'from solver import repair_queue\nfrom miner import units\n')
    helpers = definition(current, 'translation_units') + '\n\n' + definition(current, 'reference_units') + '\n\n'
    original = original.replace('def choose(state:', helpers + 'def choose(state:', 1)
    original = original.replace('                deps, _ = callgraph.edges(conn)\n',
        '                deps, _ = callgraph.edges(conn)\n                unit_index = translation_units(conn)\n')
    block_start = current.index('                index = {name: unit for name, unit in units.items()')
    block_end = current.index('        model_pin =', block_start)
    block = current[block_start:block_end].replace(' in units.items()', ' in unit_index.items()')
    original = original.replace("                state['dependency_graph'] = repair_queue.graph(deps, selected)\n",
        "                state['dependency_graph'] = repair_queue.graph(deps, selected)\n" + block, 1)
    (STAGE / relative).write_text(original)
    for relative in ['miner/units.py', 'tests/test_repair_queue.py', 'tests/test_unit_layout.py']:
        shutil.copy2(ROOT / relative, STAGE / relative)
    changed = {}
    for path in STAGE.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(STAGE).as_posix()
        prior = LIVE / relative
        if not prior.exists() or sha(prior) != sha(path):
            changed[relative] = {'old_sha256': sha(prior) if prior.exists() else None, 'new_sha256': sha(path)}
    assert set(changed) == {'miner/units.py', 'solver/repair_queue.py', 'eval/completion_campaign.py',
                            'tests/test_repair_queue.py', 'tests/test_unit_layout.py'}
    (OUT / 'staged-manifest.json').write_text(json.dumps(changed, indent=2) + '\n')
    # Existing fixture tests read run artifacts. They remain outside code pins.
    (STAGE / 'eval/results').symlink_to(ROOT / 'eval/results', target_is_directory=True)
    print(json.dumps(changed, indent=2))


if __name__ == '__main__':
    main()
