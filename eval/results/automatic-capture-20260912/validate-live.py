"""Freeze and inspect the automatic capture result without altering the run."""
import hashlib
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import campaign_state, campaign_runtime, frozen_wavefront

deployment=json.loads((OUT/'deployment.json').read_bytes())
amendment=json.loads((Path(deployment['revision'])/'amendment.json').read_bytes())
for retry in range(5):
    try:
        pointer=json.loads((RUN/'campaign.json').read_bytes())
        break
    except (FileNotFoundError, PermissionError):
        # DrvFS can briefly hide the atomically replaced live pointer. Read one
        # complete pointer, then use its immutable checkpoint for every check.
        if retry==4:
            raise
        time.sleep(0.1)
pointer['store']=str(RUN/pointer['store'])
campaign_state.atomic(OUT/'validation-checkpoint.json',pointer)
state=campaign_state.read(OUT/'validation-checkpoint.json')
frozen_wavefront.verify_files(state['pins'])
service=json.loads((RUN/'service.json').read_bytes())
history=state.get('runtime_capture',{})
rows=history.get('results',{})
expected={'heap-base-first','heap-base-nonzero'}
binding_checks={key:row.get('source_binding')==campaign_runtime.binding(state['nodes'][row['function']])
                for key,row in rows.items()}
results={key:{k:row.get(k) for k in ('function','status','counts','capture_count','capture_sha256','error')}
         for key,row in rows.items()}
receipts={}
for key,row in rows.items():
    for artifact in row.get('artifacts',[]):
        if artifact['path'].endswith('/emulator/receipt.json'):
            path=RUN/artifact['path']
            assert path.resolve().is_relative_to(RUN.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest()==artifact['sha256']
            receipts[key]=json.loads(path.read_bytes())
checks={'running':service['status']=='running' and bool(service.get('worker_pid')),
        'fresh_heartbeat':time.time()-service.get('heartbeat_at',0)<30,
        'advanced':pointer['commit']>deployment['checkpoint'],
        'imported_new_work':state['fast_metrics']['completed_items']>amendment['prior_metrics']['completed_items'],
        'prior_exact_preserved':state['summary']['object_exact_or_integrated']>=amendment['prior_summary']['object_exact_or_integrated'],
        'both_plans_passed':expected<=rows.keys() and all(rows[k]['status']=='passed' and rows[k]['counts']['passed']==1 for k in expected),
        'source_bindings_current':bool(binding_checks) and all(binding_checks.values()),
        'plan_hash_current':state['config'].get('runtime_capture_plan_sha256')==hashlib.sha256((OUT/'capture-plans.json').read_bytes()).hexdigest(),
        'captures_available_to_workers':len(state['config'].get('runtime_captures',{}).get('getRelocatableHeapBlockBase',[]))==2,
        'fresh_muted_emulators_cleaned_up':expected<=receipts.keys() and all(
            receipts[k]['status']=='captured' and receipts[k]['muted'] is True
            and receipts[k]['cleanup']['exited'] is True
            and receipts[k]['started_unix']>amendment['applied_at']-10 for k in expected)}
result={'checkpoint':pointer['commit'],'checked_at':time.time(),'summary':state['summary'],
        'pins_verified':len(state['pins']),'runtime_results':results,'checks':checks,
        'new_work_items':state['fast_metrics']['completed_items']-amendment['prior_metrics']['completed_items'],
        'service':{k:service.get(k) for k in ('status','pid','worker_pid','heartbeat_at')},
        'raw_runtime_metadata':history,'emulator_receipts':receipts}
campaign_state.atomic(OUT/'live-validation.json',result)
print(json.dumps({k:v for k,v in result.items() if k!='raw_runtime_metadata'}))
