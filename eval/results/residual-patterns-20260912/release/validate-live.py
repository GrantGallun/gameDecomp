"""Read-only final validation of the stack-home amendment and runtime."""
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import campaign_state,frozen_wavefront
from solver import rewrites

deployment=json.loads((OUT/'deployment.json').read_bytes())
amendment=json.loads((Path(deployment['revision'])/'amendment.json').read_bytes())
pointer=json.loads((RUN/'campaign.json').read_bytes())
pointer['store']=str(RUN/pointer['store'])
campaign_state.atomic(OUT/'validation-checkpoint.json',pointer)
state=campaign_state.read(OUT/'validation-checkpoint.json')
frozen_wavefront.verify_files(state['pins'])
service=json.loads((RUN/'service.json').read_bytes())
base=OUT.parent/'startEndingSlashRepeatAnim.selected'
proposals=rewrites.propose(base.with_suffix('.selected.c').read_text(),base.with_suffix('.selected.diff').read_text())
before=amendment['prior_metrics']['completed_items']
result=dict(checkpoint=pointer['commit'],checked_at=time.time(),service=service,
    summary=state['summary'],pins_verified=len(state['pins']),
    new_work_items=state['fast_metrics']['completed_items']-before,
    checks=dict(running=service['status']=='running' and bool(service.get('worker_pid')),
        fresh_heartbeat=time.time()-service.get('heartbeat_at',0)<30,
        advanced=pointer['commit']>deployment['checkpoint'],
        imported_new_work=state['fast_metrics']['completed_items']>before,
        frozen_generator_fires=sum(p.kind=='stack-home' for p in proposals)==1))
campaign_state.atomic(OUT/'live-validation.json',result)
print(json.dumps(result))
