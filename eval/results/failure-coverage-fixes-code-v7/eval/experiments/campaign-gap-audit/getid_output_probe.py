"""Paired current-range DEV probe; no model or reference implementation."""
import hashlib
import argparse
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair, semantic_lane
from solver import callee_execution, modelrepair, project_headers, workspace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',choices=('v1','v2'),default='v1')
    args=parser.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    repo = Path('/home/grant/decomp/sbk1')
    function = '__osGetId'
    source_path = root/'eval/results/failure-coverage-rom-paired-v1-batch-2-artifacts/1788724572962706623-__osGetId.best.c'
    output = root/f'eval/results/getid-output-environment-probe-{args.version}.json'
    if output.exists():
        raise ValueError('refusing to overwrite a historical receipt')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    source = source_path.read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != 'f25923a8a99cc86f002981dcc0dbf266027959ae3ae31decaa2ce88592d9d112':
        raise ValueError('source binding changed')
    ws = workspace.bootstrap(repo,function)
    tag = function+'_output_probe_'+str(time.time_ns())
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite',timeout=120) as conn:
        att = workspace.score(ws,repo,tag,source,conn=conn,func=function,
            strategy='paired-output-environment-probe',model='zero-model')
    state = modelrepair.CandidateState(source,att,ws/(tag+'.o') if att.compiled else None)
    if not att.compiled or (att.frontend or {}).get('passed') is not True:
        raise ValueError('probe requires a freshly compiled frontend-passing candidate')
    assembly = workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(),ws/'target.o')
    pairs = []
    for assumed in (False,True):
        environment, admission = callee_execution.load_binary_leaves(repo,project_headers.called_functions(assembly))
        if assumed:
            environment.outputs['__osContRamRead'] = callee_execution.OutputBuffer(3,32,
                'Explicit DEV assumption reused from stack-callee replay: success fills32 bytes '
                'without pointer escape; not hardware emulation or an established C object extent')
        panel = semantic_lane.Panel(repo,ws,function,64,10000,5000,environment,source)
        result = panel(state)
        pairs.append({'assumed_read_output':assumed,'panel':panel.report,
                      'admission':admission,'semantic':result})
        print(json.dumps({'assumed_read_output':assumed,'status':result['status'],
                          'counts':result.get('counts'),'obstructions':panel.execution_obstructions}),flush=True)
    agentrepair._atomic_json(output,{'kind':'paired-assumed-output-environment-probe',
        'function':function,'source_path':str(source_path),'source_sha256':digest,
        'attempt_id':att.receipt_id,'compiled':att.compiled,'frontend':att.frontend,
        'exact':att.exact,'pairs':pairs,'model_calls':0,'reference_bodies_used':False,
        'game_source_changed':False,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'different target-led panels per environment; not a same-panel candidate improvement or hardware proof'})


if __name__ == '__main__':
    main()
