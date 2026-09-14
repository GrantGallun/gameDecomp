"""Explicit one-node circular timer-list tests; opaque callee effects remain debt."""
from collections import Counter
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import agentrepair
from solver import mips_differential as d, workspace


def cases():
    head,node=d.PLAYER_BASE,d.ARG_POINTER_BASES['a1']
    rows=[]
    for high,low in ((0,0),(0,19),(0,20),(0,21),(1,0),(0xffffffff,0xffffffff)):
        for interval in (0,1,1<<32):
            for message in (False,True):
                words={0:head,4:head,8:interval>>32,12:interval&0xffffffff,
                    16:high,20:low,24:d.ARG_POINTER_BASES['a2'] if message else 0,28:0x1234}
                writes=(('__osTimerList',4,head),('__osTimerCounter',4,0))
                writes+=tuple((f'@arg1+0x{off:x}',4,value) for off,value in words.items())
                rows.append(d.TestCase(f'value-{high:x}-{low:x}-interval-{interval:x}-mq-{int(message)}',1,
                    player_writes=((0,4,node),(4,4,node)),global_writes=writes,
                    call_returns=(('osGetCount',0,20),)))
    rows.append(d.TestCase('empty-list',1,player_writes=((0,4,head),(4,4,head)),
        global_writes=(('__osTimerList',4,head),('__osTimerCounter',4,0))))
    return tuple(rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--parent',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='__osTimerInterrupt'
    output=args.out or root/'eval/results/timer-circular-list-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite experiment')
    parent=args.parent or root/'eval/results/timer-wide-reconstruction-v1.json'
    receipt=json.loads(parent.read_text())
    source=Path(receipt['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=receipt['result']['best_source_sha256']:
        raise ValueError('source changed')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    ws=workspace.bootstrap(repo,function)
    tag='timer_list_probe_'+str(time.time_ns())
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite') as conn:
        attempt=workspace.score(ws,repo,tag,source,conn=conn,func=function,
            strategy='explicit-circular-list-input-probe',model='zero-model')
    if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
        raise ValueError('candidate no longer compiles')
    obj=ws/(tag+'.o')
    target=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    candidate=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
    rows=d.run_suite(target,candidate,cases(),call_arities=receipt['semantic_panel']['call_arities'],
        return_registers=(),max_steps=10000)
    coverage=d.coverage_report(d.Program.parse(function,target),[r.target for r in rows])
    result={'kind':'explicit-circular-list-differential-panel','function':function,
        'parent_receipt':str(parent),'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'attempt_id':attempt.receipt_id,
        'target_assembly_sha256':hashlib.sha256(target.encode()).hexdigest(),
        'candidate_assembly_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'cases':[asdict(c) for c in cases()],'counts':dict(Counter(r.status for r in rows)),
        'coverage':coverage.to_dict(),'results':[r.to_dict() for r in rows],
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False,
        'assumptions':['header-assisted timer field offsets, one timer plus sentinel in mapped synthetic regions',
            'osGetCount first call returns20; callees remain opaque without queue/reinsertion effects'],
        'scope':'test reconstructed compare/borrow/removal/copy/call paths under explicit inputs, not actual scheduler semantics'}
    agentrepair._atomic_json(output,result)
    print(json.dumps({'counts':result['counts'],'coverage':coverage.to_dict(),
        'failures':[(r.case,r.first_divergence) for r in rows if r.status!='passed'][:3]}))


if __name__=='__main__':
    main()
