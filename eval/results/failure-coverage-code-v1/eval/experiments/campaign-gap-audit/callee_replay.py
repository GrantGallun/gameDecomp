"""Source-bound DEV replay of stack recovery and executable callee effects.

No model calls, no integration, no finished reference function bodies.
The output-buffer arm is explicitly an assumed hardware-facing environment.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import argparse
from collections import Counter
from dataclasses import asdict

from eval import agentrepair
from eval.semantic_lane import Panel
from solver import callee_execution, llm, modelrepair, workspace, mips_differential as differential


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version',default='v3',choices=['v2','v3'])
    args = parser.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    output = root/f'eval/results/stack-callee-replay-{args.version}.json'
    worker = root/f'eval/results/stack-callee-worker-{args.version}.json'
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
    # Directed DEV scenarios exercise eight successful iterations and a failure
    # at each read position. These are additional test inputs, not a source edit
    # or a generic environment proof; keep them separate from automatic cases.
    directed = []
    for seed in (0,1,0x12345678,0xffffffff):
        for fail_at in range(9):
            for page in (0,255):
                returns = [('__osPfsSelectBank',0,0)]
                returns += [('__osContRamRead',1+2*i,4 if i==fail_at else 0) for i in range(min(8,fail_at+1))]
                returns += [('__osPfsSelectBank',2+2*fail_at if fail_at<8 else 17,0)]
                directed.append(differential.TestCase(f'directed-{seed}-{fail_at}-{page}',seed,
                    global_writes=(('@arg2',2,0xffff),),
                    entry_registers=(('a1',page),('a2',differential.ARG_POINTER_BASES['a2']),('a3',255)),
                    call_returns=tuple(returns)))
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
                run_id='stack-callee-audit-'+args.version,parent_attempt_id=state['attempt_id'],
                relation='stack-callee-audit',action=label,
                extra={'hypotheses':hypotheses,'environment':environment.manifest()})
            candidate = modelrepair.CandidateState(code,att,ws/(tag+'.o') if att.compiled else None)
            semantic = panel(candidate)
            assembly = workspace.semantic_assembly((ws/(tag+'_object_dump_normalized.s')).read_text(),ws/(tag+'.o'))
            rows = differential.run_suite(panel.target,assembly,tuple(directed),target_name=function,
                call_arities=panel.arities,return_registers=panel.returns,callee_environment=environment)
            directed_report = {'counts':dict(Counter(r.status for r in rows)),
                'target_coverage':differential.coverage_report(differential.Program.parse(function,panel.target),[r.target for r in rows]).to_dict(),
                'candidate_coverage':differential.coverage_report(differential.Program.parse(function,assembly),[r.candidate for r in rows]).to_dict(),
                'failure_examples':[r.to_dict() for r in [r for r in rows if r.status!='passed'][:1]]}
            pairs.append({'label':label,'attempt_id':att.receipt_id,'parent_attempt_id':state['attempt_id'],
                'source_sha256':hashlib.sha256(code.encode()).hexdigest(),
                'compiled':att.compiled,'frontend':att.frontend,'score':att.score,'exact':att.exact,
                'semantic':semantic,'directed_cases':directed_report})
            print(label,att.compiled,att.score,att.exact,semantic and semantic.get('counts'),directed_report['counts'],flush=True)
    receipt = {'kind':'source-bound-stack-callee-development-replay',
        'function':function,'source_parent_attempt_id':state['attempt_id'],
        'worker_receipt':str(worker),'worker_result':result['result'],
        'panel':panel.report,'admission':admission,'hypotheses':hypotheses,
        'pairs':pairs,'wall_seconds':time.monotonic()-start,'model_calls':0,
        'code_snapshot':str(Path.cwd()),'directed_inputs':[asdict(case) for case in directed],
        'reference_bodies_used':False,'game_source_changed':False,
        'scope':'fixed target-led finite panel; explicit output environment, other callees opaque; not universal semantics'}
    agentrepair._atomic_json(output,receipt)
    print(str(output),flush=True)


if __name__ == '__main__':
    main()
