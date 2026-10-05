"""Fresh whole-ROM probe of an unchanged retained candidate plus the verified union."""
from pathlib import Path
import hashlib
import importlib
import json
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[2]
FROZEN = MAIN / 'eval/results/resume-pipeline-20260908/code'
WORK = Path('/home/grant/decomp/experiments/integration-headers-20261002-v4')
REPO = Path('/home/grant/decomp/sbk1')
DB = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    code = WORK / 'code'
    if code.exists():
        raise RuntimeError('fresh probe directory required')
    shutil.copytree(FROZEN, code, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    changed = 'eval/prepare_integration.py'
    shutil.copy2(MAIN / changed, code / changed)
    for rel in ('tests/test_integration_header_conflicts.py', 'tests/test_integration_declarations.py',
                'tests/test_prepare_integration.py'):
        shutil.copy2(MAIN / rel, code / rel)
    snapshot = HERE / 'reviewed' / changed
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(code / changed, snapshot)
    sys.path.insert(0, str(code))
    from eval import prepare_integration, integration_gate
    inputs = json.loads((HERE / 'inputs.json').read_text())
    names = inputs['union'] + ['checkMainMenuSecretCode']
    entries = [inputs['entries'][name] for name in names]
    t0 = time.monotonic()
    manifest = prepare_integration.prepare(repo=REPO, db=DB, entries=entries, output_dir=WORK / 'prepared')
    receipt = integration_gate.run(repo=REPO, manifest=manifest, output=WORK / 'integration.json')
    result = {'status': receipt['status'], 'whole_rom_verified': receipt['whole_rom_verified'],
              'original_union_count': len(inputs['union']), 'combined_union_count': len(names),
              'candidate': 'checkMainMenuSecretCode', 'candidate_source_unchanged': True,
              'source_checkpoint': inputs['checkpoint'], 'manifest': str(manifest),
              'receipt': str(WORK / 'integration.json'), 'code': str(code),
              'old_sha256': hashlib.sha256((FROZEN / changed).read_bytes()).hexdigest(),
              'new_sha256': hashlib.sha256((code / changed).read_bytes()).hexdigest(),
              'elapsed_seconds': time.monotonic() - t0, 'training_eligible': False,
              'scope': 'Header-assisted integration of a previously function-certified candidate; not a clean new solver discovery'}
    (HERE / 'proof.json').write_text(json.dumps(result, indent=2) + '\n')
    shutil.copy2(manifest, HERE / 'proof-manifest.json')
    shutil.copy2(WORK / 'integration.json', HERE / 'proof-integration.json')
    print(json.dumps(result, indent=2))
    if not receipt['whole_rom_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
