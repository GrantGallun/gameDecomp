"""Stage the reasoned-effort option against the live frozen runtime, read-only.

Only eval/fast_campaign.py (plus its dedicated test) changes. The main-tree diff
was reviewed to contain nothing but the effort_profile/bind_runtime_options
option; anything else appearing in the manifest is a refusal.
"""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LIVE = ROOT / 'eval/results/resume-pipeline-20260908/code'
STAGE = OUT / 'staged-code'
FILES = ['eval/fast_campaign.py', 'tests/test_campaign_reasoned_effort.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if STAGE.exists():
        raise SystemExit('staged-code already exists; remove it to restage')
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for relative in FILES:
        shutil.copy2(ROOT / relative, STAGE / relative)
    changed = {}
    for path in STAGE.rglob('*'):
        if not path.is_file() or any(p in path.parts for p in ('__pycache__', '.pytest_cache')):
            continue
        relative = path.relative_to(STAGE).as_posix()
        prior = LIVE / relative
        if not prior.exists() or prior.read_bytes() != path.read_bytes():
            changed[relative] = {'old_sha256': sha(prior) if prior.exists() else None,
                                 'new_sha256': sha(path)}
    if set(changed) != set(FILES):
        raise SystemExit(f'unexpected staged changes: {sorted(set(changed) ^ set(FILES))}')
    (OUT / 'staged-manifest.json').write_text(json.dumps(changed, indent=2) + '\n')
    print(json.dumps(changed, indent=2))


if __name__ == '__main__':
    main()
