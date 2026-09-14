"""Few explicit save slots with a checksum-sized step budget; no semantic promotion."""
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from collections import Counter
from eval import agentrepair
from solver import mips_differential as d, workspace, type_constraints


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--byte-step',action='store_true')
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp'); repo=Path('/home/grant/decomp/sbk1')
    function='writeControllerPakSave'
    parent=root/'eval/results/save-measured-memory-original-v1.json'
    output=root/('eval/results/save-long-loop-byte-step-v1.json' if args.byte_step else 'eval/results/save-long-loop-v1.json')
    if output.exists(): raise ValueError('refusing to overwrite experiment')
    receipt=json.loads(parent.read_text())
    source=Path(receipt['result']['best_source_path']).read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=receipt['result']['best_source_sha256']:
        raise ValueError('parent source changed')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    ws=workspace.bootstrap(repo,function); tag='save_long_loop_'+str(time.time_ns())
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite') as conn:
        attempt=workspace.score(ws,repo,tag,source,conn=conn,func=function,
            strategy='explicit-save-slot-long-loop',model='zero-model')
    if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
        raise ValueError('source no longer compiles')
    rewrite_report=None
    if args.byte_step:
        from solver import rewrites
        plans=rewrites.byte_pointer_step_rewrites(source,attempt.diff or '')
        if len(plans)!=1: raise ValueError('requires exactly one residual-derived byte-step proposal')
        previous=attempt.receipt_id
        before=hashlib.sha256(source.encode()).hexdigest()
        source=plans[0](source); tag+='_byte_step'
        with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite') as conn:
            attempt=workspace.score(ws,repo,tag,source,conn=conn,func=function,
                strategy='explicit-save-residual-byte-step',model='zero-model',parent_attempt_id=previous)
        if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
            raise ValueError('byte-step proposal does not compile')
        rewrite_report={'parent_attempt_id':previous,'parent_source_sha256':before,
                        'scope':'existing residual generator, targeted diagnostic activation'}
    obj=ws/(tag+'.o')
    target=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    candidate=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
    configured=json.loads((ws/'.compiler-target.json').read_text())['target']
    measured=type_constraints.measure(repo,ws,source,function,configured,
        global_symbols=('gGameSaveDataBuffer','gControllerPakRetryCounts'))
    for extent in measured['global_extents']:
        target+='\n# MIPS_DIFF_EXTENT '+extent['name']+' '+str(extent['size'])+'\n'
    left=d.Program.parse(function,target); right=d.Program.parse(function,candidate)
    result={'kind':'explicit-save-slot-long-loop','status':'running','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'parent':str(parent),'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'attempt_id':attempt.receipt_id,'rewrite':rewrite_report,'measurement':measured,'max_steps':120000,'rows':[],
        'target_assembly_sha256':hashlib.sha256(target.encode()).hexdigest(),
        'candidate_assembly_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False,
        'debt':['four header-declared slots, one synthetic memory seed; not all input states',
                'OS calls opaque; explicit successful FindFile/ReadWrite returns, no device/file effects',
                'header-assisted memory extents; no universal equivalence or byte-exact claim']}
    agentrepair._atomic_json(output,result)
    for slot in range(4):
        case=d.TestCase('slot-'+str(slot),54784,entry_registers=(('a0',slot),),
            call_returns=(('osPfsFindFile',1,0),('osPfsReadWriteFile',2,0)))
        row=d.compare_programs(left,right,case,call_arities=receipt['semantic_panel']['call_arities'],max_steps=120000)
        record={'case':asdict(case),'status':row.status,'first_divergence':row.first_divergence,
            'target_status':row.target.status,'target_error':row.target.error,'target_steps':len(row.target.trace),
            'candidate_status':row.candidate.status,'candidate_error':row.candidate.error,'candidate_steps':len(row.candidate.trace),
            'target_coverage':d.coverage_report(left,[row.target]).to_dict()}
        result['rows'].append(record)
        agentrepair._atomic_json(output,result)
        print(json.dumps({k:v for k,v in record.items() if k not in {'case','target_coverage'}}),flush=True)
    result['status']='finished'; result['counts']=dict(Counter(r['status'] for r in result['rows']))
    agentrepair._atomic_json(output,result)


if __name__=='__main__': main()
