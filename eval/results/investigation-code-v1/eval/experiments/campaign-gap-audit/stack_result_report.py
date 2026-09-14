"""Source-bound read-only stack-result diagnostic from a completed repair replay."""
import argparse
import hashlib
import json
from pathlib import Path
from solver import stack_result_evidence
from eval import agentrepair


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay',type=Path,required=True)
    p.add_argument('--function',required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():raise ValueError('refusing overwrite')
    repo=Path('/home/grant/decomp/sbk1')
    receipt=json.loads(args.replay.read_text())['result']
    source=args.replay.with_suffix('.best.c').read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=receipt['best_source_sha256']:
        raise ValueError('source hash mismatch')
    paths=list((repo/'asm'/'matchings').rglob(args.function+'.s'))
    if len(paths)!=1:raise ValueError('ambiguous target assembly')
    report=stack_result_evidence.collect(repo,source,args.function,paths[0].read_text(),
        receipt['best_residual']['frontend']['diagnostics'])
    report['replay_sha256']=hashlib.sha256(args.replay.read_bytes()).hexdigest()
    report['reference_bodies_used']=False
    agentrepair._atomic_json(args.out,report)
    print(json.dumps(report))


if __name__=='__main__':main()
