"""Stack-hint experiment; optional zero-model repair, never integration."""
import argparse
import hashlib
import json
from pathlib import Path

from solver import m2c_input


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True)
    p.add_argument('--function',required=True)
    p.add_argument('--offset',type=lambda s:int(s,0),required=True)
    p.add_argument('--type',choices=['s8','u8','s16','u16','s32','u32'],required=True)
    p.add_argument('--count',type=int,required=True)
    p.add_argument('--header',action='append',default=[])
    p.add_argument('--reason',required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--valid-syntax',action='store_true')
    p.add_argument('--repair',action='store_true')
    args=p.parse_args()
    if args.out.exists():
        raise ValueError('receipt already exists')
    from eval import agentrepair
    root=Path(__file__).resolve().parents[3]
    agentrepair._refuse_frozen_heldout(root/'eval/sets',args.function)
    repair_out=args.out.with_name(args.out.stem+'-repair.json')
    if args.repair and (repair_out.exists() or repair_out.with_suffix('.best.c').exists()):
        raise ValueError('repair output already exists')
    original=args.target.read_bytes()
    headers=[]
    for name in args.header:
        path=(args.repo/'include'/name).resolve()
        if not path.is_relative_to((args.repo/'include').resolve()) or path.suffix!='.h':
            raise ValueError('only public header evidence permitted')
        headers.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    result,meta=m2c_input.draft(args.repo,args.target,context_headers=tuple(args.header),
        stack_variables=((args.function,args.offset,args.type,args.count),),valid_syntax=args.valid_syntax)
    if result.returncode==0:
        result.stdout=''.join(f'#include "{name}"\n' for name in args.header)+result.stdout
        if args.valid_syntax:
            from solver import m2c_byte_view
            lowered=m2c_byte_view.lower(result.stdout,args.function,
                target_assembly=args.target.read_text())
            meta['byte_lowering']={k:v for k,v in lowered.items() if k!='source'}
            result.stdout=lowered['source']
    receipt={'schema':'stack-hint-probe-v1','function':args.function,
        'target_sha256':hashlib.sha256(original).hexdigest(),
        'hypothesis_reason':args.reason,'header_evidence':headers,
        'metadata':meta,'returncode':result.returncode,'stderr':result.stderr,
        'source':result.stdout,'source_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
        'status':'draft-only-unvalidated','compiler_pass':None,'semantic_pass':None}
    with args.out.open('x') as out:
        json.dump(receipt,out,indent=2)
    print(json.dumps({k:receipt[k] for k in ('returncode','source_sha256','status')}))
    if args.repair and result.returncode==0:
        import importlib
        provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
        result=agentrepair.run(repo=args.repo,db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',
            function=args.function,source=result.stdout,source_parent_attempt_id=None,
            out=repair_out,best_source_out=repair_out.with_suffix('.best.c'),
            model='zero-model-stack-hint-probe',endpoint='http://127.0.0.1:1',
            draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,
            temperature=0,num_predict=1,seed=20260906,cache_dir=None,verbose=False,
            provider=provider,resilient=True,compile_only=True,semantic_cases=64,
            semantic_steps=10000)['result']
        print(json.dumps({k:result.get(k) for k in ('best_attempt_id','best_source_sha256','exact')}))


if __name__=='__main__':
    main()
