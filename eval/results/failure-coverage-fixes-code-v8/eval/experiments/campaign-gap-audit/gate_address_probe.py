"""Controlled binary-address-unit hypothesis; not a generic rewrite generator."""
import hashlib
import argparse
import importlib
import json
import re
from pathlib import Path
from eval import agentrepair


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--typed-position',action='store_true')
    parser.add_argument('--byte-increments',action='store_true')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp')
    parent=root/'eval/results/gate-header-byteview-v6.json'
    receipt=json.loads(parent.read_text())
    source=Path(receipt['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=receipt['result']['best_source_sha256']:
        raise ValueError('parent source changed')
    if args.byte_increments and not args.typed_position:
        raise ValueError('byte-increments probe requires typed-position')
    output=root/('eval/results/gate-byte-address-v3.json' if args.byte_increments else
        'eval/results/gate-byte-address-v2.json' if args.typed_position else
        'eval/results/gate-byte-address-v1.json')
    output=args.out or output
    inputs=output.with_name(output.stem+'-inputs.json')
    if output.exists() or inputs.exists():
        raise ValueError('refusing to overwrite experiment')
    changes=[('&gCourseGateSoundParams','(u8 *)gCourseGateSoundParams'),
        ('*(&gCourseGateAngles + (gRaceCourseIndex.signedValue * 0x10))',
         '*(s16 *)((u8 *)&gCourseGateAngles + (gRaceCourseIndex.signedValue * 0x10))')]
    changes.extend((f'= {value};',f'= (void *){value};')
        for value in ('0x2001678','0x2001730','0x2001810','0x20018E8'))
    if args.typed_position:
        changes.append(('(gRaceCourseIndex.signedValue * 0x10) + (u8 *)gCourseGateSoundParams',
            '(Vec3i *)((gRaceCourseIndex.signedValue * 0x10) + (u8 *)gCourseGateSoundParams)'))
    if args.byte_increments:
        matches=list(re.finditer(r'gRegionAllocPtr = (temp_v1(?:_\d+)?) \+ 8;',source))
        if len(matches)!=10:
            raise ValueError('unexpected pointer advance count')
        changes.extend((m[0],f'gRegionAllocPtr = (s32 *)((u8 *){m[1]} + 8);') for m in matches)
    candidate=source
    for before,after in changes:
        if candidate.count(before)!=1:
            raise ValueError('ambiguous span: '+before)
        candidate=candidate.replace(before,after)
    agentrepair._atomic_json(inputs,{'parent_receipt':str(parent),
        'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),'changes':changes,
        'binary_observations':['course index shifted4 before sound table address call',
            'course index shifted4 before signed-halfword angle table load'],
        'scope':'assisted source-bound address hypotheses; no reference bodies or integration'})
    provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=Path('/home/grant/decomp/sbk1'),
        db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',function='renderCourseGateObject',
        source=candidate,source_parent_attempt_id=receipt['result']['best_attempt_id'],out=output,
        best_source_out=output.with_suffix('.best.c'),model='zero-model-assisted',endpoint='http://127.0.0.1:1',
        draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({'attempt':result['best_attempt_id'],'compiled':result['best_residual']['compiled'],
        'score':result['best_residual']['weighted_progress_score'],
        'semantic_status':(result.get('semantic_validation') or {}).get('status'),
        'counts':(result.get('semantic_validation') or {}).get('counts')}))


if __name__=='__main__':
    main()
