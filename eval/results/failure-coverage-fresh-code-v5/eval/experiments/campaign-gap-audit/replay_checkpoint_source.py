"""Zero-model repair replay of a hash-bound checkpoint source, without integration."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
from eval import agentrepair


def selected_source(checkpoint, function):
    state=json.loads(checkpoint.read_text())
    if state.get('inflight') or state.get('status') not in {
            'paused_budget','stalled_requires_new_strategy_or_evidence',
            'awaiting_integration','cohort_objects_exact','cohort_integrated'}:
        raise ValueError('requires terminal checkpoint without inflight work')
    node=state['nodes'][function]
    path=Path(node['source'])
    source=path.read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=node['source_sha256']:
        raise ValueError('checkpoint source hash mismatch')
    return source


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--function',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--compile-only',action='store_true')
    args=parser.parse_args()
    inputs=args.out.with_name(args.out.stem+'-inputs.json')
    if args.out.exists() or args.out.with_suffix('.best.c').exists() or inputs.exists():
        raise ValueError('refusing to overwrite replay')
    source=selected_source(args.checkpoint,args.function)
    root=Path('/mnt/c/Code/gameDecomp')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',args.function)
    agentrepair._atomic_json(inputs,{'checkpoint':str(args.checkpoint),
        'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        'function':args.function,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'compile_only':args.compile_only,'model_calls':0,'integration_requested':False,
        'reference_bodies_used':False})
    provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=Path('/home/grant/decomp/sbk1'),
        db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',function=args.function,
        source=source,source_parent_attempt_id=None,out=args.out,
        best_source_out=args.out.with_suffix('.best.c'),model='zero-model-checkpoint-replay',
        endpoint='http://127.0.0.1:1',draws=1,depth=1,beam=3,max_calls=0,
        timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        compile_only=args.compile_only,semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({k:result.get(k) for k in ('best_attempt_id','best_source_sha256','exact')}))


if __name__=='__main__':
    main()
