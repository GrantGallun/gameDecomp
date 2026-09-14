"""Record read-only post-amendment runtime checks without copying live history."""
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
RUN=OUT.parent/'resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import campaign_state, frozen_wavefront

state=campaign_state.read(RUN/'campaign.json')
frozen_wavefront.verify_files(state['pins'])
service=json.loads((RUN/'service.json').read_bytes())
pointer=json.loads((RUN/'campaign.json').read_bytes())
result={
    'checked_at':time.time(), 'checkpoint':pointer['commit'],
    'service':service, 'pins_verified':len(state['pins']),
    'latest_amendment':state['runtime_amendments'][-1]['kind'],
    'summary':state['summary'],
    'repair_yield':state['fast_metrics'].get('repair_yield'),
    'inflight_count':len(state.get('fast_inflight',[])),
    'checks':{
        'running':service['status']=='running' and bool(service.get('worker_pid')),
        'fresh_heartbeat':time.time()-service.get('heartbeat_at',0)<30,
        'advanced':pointer['commit']>3488,
        'imported_new_work':state['fast_metrics'].get('repair_yield',{}).get('totals',{}).get('work_items',0)>0,
    },
}
campaign_state.atomic(OUT/'live-validation.json',result)
print(json.dumps(result))
