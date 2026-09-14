"""Stage only the fast integration change over the running frozen revision."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / 'eval/results/resume-pipeline-20260908/code'
STAGE = OUT / 'staged-code'
FILES = ('eval/fast_campaign.py', 'eval/campaign_integration.py',
         'tests/test_campaign_integration.py', 'tests/test_campaign_reasoned_effort.py')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

assert (OUT/'before-main/eval/fast_campaign.py').read_bytes() == (LIVE/'eval/fast_campaign.py').read_bytes()
refresh = '--refresh' in sys.argv
if refresh:
    assert STAGE.is_dir() and (OUT/'staged-manifest.json').is_file()
    archive=OUT/'staged-manifest.before-artifact-guard.json'
    if not archive.exists():
        shutil.copy2(OUT/'staged-manifest.json',archive)
else:
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'results'))
manifest = {}
for rel in FILES:
    assert (ROOT/rel).is_file(), rel
    shutil.copy2(ROOT/rel, STAGE/rel)
    if sha(LIVE/rel) != sha(STAGE/rel):
        manifest[rel] = {'old_sha256':sha(LIVE/rel), 'new_sha256':sha(STAGE/rel)}
if not refresh:
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results', target_is_directory=True)
(OUT/'staged-manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
