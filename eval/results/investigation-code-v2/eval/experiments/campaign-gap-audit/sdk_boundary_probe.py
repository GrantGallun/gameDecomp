"""Replay ROM-derived SDK intake and retain positive boundary evidence."""
import argparse
import hashlib
from pathlib import Path

from eval import agentrepair
from solver import sdk_intake


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--function',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refusing to overwrite boundary receipt')
    agentrepair._refuse_frozen_heldout(Path('eval/sets'),args.function)
    try:
        ws = sdk_intake.bootstrap(args.repo,args.db,args.function)
        report = {'status':'admitted','workspace':str(ws)}
    except sdk_intake.UnsupportedTarget as exc:
        report = exc.report
    report['probe_implementation_sha256'] = hashlib.sha256(Path(sdk_intake.__file__).read_bytes()).hexdigest()
    report['model_calls'] = 0
    report['game_source_integrated'] = False
    agentrepair._atomic_json(args.output,report)
    print(report,flush=True)


if __name__ == '__main__':
    main()
