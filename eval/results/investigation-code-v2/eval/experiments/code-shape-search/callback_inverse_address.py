import json,sqlite3,sys,time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from solver import workspace
from solver.callback_address_hypotheses import probes,candidates
from eval import semantic_stress_pilot as stress
repo=Path('/home/grant/decomp/sbk1');function='createCallbackTaskPreservingArgs'
out=ROOT/'eval/results/callback-inverse-address-v1';out.mkdir(exist_ok=True)
source=(ROOT/f'eval/results/callback-finish-final/{function}.c').read_text()
ws=workspace.bootstrap(repo,function);db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
conn=sqlite3.connect(db,timeout=120);rows=[]
for index,(kind,v) in enumerate([('probe',v) for v in probes(source,function)]+[('candidate',v) for v in candidates(source,function)]):
 tag=f'{function}_inverse_address_{time.time_ns()}'
 a=workspace.score(ws,repo,tag,v.source,conn=conn,func=function,strategy='callback-inverse-address',model='',action=v.label,run_kind='callback-inverse-address')
 assembly=(ws/f'{tag}_object_dump_normalized.s').read_text() if a.compiled else ''
 (out/f'{index:02d}.c').write_text(v.source);(out/f'{index:02d}.s').write_text(assembly)
 rows.append({'kind':kind,'label':v.label,'attempt':asdict(a)})
 (out/'scores.json').write_text(json.dumps(rows,indent=2))
 print(index,kind,v.label,a.score,a.exact,flush=True)
conn.close()
chosen=[out/f'{i:02d}.c' for i,r in enumerate(rows) if r['kind']=='candidate' and r['attempt']['score']>97.454]
if chosen:
 cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
 r=stress.run(repo=repo,db=db,census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function=function,candidate_paths=tuple(chosen[:3]),panel_cases=stress._cases_from_rows(cases))
 for c in r['candidates']:print('REPLAY',c['candidate_path'],c['attempt']['score'],c['differential']['all'],flush=True)
