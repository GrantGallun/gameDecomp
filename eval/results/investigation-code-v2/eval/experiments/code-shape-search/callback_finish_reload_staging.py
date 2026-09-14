"""Focus on the remaining sentinel address materialization instruction."""
import json, sqlite3, sys, time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
from eval import semantic_stress_pilot as stress

repo=Path('/home/grant/decomp/sbk1')
function='createCallbackTaskPreservingArgs'
out=ROOT/'eval/results/callback-finish-reload-v2';out.mkdir(exist_ok=True)
source=(ROOT/'eval/results/callback-finish-reload-v1/10.c').read_text()
replacement='cur = insertAfter->next;'
shapes=[
 ('cur-base','cur = insertAfter; cur = cur->next;'),
 ('head-base','{ CallbackTask *headBase; headBase = insertAfter; cur = headBase->next; }'),
 ('head-base-register','{ register CallbackTask *headBase; headBase = insertAfter; cur = headBase->next; }'),
 ('head-base-int','{ u32 headBase; headBase = (u32)insertAfter; cur = ((CallbackTask *)headBase)->next; }'),
 ('head-base-signed','{ s32 headBase; headBase = (s32)insertAfter; cur = ((CallbackTask *)headBase)->next; }'),
 ('int-access','cur = (CallbackTask *)*(u32 *)((u32)insertAfter + 4);'),
 ('pointer-access','cur = *(CallbackTask **)((u8 *)insertAfter + 4);'),
 ('array-access','cur = ((CallbackTask **)insertAfter)[1];'),
 ('cast-struct','cur = ((CallbackTask *)insertAfter)->next;'),
 ('volatile-access','cur = ((volatile CallbackTask *)insertAfter)->next;'),
 ('cast-pointer','cur = *(CallbackTask * volatile *)((u8 *)insertAfter + 4);'),
 ('base-register-return','{ register CallbackTask *headBase; headBase = insertAfter; cur = headBase->next; insertAfter = headBase; }'),
 ('base-return','{ CallbackTask *headBase; headBase = insertAfter; cur = headBase->next; insertAfter = headBase; }'),
 ('cur-offset','cur = (CallbackTask *)((u8 *)insertAfter + 4); cur = *(CallbackTask **)cur;'),
 ('word-offset','cur = (CallbackTask *)((u32 *)insertAfter + 1); cur = *(CallbackTask **)cur;'),
 ('comma','cur = (cur = insertAfter, cur->next);'),
 ('array-base','{ CallbackTask **headBase; headBase = (CallbackTask **)insertAfter; cur = headBase[1]; }'),
 ('integer-comma','cur = (CallbackTask *)(cur = insertAfter, *(s32 *)((u8 *)cur + 4));'),
 ('pointer-member-address','cur = *(&insertAfter->next);'),
 ('const-access','cur = ((const CallbackTask *)insertAfter)->next;'),
]
ws=workspace.bootstrap(repo,function)
db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
conn=sqlite3.connect(db,timeout=120);rows=[]
for index,(label,body) in enumerate(shapes):
    code=source.replace(replacement,body,1)
    tag=f'{function}_reload_stage_{time.time_ns()}'
    a=workspace.score(ws,repo,tag,code,conn=conn,func=function,strategy='callback-reload-staging',model='',action=label,run_kind='callback-reload-staging')
    (out/f'{index:02d}.c').write_text(code)
    rows.append({'label':label,'attempt':asdict(a)})
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(index,label,a.score,a.exact,flush=True)
conn.close()
chosen=sorted(range(len(rows)),key=lambda i:rows[i]['attempt']['score'],reverse=True)
chosen=[out/f'{i:02d}.c' for i in chosen[:2] if rows[i]['attempt']['score']>97.454]
if chosen:
    cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
    r=stress.run(repo=repo,db=db,census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function=function,candidate_paths=tuple(chosen),panel_cases=stress._cases_from_rows(cases))
    for c in r['candidates']:print('REPLAY',c['candidate_path'],c['attempt']['score'],c['differential']['all'],flush=True)
