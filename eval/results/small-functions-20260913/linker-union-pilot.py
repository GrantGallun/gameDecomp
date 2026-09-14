"""Recompile and whole-ROM verify prior integrated union plus two newly admitted boundaries."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import campaign_runtime, campaign_state, integration_gate, prepare_integration
from solver import function_boundary

ROOT=Path('/mnt/c/Code/gameDecomp')
RUN=ROOT/'eval/results/resume-pipeline-20260908'
REPO=Path('/home/grant/decomp/sbk1')
OUT=ROOT/'eval/results/small-functions-20260913'/('linker-union-'+str(time.time_ns()))
OUT.mkdir()
raw=(RUN/'campaign.json').read_bytes()
pointer=json.loads(raw)
with closing(sqlite3.connect((RUN/pointer['store']).as_uri()+'?mode=ro',uri=True)) as conn:
    state=campaign_state._hydrate(conn,pointer)
(OUT/'pointer.json').write_bytes(raw)
prior=sorted(name for name,n in state['nodes'].items() if n['status']=='integrated')
assert len(prior)==5,prior
selected=['osCreateMesgQueue','fadeOutMultiplayerCourseSelectMenu']
report={'checkpoint':pointer['commit'],'pointer_sha256':hashlib.sha256(raw).hexdigest(),
        'prior_integrated':prior,'selected':selected,'live_mutated':False,
        'modules':{str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                   for m in (prepare_integration,function_boundary)},'functions':{}}
entries=[]
union_db=OUT/'source-bindings.sqlite'
with closing(sqlite3.connect(union_db)) as conn,conn:
    conn.executescript('CREATE TABLE tus(id INTEGER PRIMARY KEY,name TEXT); CREATE TABLE functions(addr INTEGER PRIMARY KEY,name TEXT,size INTEGER,tu_id INTEGER); CREATE TABLE attempts(id INTEGER PRIMARY KEY,func_addr INTEGER,source_code TEXT);')
for index,name in enumerate(prior+selected,1):
    node=state['nodes'][name]
    folder=OUT/name
    folder.mkdir()
    item={'source_binding':campaign_runtime.binding(node),'source':node['source'],'previous_status':node['status']}
    report['functions'][name]=item
    try:
        isolated,ws,candidate,_,_=campaign_runtime.compile_candidate(repo=REPO,db=Path(state['config']['db']),function=name,node=node,folder=folder)
        att=candidate.attempt
        certificate=att.verification or {}
        boundary=certificate.get('function_boundary') or {}
        item.update(score=att.score,object_exact=att.exact,frontend_passed=att.frontend.get('passed'),
                    function_exact=boundary.get('function_exact'),private_attempt_id=att.receipt_id,
                    private_attempt_database=str(folder/'attempts.sqlite'),source_binding_row=index,
                    workspace=str(ws))
        with closing(sqlite3.connect((folder/'attempts.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
            meta=source.execute('SELECT f.addr,f.size,t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?',(name,)).fetchone()
        with closing(sqlite3.connect(union_db)) as conn,conn:
            conn.execute('INSERT INTO tus VALUES(?,?)',(index,meta[2]))
            conn.execute('INSERT INTO functions VALUES(?,?,?,?)',(meta[0],name,meta[1],index))
            conn.execute('INSERT INTO attempts VALUES(?,?,?)',(index,meta[0],candidate.source))
        entries.append({'function':name,'source':str(folder/'selected.c'),'attempt_id':index,'verification':certificate})
    except Exception as exc:
        item['error']=type(exc).__name__+': '+str(exc)
    (OUT/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'function':name,**{k:item.get(k) for k in ('score','object_exact','function_exact','frontend_passed','error')}}),flush=True)
assert len(entries)==len(prior)+len(selected),'Fresh union source not fully verified'
try:
    manifest=prepare_integration.prepare(repo=REPO,db=union_db,entries=entries,output_dir=OUT/'prepared')
    report['manifest']=str(manifest)
    report['integration']=integration_gate.run(repo=REPO,manifest=manifest,output=OUT/'integration.json')
except Exception as exc:
    report['integration']={'status':'preparation_failed','error':type(exc).__name__+': '+str(exc)}
(OUT/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report['integration']),flush=True)
print(str(OUT/'report.json'),flush=True)
