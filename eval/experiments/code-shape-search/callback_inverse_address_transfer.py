import json,sqlite3,sys,time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from solver import workspace
from eval import semantic_stress_pilot as stress
repo=Path('/home/grant/decomp/sbk1');function='createCallbackTaskPreservingArgs'
out=ROOT/'eval/results/callback-inverse-address-v2';out.mkdir(exist_ok=True)
old=(ROOT/f'eval/results/callback-finish-final/{function}.c').read_text()
new=(ROOT/'eval/results/callback-inverse-structure-v1/07.c').read_text()
forms=[('wide','(CallbackTask *)(u32)(u64)(u32)BASE'),('signed-wide','(CallbackTask *)(s32)(s64)(s32)BASE'),('offset','(CallbackTask *)((u32)BASE + 0x10000U - 0x10000U)'),('xor','(CallbackTask *)(((u32)BASE ^ 0x80000000U) ^ 0x80000000U)')]
options=[]
for label,expr in forms:
 globalexpr=expr.replace('BASE','&gCallbackTaskActiveListSentinel')
 pointerexpr=expr.replace('BASE','insertAfter')
 options.append(('old-global-load-'+label,old.replace('cur = insertAfter->next;',f'cur=({globalexpr})->next;')))
 options.append(('old-initial-address-'+label,old.replace('insertAfter = &gCallbackTaskActiveListSentinel;',f'insertAfter = {globalexpr};')))
 options.append(('new-initial-address-'+label,new.replace('insertAfter = &gCallbackTaskActiveListSentinel;',f'insertAfter = {globalexpr};')))
 options.append(('new-guard-access-'+label,new.replace('while (insertAfter->next != NULL)',f'while (({pointerexpr})->next != NULL)')))
 options.append(('new-loop-access-'+label,new.replace('        if (insertAfter->next->priority',f'        if (({pointerexpr})->next->priority')))
 options.append(('new-update-access-'+label,new.replace('insertAfter = insertAfter->next;',f'insertAfter = ({pointerexpr})->next;')))
for label,base in [('global','&gCallbackTaskActiveListSentinel'),('pointer','insertAfter')]:
 union='{ union { CallbackTask *p; u32 n; } address; address.p='+base+';cur=((CallbackTask *)address.n)->next; }'
 options.append(('old-union-'+label,old.replace('cur = insertAfter->next;',union)))
 unioninit='{ union { CallbackTask *p; u32 n; } address; address.p=&gCallbackTaskActiveListSentinel;insertAfter=(CallbackTask *)address.n; }'
 options.append((label+'-union-init',(old if label=='global' else new).replace('insertAfter = &gCallbackTaskActiveListSentinel;',unioninit)))
ws=workspace.bootstrap(repo,function);db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
conn=sqlite3.connect(db,timeout=120);rows=[]
for index,(label,code) in enumerate(options):
 tag=f'{function}_address_transfer_{time.time_ns()}'
 a=workspace.score(ws,repo,tag,code,conn=conn,func=function,strategy='callback-address-transfer',model='',action=label,run_kind='callback-address-transfer')
 (out/f'{index:02d}.c').write_text(code)
 rows.append({'label':label,'attempt':asdict(a)});(out/'scores.json').write_text(json.dumps(rows,indent=2))
 print(index,label,a.score,a.exact,flush=True)
conn.close()
chosen=sorted(range(len(rows)),key=lambda i:rows[i]['attempt']['score'],reverse=True)
chosen=[out/f'{i:02d}.c' for i in chosen[:3] if rows[i]['attempt']['score']>98.511]
if chosen:
 cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
 r=stress.run(repo=repo,db=db,census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function=function,candidate_paths=tuple(chosen),panel_cases=stress._cases_from_rows(cases))
 for c in r['candidates']:print('REPLAY',c['candidate_path'],c['attempt']['score'],c['differential']['all'],flush=True)
