"""Source-bound DEV replay of stack recovery and executable callee effects.

No model calls, no integration, no finished reference function bodies.
The output-buffer arm is explicitly an assumed hardware-facing environment.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import agentrepair
from eval.semantic_lane import Panel
from solver import callee_execution, llm, modelrepair, workspace


def main():
    root = Path('/mnt/c/Code/gameDecomp')
    output = root/'eval/results/stack-callee-replay-v1.json'
    worker = root/'eval/results/stack-callee-worker-v1.json'
    if output.exists() or worker.exists():
        raise ValueError('refusing to overwrite immutable replay receipts')
    repo, db = Path('/home/grant/decomp/sbk1'), Path('/home/grant/decomp/kb-sbk1.sqlite')
    function = '__osBlockSum'
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    state = json.loads((root/'eval/results/autonomy-wavefront-24-v14.json').read_text())['nodes'][function]
    with sqlite3.connect(db) as conn:
        source = agentrepair._source_for_attempt(conn,state['attempt_id'],function)
    if hashlib.sha256(source.encode()).hexdigest() != state['source_sha256']:
        raise ValueError('saved source does not match frozen candidate identity')
    start = time.monotonic()
    result = agentrepair.run(repo=repo,db=db,function=function,source=source,
        source_parent_attempt_id=state['attempt_id'],out=worker,
        best_source_out=worker.with_suffix('.best.c'),model='gpt-oss:20b',endpoint=llm.host(),
        draws=0,depth=1,beam=4,max_calls=0,timeout=120,think='high',num_thread=8,
        temperature=0.6,num_predict=1200,seed=20260906,cache_dir=None,verbose=True,
        resilient=True)
    ws = workspace.bootstrap(repo,function)
    environment, admission = callee_execution.load_binary_leaves(repo,
        ['__osSumcalc','__osContRamRead','__osPfsSelectBank'])
    assert '__osSumcalc' in environment.leaves, admission
    environment.outputs['__osContRamRead'] = callee_execution.OutputBuffer(3,32,
        'DEV assumption: successful read writes 32 bytes without pointer escape; '
        'motivated by ROM 0xA83E0..0xA8410 copy loop; not hardware emulation')
    panel = Panel(repo,ws,function,64,10000,5000,callee_environment=environment)
    pairs = []
    # Every arm is freshly compiled and source-bound. The selected worker output
    # is not assumed to be the generated array candidate without inspecting it.
    from solver import stack_buffers
    variants, hypotheses = stack_buffers.candidates(source,workspace.target_asm(ws,function),function)
    assert len(variants) == 1, hypotheses
    with sqlite3.connect(db,timeout=120) as conn:
        for index,(label,code) in enumerate([('saved-root',source),*variants]):
            tag = function+'_callee_audit_'+str(time.time_ns())
            att = workspace.score(ws,repo,tag,code,conn=conn,func=function,
                strategy='stack-callee-paired-audit',model='zero-model',
                run_id='stack-callee-audit-v1',parent_attempt_id=state['attempt_id'],
                relation='stack-callee-audit',action=label,
                extra={'hypotheses':hypotheses,'environment':environment.manifest()})
            candidate = modelrepair.CandidateState(code,att,ws/(tag+'.o') if att.compiled else None)
            semantic = panel(candidate)
            pairs.append({'label':label,'attempt_id':att.receipt_id,'parent_attempt_id':state['attempt_id'],
                'source_sha256':hashlib.sha256(code.encode()).hexdigest(),
                'compiled':att.compiled,'frontend':att.frontend,'score':att.score,'exact':att.exact,
                'semantic':semantic})
            print(label,att.compiled,att.score,att.exact,semantic and semantic.get('counts'),flush=True)
    receipt = {'kind':'source-bound-stack-callee-development-replay',
        'function':function,'source_parent_attempt_id':state['attempt_id'],
        'worker_receipt':str(worker),'worker_result':result['result'],
        'panel':panel.report,'admission':admission,'hypotheses':hypotheses,
        'pairs':pairs,'wall_seconds':time.monotonic()-start,'model_calls':0,
        'reference_bodies_used':False,'game_source_changed':False,
        'scope':'fixed target-led finite panel; explicit output environment, other callees opaque; not universal semantics'}
    agentrepair._atomic_json(output,receipt)
    print(str(output),flush=True)


if __name__ == '__main__':
    main()
