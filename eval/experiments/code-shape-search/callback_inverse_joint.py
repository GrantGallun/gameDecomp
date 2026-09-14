import json, sqlite3, sys, time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace, callback_joint_search
name='createCallbackTaskPreservingArgs'
source=(ROOT/'eval/results/callback-finish-final'/f'{name}.c').read_text()
out=ROOT/'eval/results/callback-inverse-joint-v1'
variants=callback_joint_search.candidates(source,name)
if '--compose' in sys.argv:
    out=ROOT/'eval/results/callback-inverse-joint-v2'
    best=(ROOT/'eval/results/callback-inverse-structure-v1/07.c').read_text()
    old_loop=source[source.index('    if (gCallbackTaskActiveListSentinel.next != NULL)'):source.index('    /* Insert newTask')]
    new_loop=best[best.index('    while (insertAfter->next != NULL)'):best.index('    /* Insert newTask')]
    from solver.principle_variants import Variant
    variants=[Variant(v.label,v.source.replace(old_loop,new_loop)) for v in variants if ':direct:' in v.label and ':late-allocation' not in v.label]
out.mkdir(exist_ok=True)
repo=Path('/home/grant/decomp/sbk1')
ws=workspace.bootstrap(repo,name)
conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
rows=[]
for i,v in enumerate(variants):
    tag=f'{name}_joint_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,v.source,conn=conn,func=name,strategy='callback-joint',action=v.label)
    rows.append(dict(label=v.label,tag=tag,**asdict(a)))
    (out/f'{i:02d}.c').write_text(v.source)
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(i,v.label,a.score,a.exact,flush=True)
    if a.score>97.454: print('NEW BEST',i,a.diff,flush=True)
