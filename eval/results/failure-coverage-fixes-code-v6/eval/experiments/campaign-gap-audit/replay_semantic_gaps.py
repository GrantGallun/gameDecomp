"""Source-bound zero-model replay and paired debugger-evidence ablations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import agentrepair, semantic_lane
from solver import callee_execution, mips_differential as d, modelrepair, stack_buffers, workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refusing to overwrite replay receipt')
    checkpoint = json.loads(args.checkpoint.read_text())
    receipt = {'kind':'semantic-gap-paired-replay', 'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
               'reference_bodies_used':False,'model_calls':0,'functions':{}}
    with sqlite3.connect(args.db) as conn:
        for name in ('drawRaceSetupSavePlayerPanels','findRaceItemProjectileHomingTarget'):
            agentrepair._refuse_frozen_heldout(Path.cwd()/'eval/sets',name)
            node = checkpoint['nodes'][name]
            source = Path(node['source']).read_text()
            if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
                raise ValueError('checkpoint source changed')
            ws = workspace.bootstrap(args.repo,name)
            run_id = 'semantic-gaps-'+str(time.time_ns())
            tag = run_id+'-root'
            root = workspace.score(ws,args.repo,tag,source,conn=conn,func=name,
                strategy='semantic-gap-root',run_id=run_id,parent_attempt_id=node['attempt_id'])
            panel = semantic_lane.Panel(args.repo,ws,name)
            state = modelrepair.CandidateState(source,root,ws/(tag+'.o'))
            result = {'root_attempt':root.receipt_id,'root_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                      'root':panel(state), 'score':root.score,'exact':root.exact,'variants':[]}
            assembly = workspace.semantic_assembly((ws/(tag+'_object_dump_normalized.s')).read_text(),state.object_path)
            no_literals = re.sub(r'(?m)^# MIPS_DIFF_BYTES \.\S+ [0-9a-fA-F]+\n?', '',assembly)
            opaque_pair = callee_execution.Environment({n:l for n,l in panel.callee_environment.leaves.items()
                                                       if not l.word_pair_multiply})
            for label, code, environment in [('full',assembly,panel.callee_environment),
                    ('without-section-bytes',no_literals,panel.callee_environment),
                    ('without-word-pair-execution',assembly,opaque_pair)]:
                rows = d.run_suite(panel.target,code,panel.cases,target_name=name,candidate_name=name+'-candidate',
                    call_arities=panel.arities,return_registers=panel.returns,max_steps=panel.max_steps,
                    callee_environment=environment)
                result.setdefault('paired_ablations',{})[label] = {
                    'counts':dict(Counter(r.status for r in rows)),
                    'first_failure':next((d.causal_feedback(r,max_steps=6) for r in rows if r.status!='passed'),None),
                    'callee_calls':sum(len(r.target.concrete_calls) for r in rows)}
            variants, result['stack_hypotheses'] = stack_buffers.candidates(source,workspace.target_asm(ws,name),name)
            for index,(label,child) in enumerate(variants):
                child_tag = run_id+'-child-'+str(index)
                attempt = workspace.score(ws,args.repo,child_tag,child,conn=conn,func=name,
                    strategy='semantic-gap-'+label,run_id=run_id,parent_attempt_id=root.receipt_id,
                    extra={'stack_hypotheses':result['stack_hypotheses']})
                observed = panel(modelrepair.CandidateState(child,attempt,ws/(child_tag+'.o')))
                result['variants'].append({'label':label,'attempt_id':attempt.receipt_id,
                    'source':str(ws/(child_tag+'.c')),'source_sha256':hashlib.sha256(child.encode()).hexdigest(),
                    'compiled':attempt.compiled,'frontend':attempt.frontend,'score':attempt.score,
                    'exact':attempt.exact,'semantic':observed})
            receipt['functions'][name] = result
            agentrepair._atomic_json(args.output,receipt)
            print(json.dumps({'function':name,'root':result['root']['counts'],
                'variants':[{k:v for k,v in r.items() if k in ('label','compiled','score','exact')} for r in result['variants']]}),flush=True)


if __name__ == '__main__':
    main()
