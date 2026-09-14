"""Freeze a read-only checkpoint and verify the installed campaign's outcome."""
import hashlib
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
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
nodes={name:{key:node.get(key) for key in ('status','attempt_id','source_sha256','integration')}
       for name,node in state['nodes'].items() if node['status']=='integrated'}
result={'checkpoint':pointer['commit'],'checked_at':time.time(),
    'summary':state['summary'],'pins_verified':len(state['pins']),
    'integration_sweep':state.get('integration_sweep'), 'integrated_nodes':nodes,
    'new_work_items':state['fast_metrics']['completed_items']-amendment['prior_metrics']['completed_items'],
    'service':{key:service.get(key) for key in ('status','pid','worker_pid','heartbeat_at')},
    'checks':{'running':service['status']=='running' and bool(service.get('worker_pid')),
        'fresh_heartbeat':time.time()-service.get('heartbeat_at',0)<30,
        'advanced':pointer['commit']>deployment['checkpoint'],
        'imported_new_work':state['fast_metrics']['completed_items']>amendment['prior_metrics']['completed_items'],
        'integration_enabled':state['runtime_options'].get('integrate') is True,
        'object_exact_preserved':sum(n['status']=='object_exact' for n in state['nodes'].values())>=amendment['prior_summary']['object_exact_or_integrated']}}
campaign_state.atomic(OUT/'live-validation.json',result)
print(json.dumps({key:value for key,value in result.items() if key not in {'integration_sweep','integrated_nodes'}}))
