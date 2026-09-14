"""Actual selected-C semantic comparison before/after ROM readonly memory admission."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import campaign_runtime,campaign_state,semantic_lane
from solver import data_memory

ROOT=Path('/mnt/c/Code/gameDecomp')
RUN=ROOT/'eval/results/resume-pipeline-20260908'
BASE=ROOT/'eval/results/data-mechanism-20260913'
OUT=BASE/('memory-pilot-'+str(time.time_ns()))
OUT.mkdir()
context_path=next((BASE/'pilot/binary-data').glob('*.json'))
contexts=json.loads(context_path.read_bytes())['memory']
raw=(RUN/'campaign.json').read_bytes()
pointer=json.loads(raw)
with closing(sqlite3.connect((RUN/pointer['store']).as_uri()+'?mode=ro',uri=True)) as conn:
    state=campaign_state._hydrate(conn,pointer)
(OUT/'pointer.json').write_bytes(raw)
function='__sinf'
node=state['nodes'][function]
report={'function':function,'checkpoint':pointer['commit'],'source_binding':campaign_runtime.binding(node),
        'context_sha256':contexts[function]['sha256'],'context_artifact_sha256':hashlib.sha256(context_path.read_bytes()).hexdigest(),
        'budgets':{'semantic_cases':64,'max_steps':10000,'exploration_cases':5000,'model_calls':0},
        'live_mutated':False,'modules':{str(Path(m.__file__)):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (semantic_lane,data_memory)}}
try:
    compiled=campaign_runtime.compile_candidate(repo=Path('/home/grant/decomp/sbk1'),db=Path(state['config']['db']),function=function,node=node,folder=OUT)
    isolated,ws,candidate,_,_=compiled
    report['candidate']={'score':candidate.attempt.score,'compiled':candidate.attempt.compiled,'exact':candidate.attempt.exact,
                         'private_attempt_id':candidate.attempt.receipt_id,'workspace':str(ws)}
    for label,memory in [('before',None),('after',contexts[function])]:
        started=time.monotonic()
        panel=semantic_lane.DeferredPanel(isolated,ws,function,64,10000,memory_context=memory)
        outcome=panel(candidate)
        entry={'elapsed_seconds':time.monotonic()-started,'outcome':outcome,'panel_report':panel.report}
        (OUT/(label+'.json')).write_text(json.dumps(entry,indent=2))
        report[label]={'seconds':entry['elapsed_seconds'],'status':(outcome or {}).get('status'),
                       'counts':(outcome or {}).get('counts'),'reason':(outcome or {}).get('reason'),
                       'panel_sha256':(outcome or {}).get('panel_sha256'),
                       'panel_report':{k:v for k,v in panel.report.items() if k in ('data_memory','status','counts','reason','total_cases','case_count','target_coverage','coverage')}}
        (OUT/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'phase':label,**report[label]}),flush=True)
except Exception as exc:
    report['error']=type(exc).__name__+': '+str(exc)
(OUT/'report.json').write_text(json.dumps(report,indent=2))
print(str(OUT/'report.json'),flush=True)
