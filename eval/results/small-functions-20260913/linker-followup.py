"""Retain failed seven-way proof; verify six unchanged, then a compiler-diagnosed extern alternative."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import integration_gate, prepare_integration
from solver import workspace

ROOT=Path('/mnt/c/Code/gameDecomp')
BASE=ROOT/'eval/results/small-functions-20260913/linker-union-1789318026368143892'
OUT=BASE/('followup-'+str(time.time_ns()))
OUT.mkdir()
report=json.loads((BASE/'report.json').read_bytes())
names=report['prior_integrated']+report['selected']
entries=[]
for i,name in enumerate(names,1):
    att=json.loads((BASE/name/'compile.json').read_bytes())
    entries.append({'function':name,'source':str(BASE/name/'selected.c'),'attempt_id':i,'verification':att['verification']})
result={'checkpoint':report['checkpoint'],'original_report_sha256':hashlib.sha256((BASE/'report.json').read_bytes()).hexdigest(),
        'diagnostic_log_sha256':hashlib.sha256((BASE/'integration.build.log').read_bytes()).hexdigest(),
        'diagnostic':'full-TU Clang: gFramebufferSwapHold redeclaration s8 versus prior u8',
        'reference_body_used_for_inference':False,'live_mutated':False}
manifest=prepare_integration.prepare(repo=Path('/home/grant/decomp/sbk1'),db=BASE/'source-bindings.sqlite',entries=entries[:-1],output_dir=OUT/'six-prepared')
result['unchanged_six']=integration_gate.run(repo=Path('/home/grant/decomp/sbk1'),manifest=manifest,output=OUT/'six-integration.json')
(OUT/'report.json').write_text(json.dumps(result,indent=2))
print('unchanged six: '+result['unchanged_six']['status'],flush=True)

name=names[-1]
assert name=='fadeOutMultiplayerCourseSelectMenu'
original=(BASE/name/'selected.c').read_text()
assert original.count('extern s8 gFramebufferSwapHold;')==1
source=original.replace('extern s8 gFramebufferSwapHold;','extern u8 gFramebufferSwapHold;')
source_path=OUT/'unsigned-extern.c'
source_path.write_text(source)
ws=Path(report['functions'][name]['workspace'])
isolated=ws.parent.parent
with closing(sqlite3.connect(BASE/name/'attempts.sqlite')) as conn:
    attempt=workspace.score(ws,isolated,name+'_unsigned_extern_'+str(time.time_ns()),source,
        conn=conn,func=name,strategy='integration-frontend-extern-type',model='zero-model',
        run_id='integration-extern-'+str(time.time_ns()),parent_attempt_id=report['functions'][name]['private_attempt_id'],
        relation='compiler-diagnostic-repair',action='preserve extern; use type required by full-TU Clang conflict',
        extra={'source_sha256':hashlib.sha256(original.encode()).hexdigest(),'diagnostic_log_sha256':result['diagnostic_log_sha256']})
(OUT/'unsigned-extern-compile.json').write_text(json.dumps(asdict(attempt),indent=2))
result['alternative']={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'private_attempt_id':attempt.receipt_id,
    'private_db':str(BASE/name/'attempts.sqlite'),'score':attempt.score,'frontend_passed':attempt.frontend.get('passed'),
    'function_exact':(attempt.verification or {}).get('function_boundary',{}).get('function_exact')}
if not result['alternative']['function_exact'] or not result['alternative']['frontend_passed']:
    raise ValueError('compiler-diagnosed alternative did not certify')
entries[-1]={'function':name,'source':str(source_path),'attempt_id':len(entries),'verification':attempt.verification}
newdb=OUT/'source-bindings.sqlite'
with closing(sqlite3.connect((BASE/'source-bindings.sqlite').as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(newdb)) as new:
    old.backup(new)
    new.execute('UPDATE attempts SET source_code=? WHERE id=?',(source,len(entries)))
    new.commit()
manifest=prepare_integration.prepare(repo=Path('/home/grant/decomp/sbk1'),db=newdb,entries=entries,output_dir=OUT/'seven-prepared')
result['corrected_seven']=integration_gate.run(repo=Path('/home/grant/decomp/sbk1'),manifest=manifest,output=OUT/'seven-integration.json')
(OUT/'report.json').write_text(json.dumps(result,indent=2))
print('corrected seven: '+result['corrected_seven']['status'],flush=True)
print(str(OUT/'report.json'),flush=True)
