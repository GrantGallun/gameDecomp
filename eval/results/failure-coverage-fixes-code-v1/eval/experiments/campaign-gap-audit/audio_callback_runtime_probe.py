"""Explicit header-assisted callback ABI experiment; no callee effect claims."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import agentrepair, semantic_lane
from solver import callback_abi, callee_execution, modelrepair, type_constraints, workspace


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='alAudioFrame'
    output=root/'eval/results/audio-callback-runtime-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite experiment')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    source=(root/'eval/results/audio-frame-pointer-difference-v1.best.c').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!='c31b9206e696ba99433a39ee5737c3a63d8d9c1df6d64a47d4c427d6b77a0bb2':
        raise ValueError('source changed')
    if source.count('var_s4 + 8')!=1:
        raise ValueError('ambiguous pointer-advance hypothesis')
    sources={'baseline':source,'one_element':source.replace('var_s4 + 8','var_s4 + 1')}
    ws=repo/'nonmatchings'/function
    config=json.loads((ws/'.compiler-target.json').read_text())
    if config['function']!=function:
        raise ValueError('compiler target mismatch')
    measurement=type_constraints.measure(repo,ws,source,function,config['target'])
    target=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    specs={}
    target_spec=callback_abi.admit(target,measurement)
    specs[target_spec.identity]=target_spec
    states={}
    db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite'
    with sqlite3.connect(db,timeout=120) as conn:
        for name,body in sources.items():
            tag=function+'_callback_'+name+'_'+str(time.time_ns())
            attempt=workspace.score(ws,repo,tag,body,conn=conn,func=function,
                strategy='assisted-callback-stride-hypothesis',model='zero-model')
            if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
                raise ValueError('callback comparison source no longer compiles')
            obj=ws/(tag+'.o')
            assembly=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
            spec=callback_abi.admit(assembly,measurement)
            specs[spec.identity]=spec
            states[name]=modelrepair.CandidateState(body,attempt,obj)
    environment=callee_execution.Environment(callbacks=tuple(specs.values()))
    panel=semantic_lane.Panel(repo,ws,function,64,10000,5000,environment,source)
    results={name:panel(state) for name,state in states.items()}
    receipt={'kind':'header-bound-callback-argument-comparison','function':function,
        'hypothesis':'Acmd one-element advance instead of eight-element advance; assisted from binary argument delta and measured layout',
        'sources':{name:hashlib.sha256(body.encode()).hexdigest() for name,body in sources.items()},
        'attempts':{name:state.attempt.receipt_id for name,state in states.items()},
        'results':results,'panel':panel.report,'header_measurement':measurement,
        'callback_environment':environment.manifest(),'model_calls':0,'reference_bodies_used':False,
        'integration_requested':False,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'argument contracts only; raw stack labels, pointee identity/effects and synthetic callback results remain debt'}
    agentrepair._atomic_json(output,receipt)
    print(json.dumps({'output':str(output),'attempts':receipt['attempts'],
        'results':{name:{'status':row['status'],'counts':row.get('counts')} for name,row in results.items()}}))


if __name__=='__main__':
    main()
