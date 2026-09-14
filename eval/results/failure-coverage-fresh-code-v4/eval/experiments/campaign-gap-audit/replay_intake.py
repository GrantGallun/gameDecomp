"""Non-integrating fresh intake replay with immutable input hashes and receipts."""
import argparse
import hashlib
from pathlib import Path
from eval import agentrepair, completion_campaign
from solver import workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--function',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    repo = Path('/home/grant/decomp/sbk1')
    inputs = args.out.with_name(args.out.stem+'-inputs.json')
    if any(p.exists() for p in (args.out,inputs,args.out.with_suffix('.best.c'))):
        raise ValueError('refusing to overwrite intake replay')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',args.function)
    ws = workspace.bootstrap(repo,args.function)
    pins = {name:hashlib.sha256((ws/name).read_bytes()).hexdigest()
            for name in ('base.c','target.s','target.o') if (ws/name).is_file()}
    agentrepair._atomic_json(inputs,{'function':args.function,'workspace':str(ws),
        'inputs':pins,'model_calls':0,'reference_bodies_used':False,'integration_requested':False})
    result = completion_campaign._intake(repo=repo,
        db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',function=args.function,node={},out=args.out)
    unchanged = all(hashlib.sha256((ws/name).read_bytes()).hexdigest()==digest for name,digest in pins.items())
    agentrepair._atomic_json(args.out,{'kind':'source-bound-intake-replay',
        'input_receipt':str(inputs),'inputs_unchanged':unchanged,'result':result})
    if not unchanged:
        raise ValueError('intake inputs changed during replay')
    print({k:result.get(k) for k in ('status','attempt_id','source_sha256','score')})


if __name__ == '__main__':
    main()
