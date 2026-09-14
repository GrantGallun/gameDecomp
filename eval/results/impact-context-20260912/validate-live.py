"""Validate an immutable snapshot plus current service health after deployment."""
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
RUN=OUT.parent/'resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import campaign_state, frozen_wavefront

deployment=json.loads((OUT/'deployment.json').read_bytes())
amendment=json.loads((Path(deployment['revision'])/'amendment.json').read_bytes())
pointer=json.loads((RUN/'campaign.json').read_bytes())
pointer['store']=str(RUN/pointer['store'])
campaign_state.atomic(OUT/'validation-checkpoint.json',pointer)
state=campaign_state.read(OUT/'validation-checkpoint.json')
frozen_wavefront.verify_files(state['pins'])
service=json.loads((RUN/'service.json').read_bytes())
before=amendment['prior_metrics']['repair_yield']['totals']
now=state['fast_metrics']['repair_yield']['totals']
result={
    'checked_at':time.time(), 'checkpoint':pointer['commit'], 'service':service,
    'pins_verified':len(state['pins']), 'summary':state['summary'],
    'repair_yield':state['fast_metrics']['repair_yield'],
    'new_work_items':now.get('work_items',0)-before.get('work_items',0),
    'new_inference_seconds':now.get('inference_seconds',0)-before.get('inference_seconds',0),
    'checks':{
        'running':service['status']=='running' and bool(service.get('worker_pid')),
        'fresh_heartbeat':time.time()-service.get('heartbeat_at',0)<30,
        'advanced':pointer['commit']>deployment['checkpoint'],
        'imported_new_work':now.get('work_items',0)>before.get('work_items',0),
        'inference_recorded':now.get('inference_seconds',0)>before.get('inference_seconds',0),
    },
}
campaign_state.atomic(OUT/'live-validation.json',result)
print(json.dumps(result))
