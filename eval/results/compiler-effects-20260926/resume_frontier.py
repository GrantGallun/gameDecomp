"""Resume authorized deterministic work after the reviewed overflow amendment.

Fresh receipts; first batch 15 jobs, then at most four 100-job batches. Existing
controller checks preserve exact membership, disable model work, and restore
pause markers on failure. A STOP file in frontier/ stops between batches.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / 'frontier-run-20260926'


def bounded_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--batch', type=int)
    parser.add_argument('--max-batches', type=int, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.max_batches <= 5:
        parser.error('--max-batches must be between 1 and 5')
    if args.batch is not None and not 1 <= args.batch <= 5:
        parser.error('--batch must be between 1 and 5')
    return args


if __name__ == '__main__':
    args = bounded_args(sys.argv[1:])
    amendment = json.loads((HERE / 'overflow/recovery/amendment.json').read_text())
    if amendment['kind'] != 'cvt-w-s-overflow-guard' or not amendment.get('result', {}).get('new_pin_digest'):
        raise RuntimeError('reviewed overflow amendment has not completed')
    spec = importlib.util.spec_from_file_location('frontier_after_overflow', PRIOR / 'run_frontier.py')
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.HERE = HERE / 'frontier'
    driver.HERE.mkdir(exist_ok=True)
    driver.HELPER = PRIOR / 'campaign_batch.py'
    driver.BATCH_SIZE = 15 if args.batch == 1 else 100
    driver.__file__ = __file__
    sys.argv = [__file__, '--max-batches', str(args.max_batches)]
    if args.run:
        sys.argv.append('--run')
    if args.audit:
        sys.argv.append('--audit')
    if args.batch is not None:
        sys.argv += ['--batch', str(args.batch)]
    driver.main()
