"""Validate automatic wide-return activation from the saved assisted layout source."""
import hashlib
import argparse
import importlib
import json
from pathlib import Path

from eval import agentrepair


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fresh-byte-view',action='store_true')
    parser.add_argument('--original',action='store_true')
    parser.add_argument('--function',default='getRaceCourseSurfaceHeight')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    function=args.function
    if function!='getRaceCourseSurfaceHeight' and not args.original:
        raise ValueError('other functions require --original')
    root=Path('/mnt/c/Code/gameDecomp')
    parent=root/'eval/results/surface-pointer-layout-v1.json'
    if args.original:
        if args.fresh_byte_view:
            raise ValueError('original probe must not pre-repair its source')
        parent=root/f'eval/results/failure-coverage-fixes-replay-v5-artifacts/{function}.json'
    previous=json.loads(parent.read_text())
    source=Path(previous['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=previous['result']['best_source_sha256']:
        raise ValueError('parent source changed')
    output=root/('eval/results/surface-original-pipeline-v1.json' if args.original else
        'eval/results/surface-byteview-pipeline-v1.json' if args.fresh_byte_view else
        'eval/results/surface-wide-pipeline-v1.json')
    if function!='getRaceCourseSurfaceHeight':
        output=root/f'eval/results/{function}-original-pipeline-v1.json'
    output=args.out or output
    if output.exists():
        raise ValueError('refusing to overwrite probe')
    if args.fresh_byte_view:
        from solver import m2c_input,m2c_byte_view
        repo=Path('/home/grant/decomp/sbk1')
        draft,meta=m2c_input.draft(repo,repo/'nonmatchings/getRaceCourseSurfaceHeight/target.s',valid_syntax=True)
        if draft.returncode:
            raise ValueError(draft.stderr)
        lowered=m2c_byte_view.lower(draft.stdout,'getRaceCourseSurfaceHeight')
        source='#include "common.h"\n'+lowered['source']
        inputs=output.with_name(output.stem+'-inputs.json')
        if inputs.exists():
            raise ValueError('refusing to overwrite input receipt')
        agentrepair._atomic_json(inputs,{'draft':meta,'lowering':lowered,
            'reference_bodies_used':False,'scope':'fresh assembly-only draft; no assisted field/record definitions'})
    provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=Path('/home/grant/decomp/sbk1'),
        db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',function=function,
        source=source,source_parent_attempt_id=None if args.fresh_byte_view or args.original else previous['result']['best_attempt_id'],out=output,
        best_source_out=output.with_suffix('.best.c'),model='zero-model-pipeline',endpoint='http://127.0.0.1:1',
        draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({k:result.get(k) for k in ['best_attempt_id','best_source_sha256','exact']}))


if __name__=='__main__':
    main()
