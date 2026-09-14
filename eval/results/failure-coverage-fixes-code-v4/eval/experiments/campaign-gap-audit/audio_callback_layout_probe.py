"""Header-only callback layout and target-binary provenance, no ABI admission."""
import hashlib
import argparse
import json
from pathlib import Path

from eval import agentrepair
from solver import callback_abi, dataflow, type_constraints, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bindings',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    out=root/('eval/results/audio-callback-layout-bindings-v1.json' if args.bindings else
              'eval/results/audio-callback-layout-provenance-v1.json')
    if args.output:
        out=args.output.resolve()
    if out.exists():
        raise ValueError('refusing to overwrite experiment')
    function='alAudioFrame'
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    source=(root/'eval/results/audio-frame-pointer-difference-v1.best.c').read_text(encoding='utf-8')
    source_sha=hashlib.sha256(source.encode()).hexdigest()
    if source_sha!='c31b9206e696ba99433a39ee5737c3a63d8d9c1df6d64a47d4c427d6b77a0bb2':
        raise ValueError('source identity changed')
    ws=repo/'nonmatchings'/function
    target=json.loads((ws/'.compiler-target.json').read_text())
    if target['function']!=function:
        raise ValueError('compiler target mismatch')
    measured=type_constraints.measure(repo,ws,source,function,target['target'])
    assembly=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    analysis=dataflow.analyse(assembly)
    bindings=callback_abi.bind(assembly,measured) if args.bindings else None
    calls=[{'instruction':i,'target':c.target,
            'callee_value':c.target_value.describe(precise=True) if c.target_value else None,
            'arguments':[v.describe(precise=True) if v else None for v in c.arguments]}
           for i,c in sorted(analysis.callsites.items())]
    report={'kind':'header-callback-layout-and-binary-provenance','function':function,
        'source_sha256':source_sha,'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'reachable_blocks':len(analysis.graph.reachable()),'calls':calls,
        'header_measurement':measured,'callback_bindings':bindings,'model_calls':0,'reference_bodies_used':False,
        'execution_admitted':False,'integration_requested':False,
        'scope':'compiler-measured header layouts and binary call paths; root-type/ABI binding still required',
        'implementation_sha256':{name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name,module in [('cfg',dataflow.cfg),('dataflow',dataflow),('workspace',workspace),
                                ('type_constraints',type_constraints),('callback_abi',callback_abi)]}}
    agentrepair._atomic_json(out,report)
    print(json.dumps({'output':str(out),'calls':calls,'types':list(measured['layouts']),
        'bindings':bindings,
        'callback_fields':{name:[f for f in fields if f['member'] in ('handler','setParam','drvr.outputFilter')]
            for name,fields in measured['layouts'].items() if name in ('ALGlobals','ALFilter')}}))


if __name__=='__main__':
    main()
