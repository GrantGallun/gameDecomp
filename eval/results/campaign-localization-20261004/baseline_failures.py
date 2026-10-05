"""Reproduce broad-suite failures with only this feature's wiring removed."""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
WORK = Path('/home/grant/decomp/experiments/campaign-localization-20261004/baseline-overlay')
WORK.mkdir(parents=True, exist_ok=True)
for package in ('solver', 'eval'):
    folder = WORK / package
    folder.mkdir(exist_ok=True)
    (folder / '__init__.py').write_text('__path__ = ' + repr([str(folder), str(ROOT / package)]) + '\n')


def remove(text, token, replacement=''):
    if text.count(token) != 1:
        raise RuntimeError('baseline reverse anchor differs: ' + repr(token))
    return text.replace(token, replacement)


for rel in ('solver/modelrepair.py', 'solver/repair_queue.py', 'eval/agentrepair.py', 'eval/completion_campaign.py'):
    text = (ROOT / rel).read_text()
    if rel.endswith('modelrepair.py'):
        a = text.index('            localization_packet = None\n')
        b = text.index('            pending_completion = ""\n', a)
        text = text[:a] + text[b:]
        text = remove(text, '    localization_cache = {}\n')
        a = text.index('    if localization:\n')
        b = text.index('    from solver import edit_slots', a)
        text = text[:a] + text[b:]
        text = remove(text, ', localization=None')
        text = remove(text, ',\n           compiler_localization: bool = False')
        text = remove(text, '        "compiler_localization": compiler_localization,\n')
        text = remove(text, ', localization=localization_packet')
    elif rel.endswith('agentrepair.py'):
        text = remove(text, ', compiler_localization: bool = False')
        text = remove(text, '        "compiler_localization": compiler_localization,\n')
        text = remove(text, ',\n        compiler_localization=compiler_localization')
    elif rel.endswith('completion_campaign.py'):
        a = text.index('    {"name": "localized_patch"')
        b = text.index('\n)', a)
        text = text[:a] + text[b:]
        text = remove(text, "        if profile.get('compiler_localization') and node.get('residual', {}).get('compiled') is not True:\n            continue\n")
        text = remove(text, "        compiler_localization=profile.get('compiler_localization', False),\n")
    else:
        text = remove(text, "    profiles = [p for p in profiles if not p.get('compiler_localization') or (\n        phase == Lane.BYTE and (node.get('residual') or {}).get('compiled') is True)]\n")
    (WORK / rel).write_text(text)
log = (ROOT / '.tmp-campaign-localization-project-suite.log').read_text()
nodes = re.findall(r'^(?:FAILED|ERROR) (tests/\S+)', log, re.M)
sys.path.insert(0, str(WORK))
sys.path.insert(1, str(ROOT))
import solver, eval
import pytest
os.chdir(ROOT)
result = pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp', str(WORK / 'tmp'), *nodes])
(Path(__file__).parent / 'baseline-run.json').write_text(json.dumps({'nodes': nodes, 'returncode': result}, indent=2))
raise SystemExit(result)
