"""Paired actual compiler/semantic replay, using two explicitly selected receipt sources."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from eval import agentrepair, campaign_workers
from solver import modelrepair

parser=argparse.ArgumentParser()
parser.add_argument('--label',required=True)
args=parser.parse_args()
ROOT=Path('/mnt/c/Code/gameDecomp')
OUT=ROOT/'eval/results/zero-gain-20260913'
folder=OUT/('replay-'+args.label+'-'+str(time.time_ns()))
folder.mkdir()
function='initCoursePreviewCloseSparkles'
live=ROOT/'eval/results/resume-pipeline-20260908/campaign.sqlite'
with closing(sqlite3.connect(live.as_uri()+'?mode=ro',uri=True,timeout=30)) as conn:
    conn.row_factory=sqlite3.Row
    metadata=dict(conn.execute('SELECT f.addr,f.size,t.name AS tu FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?',(function,)).fetchone())
    sources=[dict(conn.execute('SELECT id,source_code,source_sha256,score,compiled FROM attempts WHERE id=?',(i,)).fetchone()) for i in (90593,90594)]
db=folder/'attempts.sqlite'
with closing(sqlite3.connect(db)) as conn,conn:
    conn.executescript((Path(modelrepair.__file__).resolve().parents[1]/'kb/schema.sql').read_text())
    conn.execute('INSERT INTO tus(id,name) VALUES(1,?)',(metadata['tu'],))
    conn.execute('INSERT INTO functions(addr,name,size,tu_id) VALUES(?,?,?,1)',(metadata['addr'],function,metadata['size']))
    for source in sources:
        conn.execute('INSERT INTO attempts(id,func_addr,iteration,source_code,source_sha256,score,compiled,strategy,created_at) VALUES(?,?,0,?,?,?,?,?,?)',
                     (source['id'],metadata['addr'],source['source_code'],source['source_sha256'],source['score'],source['compiled'],'read-only-selected-replay-seed',int(time.time())))
native=Path(tempfile.mkdtemp(prefix='decomp-zero-gain-'))
repo=campaign_workers.isolate(Path('/home/grant/decomp/sbk1'),native/'game',function)
modules={str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (modelrepair,agentrepair)}
inputs={'function':function,'baseline_source':sources[0]['source_sha256'],'retained_source':sources[1]['source_sha256'],
        'canonical_seed_attempts':[s['id'] for s in sources],'modules':modules,'native_workspace':str(native),
        'deterministic_budget':32,'model_calls':0,'semantic_cases':64,'semantic_steps':10000}
(folder/'inputs.json').write_text(json.dumps(inputs,indent=2))
started=time.monotonic()
result=agentrepair.run(repo=repo,db=db,function=function,source=sources[0]['source_code'],source_parent_attempt_id=sources[0]['id'],
    out=folder/'repair.json',best_source_out=folder/'best.c',model='zero-model',endpoint='http://127.0.0.1:1',
    draws=1,depth=4,beam=3,max_calls=0,timeout=240,think='low',num_thread=12,temperature=.35,num_predict=6000,
    seed=20260904,cache_dir=None,verbose=False,retained_frontier=({'attempt_id':sources[1]['id'],'source_sha256':sources[1]['source_sha256']},),
    deterministic_budget=32,deterministic_depth=2,structured_output=True,retry_invalid=True,include_header_context=True,resilient=True)
payload=result['result']
summary={**inputs,'label':args.label,'seconds':time.monotonic()-started,
         'selected_source_sha256':payload['best_source_sha256'],
         'preserved_source':payload['best_source_sha256']==sources[0]['source_sha256'],
         'score':payload['best_residual']['weighted_progress_score'],
         'best_score_improved':payload['best_score_improved'],
         'calls_attempted':payload['calls_attempted'],
         'semantic':payload.get('semantic_validation'),'exact':payload.get('exact'),
         'result':str(folder/'repair.json')}
(folder/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
