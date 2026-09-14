"""Controlled synthetic-callee comparison, never actual callee equivalence."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import argparse

from eval import agentrepair, semantic_lane
from solver import callee_execution as c, linked_callee, modelrepair, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--real-callee',action='store_true')
    args=parser.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    repo = Path('/home/grant/decomp/sbk1')
    function = 'updateRaceCourseProgressMeter'
    output = root/('eval/results/progress-output-real-callee-v1.json' if args.real_callee
                  else 'eval/results/progress-output-controlled-v1.json')
    if output.exists():
        raise ValueError('refusing to overwrite experiment')
    agentrepair._refuse_frozen_heldout(root/'eval/sets', function)
    expected = {63:'4880deefbe2612dfc925188b8cc0a69f64d759b7fc4ca03b8f89f11577f7ba23',
                66:'13add2f803ce65016aee1dd453959b10b3b277d43778d400c928bfa9fb4a9bcd'}
    db = root/'eval/results/kb-sbk1-range-replay-v1.sqlite'
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True) as conn:
        sources = {index:conn.execute('SELECT source_code FROM attempts WHERE id=?',(index,)).fetchone()[0]
                   for index in expected}
    for index, source in sources.items():
        if hashlib.sha256(source.encode()).hexdigest() != expected[index]:
            raise ValueError('candidate identity changed')
    ws = workspace.bootstrap(repo,function)
    states = {}
    with sqlite3.connect(db,timeout=120) as conn:
        for index, source in sources.items():
            tag = function+'_controlled_'+str(time.time_ns())
            attempt = workspace.score(ws,repo,tag,source,conn=conn,func=function,
                strategy='controlled-synthetic-output-comparison',model='zero-model')
            if not attempt.compiled or not (attempt.frontend or {}).get('passed'):
                raise ValueError('candidate no longer passes compiler/frontend')
            states[index] = modelrepair.CandidateState(source,attempt,ws/(tag+'.o'))
    scenarios = []
    for value in ((None,) if args.real_callee else (0,128,-1)):
        # This executes an explicit counterfactual body, NOT the real callee.
        assembly = f'li t0,{value}\nsw t0,0(a1)\nsw zero,0(a2)\njr ra\nnop'
        environment = c.Environment() if args.real_callee else c.Environment({'getRacePlayerRankingProgress':c.Leaf(assembly,
            'SYNTHETIC DEV ASSUMPTION: constant outputs, not ROM-derived callee behavior',
            return_registers=())})
        if args.real_callee:
            callee='getRacePlayerRankingProgress'
            agentrepair._refuse_frozen_heldout(root/'eval/sets',callee)
            callee_ws=repo/'nonmatchings'/callee
            assembly=workspace.semantic_assembly((callee_ws/'target_object_dump_normalized.s').read_text(),callee_ws/'target.o')
            baseline=root/'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
            with sqlite3.connect(f'file:{baseline.as_posix()}?mode=ro',uri=True) as conn:
                address,size=conn.execute('SELECT addr,size FROM functions WHERE name=?',(callee,)).fetchone()
            environment=c.Environment({callee:linked_callee.admit(repo,callee,assembly,address,size,return_registers=())})
        panel = semantic_lane.Panel(repo,ws,function,64,10000,5000,environment,sources[63])
        results = {str(index):panel(state) for index,state in states.items()}
        scenarios.append({'real_callee':args.real_callee,'assumed_first_output':value,
                          'assumed_second_output':None if args.real_callee else 0,
                          'panel':panel.report,'results':results})
        print(json.dumps({'assumed_output':value,'results':{k:{'status':v['status'],
            'counts':v.get('counts')} for k,v in results.items()}}),flush=True)
    agentrepair._atomic_json(output,{'kind':'real-linked-callee-candidate-comparison' if args.real_callee else 'controlled-synthetic-callee-candidate-comparison',
        'function':function,'parent_sources':expected,
        'replay_attempts':{str(k):v.attempt.receipt_id for k,v in states.items()},
        'scenarios':scenarios,'model_calls':0,'reference_bodies_used':False,
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':('same panel with ROM-bound ranking callee; remaining synthetic global inputs/opaque calls are not universal semantics'
                 if args.real_callee else 'same target-led panel within each synthetic scenario; not actual callee behavior'),
        'integration_requested':False})


if __name__ == '__main__':
    main()
