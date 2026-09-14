"""Stage incumbent-selection fix over frozen runtime without other upgrades."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / 'eval/results/resume-pipeline-20260908/code'
STAGE = OUT / 'staged-code'
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

if '--refresh' not in sys.argv:
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'results'))
    (STAGE / 'eval/results').symlink_to(ROOT / 'eval/results', target_is_directory=True)
manifest = {}
rel = 'solver/modelrepair.py'
source = (LIVE / rel).read_text()
before = '    best_state = result.frontier[0]\n'
after = '    best_state = initial[0]\n'
assert source.count(before) == 1
source = source.replace(before, after, 1)
before = '    for state in result.frontier:\n        if state_quality(state) > state_quality(best_state):\n'
after = '    for state in initial[1:]:\n        if state_quality(state) > state_quality(best_state):\n'
assert source.count(before) == 1
source = source.replace(before, after, 1)
compile(source, rel, 'exec')
(STAGE / rel).write_text(source)
manifest[rel] = {'old_sha256': sha(LIVE / rel), 'new_sha256': sha(STAGE / rel),
                 'scope': 'Select incumbent on measured ties; retain exploration frontier unchanged.'}
rel = 'tests/test_incumbent_selection.py'
shutil.copy2(ROOT / rel, STAGE / rel)
manifest[rel] = {'old_sha256': sha(LIVE / rel), 'new_sha256': sha(STAGE / rel)}
(OUT / 'staged-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
protocol = (ROOT / 'eval/results/inline-regions-20260912/deploy_protocol.py').read_text()
protocol = protocol.replace("REVISION='20260912-impact'", "REVISION='20260913-incumbent-selection'")
protocol = protocol.replace("AMENDMENT_KIND='impact-amendment'", "AMENDMENT_KIND='incumbent-selection-amendment'")
protocol = protocol.replace('User requested implementing inlining machinery for large functions in the ongoing campaign.',
                            'User requested fixing repeated zero-gain repairs in the ongoing campaign.')
protocol = protocol.replace('Binary repeated-region prompt hints and bounded source-local helper expansion only; no original-inlining claim.',
                            'Keep the freshly evaluated incumbent on measured ties; preserve real semantic improvements and exploration alternatives.')
(OUT / 'deploy.py').write_text(protocol)
print(json.dumps({'stage': str(STAGE), 'files': list(manifest)}))
