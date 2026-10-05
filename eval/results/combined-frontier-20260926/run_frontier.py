"""Run the ordinary frozen controller with the combined confirmed capabilities.

First batch is a 15-job canary; subsequent batches use 200 jobs and the existing
three-worker launch. The driver drains eligible deterministic work, checks exact
membership and model-call invariants, and restores the dispatch pause after each
batch. A STOP file in the chosen receipt directory stops between batches.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / 'frontier-run-20260926'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--batch', type=int)
    parser.add_argument('--max-batches', type=int, default=1000)
    parser.add_argument('--receipt-dir', default='frontier')
    args = parser.parse_args()
    if not 1 <= args.max_batches <= 1000:
        parser.error('--max-batches must be between 1 and 1000')
    if args.batch is not None and not 1 <= args.batch <= 1000:
        parser.error('--batch must be between 1 and 1000')
    # The inherited driver launches this wrapper for each child batch. Persist
    # the selected receipt folder in an environment variable for those children.
    import os
    folder = os.environ.get('COMBINED_FRONTIER_RECEIPT_DIR', args.receipt_dir)
    if not folder or Path(folder).name != folder or folder in {'.', '..'}:
        parser.error('receipt directory must be a simple folder name')
    os.environ['COMBINED_FRONTIER_RECEIPT_DIR'] = folder
    amendment = json.loads((HERE / 'deployment/amendment.json').read_text())
    if (amendment['kind'] != '20260926-branch-defaults-main-solver' or
            not amendment.get('result', {}).get('new_pin_digest')):
        raise RuntimeError('reviewed branch-default amendment has not completed')
    frontend = json.loads((HERE / 'frontend-payload/amendment.json').read_text())
    if (frontend['kind'] != '20260926-frontend-header-capabilities' or
            not frontend.get('result', {}).get('new_pin_digest')):
        raise RuntimeError('reviewed frontend amendment has not completed')
    spec = importlib.util.spec_from_file_location('combined_frontier_driver', PRIOR / 'run_frontier.py')
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.HERE = HERE / folder
    driver.HERE.mkdir(exist_ok=True)
    driver.HELPER = PRIOR / 'campaign_batch.py'
    driver.BATCH_SIZE = 15 if args.batch == 1 else 200
    driver.__file__ = __file__
    sys.argv = [__file__, '--max-batches', str(args.max_batches)]
    if args.run:
        sys.argv.append('--run')
    if args.audit:
        sys.argv.append('--audit')
    if args.batch is not None:
        sys.argv += ['--batch', str(args.batch)]
    try:
        driver.main()
    finally:
        if args.run and args.batch is None:
            # Diagnose both clean stops and failed batches. The report labels
            # durable in-flight rows without pretending crashed workers live.
            with (driver.HERE / 'diagnosis.log').open('w') as log:
                diagnosed = subprocess.run([
                    sys.executable, str(HERE / 'diagnose.py'), '--baseline', '1003',
                    '--output', str(driver.HERE / 'diagnosis.json'),
                ], stdout=log, stderr=subprocess.STDOUT, check=False)
            if diagnosed.returncode:
                print('Checkpoint diagnosis failed; inspect frontier/diagnosis.log', file=sys.stderr)


if __name__ == '__main__':
    main()
