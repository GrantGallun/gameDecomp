"""Full seven-function ROM proof with two explicitly logged frontend-diagnosed alternatives."""
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
PREVIOUS=BASE/'followup-1789318187711780903'
OUT=BASE/('sentinel-'+str(time.time_ns()))
OUT.mkdir()
report=json.loads((BASE/'report.json').read_bytes())
prior=json.loads((PREVIOUS/'report.json').read_bytes())
repo=Path('/home/grant/decomp/sbk1')
original=(BASE/'osCreateMesgQueue/selected.c').read_text()
assert original.count('extern OSThread __osThreadTail;')==1 and original.count('&__osThreadTail')==2
source=original.replace('extern OSThread __osThreadTail;','#include "PRinternal/osint.h"').replace('&__osThreadTail','(OSThread *)&__osThreadTail')
path=OUT/'sentinel-view.c'
path.write_text(source)
name='osCreateMesgQueue'
ws=Path(report['functions'][name]['workspace'])
with closing(sqlite3.connect(BASE/name/'attempts.sqlite')) as conn:
    att=workspace.score(ws,ws.parent.parent,name+'_sentinel_view_'+str(time.time_ns()),source,conn=conn,func=name,
        strategy='integration-frontend-sentinel-view',model='zero-model',run_id='sentinel-view-'+str(time.time_ns()),
        parent_attempt_id=report['functions'][name]['private_attempt_id'],relation='compiler-diagnostic-repair',
        action='use SDK-declared two-field sentinel object and explicit OSThread pointer view',
        extra={'source_sha256':hashlib.sha256(original.encode()).hexdigest(),
               'diagnostic_log_sha256':hashlib.sha256((PREVIOUS/'six-integration.build.log').read_bytes()).hexdigest(),
               'sdk_header_sha256':hashlib.sha256((repo/'include/PRinternal/osint.h').read_bytes()).hexdigest(),
               'reference_body_used_for_inference':False})
(OUT/'sentinel-view-compile.json').write_text(json.dumps(asdict(att),indent=2))
result={'checkpoint':report['checkpoint'],'source_alternatives':{
    name:{'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'private_attempt_id':att.receipt_id,
          'private_db':str(BASE/name/'attempts.sqlite'),'score':att.score,'frontend_passed':att.frontend.get('passed'),
          'function_exact':(att.verification or {}).get('function_boundary',{}).get('function_exact')},
    'fadeOutMultiplayerCourseSelectMenu':prior['alternative']},'live_mutated':False,'reference_body_used_for_inference':False}
assert result['source_alternatives'][name]['function_exact'] and att.frontend.get('passed')
db=OUT/'source-bindings.sqlite'
with closing(sqlite3.connect((PREVIOUS/'source-bindings.sqlite').as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(db)) as new:
    old.backup(new)
    new.execute('UPDATE attempts SET source_code=? WHERE id=?',(source,6))
    new.commit()
entries=[]
for i,function in enumerate(report['prior_integrated']+report['selected'],1):
    if function==name:
        sourcepath=path
        certificate=att.verification
    elif function=='fadeOutMultiplayerCourseSelectMenu':
        sourcepath=PREVIOUS/'unsigned-extern.c'
        certificate=json.loads((PREVIOUS/'unsigned-extern-compile.json').read_bytes())['verification']
    else:
        sourcepath=BASE/function/'selected.c'
        certificate=json.loads((BASE/function/'compile.json').read_bytes())['verification']
    entries.append({'function':function,'source':str(sourcepath),'attempt_id':i,'verification':certificate})
manifest=prepare_integration.prepare(repo=repo,db=db,entries=entries,output_dir=OUT/'prepared')
result['manifest']=str(manifest)
result['integration']=integration_gate.run(repo=repo,manifest=manifest,output=OUT/'seven-integration.json')
(OUT/'report.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2),flush=True)
