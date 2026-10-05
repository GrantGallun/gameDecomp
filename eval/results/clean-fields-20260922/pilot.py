"""Real compiler pilot of reusable owners and the campaign normalization entry."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval.intake_runners import RUNNERS
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_diagnostics, modelrepair, workspace

OUT=Path(__file__).resolve().parent
NATIVE=Path.home()/'decomp/experiments/clean-fields-20260922'
rows={r['function']:r for r in json.loads((OUT/'baseline-reviewed.json').read_text())['rows']}
conn=sqlite3.connect(NATIVE/'attempts.sqlite',timeout=60)
results=[]
for name in ('initRaceCourseCoinMarkers','renderRacePickupBase','func_800625D8'):
    row=rows[name]; source=row['source']
    repo=NATIVE/'baseline-reviewed-builds'/name; ws=repo/'nonmatchings'/name
    parent=conn.execute('SELECT a.id FROM attempts a JOIN functions f ON a.func_addr=f.addr WHERE f.name=? ORDER BY a.id DESC',(name,)).fetchone()[0]
    for owner in ('global_fields','stack_arrays','frontend_casts'):
        ctx=dict(candidate=source,function=name,repo=str(repo),workspace=str(ws),target=row['target'],target_asm_path=str(ws/'target.s'))
        proposal=RUNNERS['eval.intake_runners.'+owner](ctx,{'allow_partial':True})
        if not proposal.get('changed'):
            results.append(dict(function=name,owner=owner,changed=False,reason=proposal.get('reason')));continue
        source=proposal['source']
        att=workspace.score(ws,repo,'fields_pilot_'+owner,source,conn=conn,func=name,
            strategy='clean-fields:pilot:'+owner,run_id='clean-fields-pilot',model='zero-model',parent_attempt_id=parent,
            relation='repair',action=owner,extra=proposal.get('detail'))
        parent=att.receipt_id
        front=frontend_diagnostics.analyse(source,repo=repo,target=row['target'],full_diagnostics=True)
        results.append(dict(function=name,owner=owner,changed=True,compiled=att.compiled,exact=att.exact,
            score=att.score,errors=front['error_count'],attempt_id=parent))
        print(json.dumps(results[-1]),flush=True)
    if name=='initRaceCourseCoinMarkers':
        # Start from the original source and exercise the campaign's actual entry.
        base=workspace.score(ws,repo,'fields_campaign_base',row['source'],conn=conn,func=name,
            strategy='clean-fields:campaign-base',run_id='clean-fields-pilot',model='zero-model')
        result=modelrepair.search(repo,name,row['source'],ws,model='zero-model',endpoint='none',conn=conn,
            base_attempt=base,resilient=True,max_calls=0,compile_only=True,run_id='clean-fields-campaign-pilot')
        results.append(dict(function=name,owner='modelrepair.search',compiled=result.best_attempt.compiled,
            frontend=result.best_attempt.frontend.get('passed'),exact=result.best_attempt.exact,
            score=result.best_attempt.score,normalization_candidates=result.normalization_candidates,log=result.log))
        print(json.dumps({k:v for k,v in results[-1].items() if k!='log'}),flush=True)
(OUT/'pilot.json').write_text(json.dumps(results,indent=2)+'\n')
