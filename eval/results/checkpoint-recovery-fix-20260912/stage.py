"""Stage only checkpoint recovery changes over the active frozen runtime."""
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
for rel in ('eval/campaign_state.py', 'eval/campaign_service.py',
            'tests/test_campaign_state_recovery.py'):
    shutil.copy2(ROOT / rel, STAGE / rel)
    manifest[rel] = {'old_sha256': sha(LIVE / rel), 'new_sha256': sha(STAGE / rel)}

# Controller differences outside the startup read remain frozen.
rel = 'eval/fast_campaign.py'
before = '        state = campaign_state.read(state_path)\n'
after = '        state = campaign_state.read_for_resume(state_path)\n'
source = (LIVE / rel).read_text()
assert source.count(before) == 1
assert after in (ROOT / rel).read_text()
(STAGE / rel).write_text(source.replace(before, after, 1))
manifest[rel] = {'old_sha256': sha(LIVE / rel), 'new_sha256': sha(STAGE / rel)}
(OUT / 'staged-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

protocol = (ROOT / 'eval/results/inline-regions-20260912/deploy_protocol.py').read_text()
protocol = protocol.replace("REVISION='20260912-impact'", "REVISION='20260912-checkpoint-recovery'")
protocol = protocol.replace("AMENDMENT_KIND='impact-amendment'", "AMENDMENT_KIND='checkpoint-recovery-amendment'")
protocol = protocol.replace('User requested implementing inlining machinery for large functions in the ongoing campaign.',
                            'User reported broken ROM verification; recover the interrupted campaign and fix its checkpoint restart failure.')
protocol = protocol.replace('Binary repeated-region prompt hints and bounded source-local helper expansion only; no original-inlining claim.',
                            'Controller-locked SQLite journal recovery, explicit connection closure, and storage failure diagnostics only.')
(OUT / 'deploy.py').write_text(protocol)
print(json.dumps({'stage': str(STAGE), 'files': list(manifest)}))
