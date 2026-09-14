"""Replay the pre-admission caller inputs against concrete callees."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from dataclasses import asdict

from eval import agentrepair
from solver import callee_execution, mips_differential as d, workspace


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='handleCharacterSelectCourseSelection'
    path=root/f'eval/results/failure-coverage-fixes-replay-v2-artifacts/{function}.json'
    output=root/'eval/results/concrete-callee-path-probe-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite probe')
    receipt=json.loads(path.read_text())
    panel=receipt['semantic_panel']
    ws=repo/'nonmatchings'/function
    assembly=workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    program=d.Program.parse(function,assembly)
    contracts=panel['call_contracts']
    environment,admission=callee_execution.load_binary_leaves(repo,contracts,contracts)
    counts=Counter()
    examples={}
    for raw in panel['cases']:
        case=d.TestCase(**raw)
        run=d.execute_case(program,case,call_arities=panel['call_arities'],
            return_registers=tuple(panel['abi']['return_registers']),max_steps=10000,callee_environment=environment)
        counts[run.status]+=1
        key=(run.status,run.error.split(' address ')[0])
        if key not in examples and len(examples)<6:
            examples[key]={'case':asdict(case),'run':run.to_dict(),
                'mutation_read_locations':sorted(d._read_locations(run),key=str),
                'urgent_location':d._failure_input_location(run)}
    result={'kind':'pre-admission-input-concrete-replay','function':function,
        'input_receipt':str(path),'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'target_assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'counts':dict(counts),'examples':list(examples.values()),'admission':admission,
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False}
    agentrepair._atomic_json(output,result)
    print(json.dumps({'counts':dict(counts),'examples':[{'status':e['run']['status'],'error':e['run']['error'],
        'locations':e['mutation_read_locations'],'urgent':e['urgent_location']} for e in examples.values()]}))


if __name__=='__main__':
    main()
