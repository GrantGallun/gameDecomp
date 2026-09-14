import json, sqlite3, sys,time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
name='createCallbackTaskPreservingArgs'
source=(ROOT/'eval/results/callback-inverse-structure-v1/07.c').read_text()
out=ROOT/'eval/results/callback-inverse-joint-v3'
out.mkdir(exist_ok=True)
start=source.index('    gFreeCallbackTaskCount--;')
end=source.index('    while (insertAfter->next != NULL)',start)
block=source[start:end]
options=[]
for typ in ('u16','u32','s32'):
    replacement='    { '+typ+' idx;\n'+block+'    }\n'
    options.append(('block:'+typ,(source[:start]+replacement+source[end:]).replace('    u16 idx;\n','',1)))
for expression in ('(u16)(gFreeCallbackTaskCount -= 1)','(u16)--gFreeCallbackTaskCount','(u32)(u16)(gFreeCallbackTaskCount -= 1)'):
    replacement='    insertAfter = &gCallbackTaskActiveListSentinel;\n    newTask = gFreeCallbackTaskPool['+expression+'];\n'
    options.append(('cast-chain:'+expression,(source[:start]+replacement+source[end:]).replace('    u16 idx;\n','',1)))
repo=Path('/home/grant/decomp/sbk1')
ws=workspace.bootstrap(repo,name)
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
rows=[]
for i,(label,code) in enumerate(options):
    tag=f'{name}_jointscope_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,code,conn=conn,func=name,strategy='callback-joint-scope',action=label)
    rows.append(dict(label=label,tag=tag,**asdict(a)))
    (out/f'{i:02d}.c').write_text(code)
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(i,label,a.score,a.exact,flush=True)
