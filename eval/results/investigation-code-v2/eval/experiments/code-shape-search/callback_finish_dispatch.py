import json, sqlite3, sys, time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
function='createCallbackTaskPreservingArgs'
source=(ROOT/'eval/results/swarm-final'/f'{function}.c').read_text()
out=ROOT/'eval/results/callback-finish-dispatch-v1'
if '--compose' in sys.argv or '--reuse' in sys.argv:
    source=(ROOT/'eval/results/callback-finish-reload-v1/10.c').read_text()
    out=ROOT/'eval/results/callback-finish-dispatch-v2'
if '--reuse' in sys.argv:
    out=ROOT/'eval/results/callback-finish-dispatch-v3'
out.mkdir(exist_ok=True)
options=[('baseline',source)]
if '--baseline' not in sys.argv:
    from solver.callback_dispatch_alternatives import candidates
    variants=candidates(source,function,60)
    if '--compose' in sys.argv:
        variants=[v for v in variants if any(x in v.label for x in ('inline-index','idx-type','inline-selector','selector-type','scope:'))]
    options += [(v.label,v.source) for v in variants]
if '--reuse' in sys.argv:
    options=[]
    import re
    for typ in ('u16','u32','s32'):
        for cast in (True,False):
            code=source.replace('u16 idx;','').replace('u8 t8;',typ+' t8;').replace('t8 = type;','t8 = (u8)type;')
            code=re.sub(r'\bidx\b','t8',code)
            if cast: code=code.replace('gFreeCallbackTaskPool[t8]','gFreeCallbackTaskPool[(u16)t8]')
            options.append((f'reuse-selector-index:{typ}:{cast}',code))
repo=Path('/home/grant/decomp/sbk1')
ws=workspace.bootstrap(repo,function)
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
rows=[]
for i,(label,code) in enumerate(options):
    tag=f'{function}_dispatch_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,code,conn=conn,func=function,strategy='callback-finish-dispatch',action=label)
    rows.append(dict(label=label,tag=tag,**asdict(a)))
    (out/f'{i:02d}.c').write_text(code)
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(i,label,a.score,a.exact,flush=True)
    if '--baseline' in sys.argv: print(a.diff,flush=True)
