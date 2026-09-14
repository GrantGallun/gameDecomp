"""Production-compiler head-reload experiments in an isolated attempt DB."""
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import workspace
from solver.callback_reload_alternatives import candidates
from eval import semantic_stress_pilot as stress

def main():
    out=ROOT/'eval/results/callback-finish-reload-v1'
    out.mkdir(exist_ok=True)
    repo=Path('/home/grant/decomp/sbk1')
    function='createCallbackTaskPreservingArgs'
    source=(ROOT/f'eval/results/swarm-final/{function}.c').read_text()
    ws=workspace.bootstrap(repo,function)
    db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
    conn=sqlite3.connect(db,timeout=120)
    rows=[]
    for index,v in enumerate(candidates(source,function,60)):
        tag=f'{function}_reload_{time.time_ns()}'
        a=workspace.score(ws,repo,tag,v.source,conn=conn,func=function,
            strategy='callback-reload',model='',action=v.label,run_kind='callback-reload')
        (out/f'{index:02d}.c').write_text(v.source)
        rows.append({'label':v.label,'attempt':asdict(a)})
        (out/'scores.json').write_text(json.dumps(rows,indent=2))
        print(index,v.label,a.score,a.exact,flush=True)
    conn.close()
    best=sorted(range(len(rows)),key=lambda i:rows[i]['attempt']['score'],reverse=True)
    chosen=[];seen=set()
    for i in best:
        a=rows[i]['attempt']
        if a['score']<=94.723:break
        if a['diff'] in seen:continue
        seen.add(a['diff']);chosen.append(out/f'{i:02d}.c')
        if len(chosen)>=3:break
    if chosen:
        cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
        receipt=stress.run(repo=repo,db=db,census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function=function,candidate_paths=tuple(chosen),panel_cases=stress._cases_from_rows(cases))
        for row in receipt['candidates']:
            print('REPLAY',row['candidate_path'],row['attempt']['score'],row['differential']['all'],flush=True)

if __name__=='__main__':main()
