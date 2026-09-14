"""Explicit binary-stride geometry fixtures; not an automatic object generator."""
from collections import Counter
import argparse
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import time

from eval import agentrepair
from solver import callee_execution, mips_differential as d, workspace


def cases():
    rows=[]
    for reverse in (False,True):
        for flag in (0,1):
            for x,z in ((0,0),(1,1),(4,4),(8,0),(0,8),(-1,1),(9,9)):
                vertices=((0,2,0),(8,4,0),(0,6,8))
                indices=(0,2,1) if reverse else (0,1,2)
                writes=[('gRaceCourseSurfaces',4,d.PLAYER_BASE),
                    ('gRaceCourseSurfaceFaces',4,d.ARG_POINTER_BASES['a1']),
                    ('gRaceCourseSurfaceCoords',4,d.ARG_POINTER_BASES['a2'])]
                writes.extend((f'@arg1+0x{i*2:x}',2,v) for i,v in enumerate(indices))
                writes.append(('@arg1+0x7',1,flag))
                writes.extend((f'@arg2+0x{i*6+j*2:x}',2,v & 0xffff)
                    for i,vertex in enumerate(vertices) for j,v in enumerate(vertex))
                rows.append(d.TestCase(f'orientation-{int(reverse)}-flag-{flag}-point-{x}-{z}',1,
                    player_writes=((20,2,0),(22,2,1)),global_writes=tuple(writes),
                    entry_registers=(('a0',0),('a1',(x<<17)&0xffffffff),('a2',(z<<17)&0xffffffff))))
    return tuple(rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repair-word-pairs',action='store_true')
    parser.add_argument('--word-view',action='store_true')
    parser.add_argument('--generic-word-view',action='store_true')
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='getRaceCourseSurfaceHeight'
    if args.word_view and not args.repair_word_pairs:
        raise ValueError('--word-view requires --repair-word-pairs')
    output=root/('eval/results/surface-explicit-geometry-generic-v1.json' if args.generic_word_view else
        'eval/results/surface-explicit-geometry-wordview-v1.json' if args.word_view else
        'eval/results/surface-explicit-geometry-wide-v1.json' if args.repair_word_pairs
        else 'eval/results/surface-explicit-geometry-v1.json')
    if output.exists():
        raise ValueError('refusing to overwrite experiment')
    parent=root/'eval/results/surface-pointer-layout-v1.json'
    receipt=json.loads(parent.read_text())
    source=Path(receipt['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=receipt['result']['best_source_sha256']:
        raise ValueError('source changed')
    changes=[]
    generic_report=None
    if args.generic_word_view:
        if args.repair_word_pairs or args.word_view:
            raise ValueError('generic probe cannot include assisted edits')
        from solver import wide_return_repair
        environment,admission=callee_execution.load_binary_leaves(repo,['__ll_mul'])
        generic_report=wide_return_repair.propose(source,function,environment,byteorder='big')
        if not generic_report['changes']:
            raise ValueError('generic repair declined: '+str(generic_report['declines']))
        source=generic_report['source']
    if args.repair_word_pairs:
        changes=[('s32 __ll_mul(', 'u64 __ll_mul(')]
        names=['temp_ret']+[f'temp_ret_{i}' for i in range(2,7)]
        changes.extend((f's32 {name};',f'u64 {name};') for name in names)
        changes.extend((f'sp50 = {name};',f'sp50 = (s32) ({name} >> 32);')
            for name in ('temp_ret_2','temp_ret_4'))
        changes.append(('sp48 = temp_ret_6;','sp48 = (s32) (temp_ret_6 >> 32);'))
        changes.extend((f' - {name})',f' - (s32) ({name} >> 32))')
            for name in ('temp_ret_3','temp_ret_5','temp_ret'))
        for before,after in changes:
            if source.count(before)!=1:
                raise ValueError('ambiguous word-pair edit: '+before)
            source=source.replace(before,after)
        if args.word_view:
            for name in names:
                source=re.sub(r'\b'+name+r'\b',name+'.wide',source)
                source=source.replace(f'u64 {name}.wide;',
                    f'union {{ u64 wide; struct {{ s32 high; u32 low; }} words; }} {name};')
                source=source.replace(f'(s32) ({name}.wide >> 32)',f'{name}.words.high')
                source=source.replace(f'(u32) (u64) {name}.wide',f'{name}.words.low')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    ws=workspace.bootstrap(repo,function)
    tag='surface_geometry_probe_'+str(time.time_ns())
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite') as conn:
        attempt=workspace.score(ws,repo,tag,source,conn=conn,func=function,
            strategy='explicit-surface-geometry-input-probe',model='zero-model')
    if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
        raise ValueError('candidate no longer compiles')
    obj=ws/(tag+'.o')
    target=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    candidate=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
    environment,admission=callee_execution.load_binary_leaves(repo,['__ll_mul'])
    if '__ll_mul' not in environment.leaves:
        raise ValueError('multiply admission failed: '+str(admission))
    rows=d.run_suite(target,candidate,cases(),call_arities={'__ll_mul':4,'__ll_div':4},
        return_registers=('v0',),max_steps=10000,callee_environment=environment)
    coverage=d.coverage_report(d.Program.parse(function,target),[r.target for r in rows]).to_dict()
    result={'kind':'explicit-surface-geometry-differential-panel','function':function,
        'parent_receipt':str(parent),'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'attempt_id':attempt.receipt_id,
        'source':source,'assisted_source_changes':changes,'big_endian_union_word_view':args.word_view,
        'generic_repair':generic_report,
        'target_assembly_sha256':hashlib.sha256(target.encode()).hexdigest(),
        'candidate_assembly_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'cases':[asdict(c) for c in cases()],'counts':dict(Counter(r.status for r in rows)),
        'coverage':coverage,'results':[r.to_dict() for r in rows],
        'callee_environment':environment.manifest(),'callee_admission':admission,
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False,
        'assumptions':['synthetic one-face records using observed binary strides and load offsets',
            '__ll_div four-word argument observation; division effects/results remain opaque'],
        'scope':'exercise previously unvisited geometry; not universal equivalence or game-valid objects'}
    agentrepair._atomic_json(output,result)
    print(json.dumps({'counts':result['counts'],'instructions':coverage['covered_instruction_count'],
        'edges':coverage['covered_branch_edge_count']}))


if __name__=='__main__':
    main()
