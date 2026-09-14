"""Small, explicitly assisted type-choice experiment; no C edits or promotions."""
import argparse
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair
from kb import attempts
from solver import llm, refine, type_constraints, workspace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists(): raise ValueError('new receipt path required')
    function='alLoadParam'
    with sqlite3.connect(args.db) as conn:
        refine.ensure_schema(conn)
        source=agentrepair._source_for_attempt(conn,29654,function)
        ws=args.repo/'nonmatchings'/function
        measured=type_constraints.measure(args.repo,ws,source,function,'build/src/ultra/audio/load.o')
        layouts=measured['layouts']
        observations=[a for a in type_constraints.compile_obligations.analyse(workspace.target_asm(ws,function))[1]
                      if a['address'] in ('param2','param2+0x8','param0+0x28')]
        choices={n:layouts[n] for n in ('ALParam','ALWaveTable')}
        run_id='type-anchor-'+str(time.time_ns())
        attempts.start_run(conn,run_id,kind='assisted-type-anchor',model='gpt-oss:20b',config={'source_attempt':29654,'trials':2,'reference_body_used':False})
        results=[]
        for index,with_flow in enumerate((False,True)):
            prompt=('Choose the compatible ordinary, uncast struct-pointer view for param2 from ALParam or ALWaveTable. '
                'This is one hypothesis decision, not whole-function reconstruction. A load width alone is not a universal C-type proof. '
                'Return JSON {"type":"ALParam or ALWaveTable","reason":"cite the distinguishing layout/flow constraint"}.\n'
                'BINARY OBSERVATIONS:\n'+json.dumps(observations)+'\nTARGET-COMPILER-MEASURED HEADER CHOICES:\n'+json.dumps(choices))
            if with_flow:
                prompt+='\nADDITIONAL EXPLICIT ASSUMPTION: param0 uses the ALLoadFilter header view. Its measured fields are:\n'+json.dumps(layouts['ALLoadFilter'])
            workspace.assert_uncontaminated(prompt,args.repo,function)
            started=time.time()
            text,meta=llm.generate(llm.host(),'gpt-oss:20b',prompt,timeout=420,think='low',num_predict=1800,
                temperature=0.2,seed=20260907+index,response_schema={'type':'object','required':['type','reason'],
                    'properties':{'type':{'type':'string','enum':['ALParam','ALWaveTable']},'reason':{'type':'string'}},'additionalProperties':False})
            pid=attempts.record_model_proposal(conn,run_id=run_id,parent_attempt_id=29654,prompt=prompt,raw_response=text,
                status='diagnostic-choice',model='gpt-oss:20b',sampling={'with_flow_assumption':with_flow,'seed':20260907+index},
                wall_ms=int((time.time()-started)*1000),token_cost=meta.get('eval_count',0))
            results.append({'proposal_id':pid,'with_flow_assumption':with_flow,'response':text,'tokens':meta.get('eval_count'),
                            'done_reason':meta.get('done_reason')})
            print(json.dumps(results[-1]),flush=True)
        agentrepair._atomic_json(args.out,{'kind':'assisted-small-type-choice','run_id':run_id,'results':results,
            'source_attempt':29654,'reference_body_used':False,'layout_measurement':measured,
            'scope':'two named alternatives on one DEV decision; not whole-function ability or an unbiased model benchmark'})


if __name__=='__main__': main()
