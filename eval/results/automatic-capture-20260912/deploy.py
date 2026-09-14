"""Install tested capture code and explicitly bind the approved plan manifest."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(OUT/'staged-code'))
spec=importlib.util.spec_from_file_location('capture_deploy',OUT/'deploy_protocol.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT=OUT
module.ROOT=ROOT
module.RUN=ROOT/'eval/results/resume-pipeline-20260908'
module.STAGE=OUT/'staged-code'
module.REVISION='20260912-runtime-capture'
module.AMENDMENT_KIND='runtime-capture-amendment'
module.main()

from eval import campaign_state, completion_campaign as campaign, frozen_wavefront
run=module.RUN
with campaign.campaign_lock(run/'campaign.lock'):
    service=json.loads((run/'service.json').read_bytes())
    assert service['status']=='paused' and not service.get('worker_pid') and (run/'service.pause').exists()
    state=campaign_state.read(run/'campaign.json')
    assert not state.get('inflight') and not state.get('fast_inflight')
    frozen_wavefront.verify_files(state['pins'])
    plan=OUT/'capture-plans.json'
    raw=plan.read_bytes()
    plan_hash=hashlib.sha256(raw).hexdigest()
    launch=json.loads((run/'launch.json').read_bytes())
    assert '--runtime-plan' not in launch['command']
    assert state['runtime_options'].get('runtime_plan') is None
    revision=run/'revisions/20260912-runtime-capture'
    (revision/'capture-plans.json').write_bytes(raw)
    record={'kind':'runtime-capture-option-amendment','applied_at':time.time(),
        'authorization':'User requested muted automatic emulator capture and replay in the current main test workflow.',
        'previous_runtime_options':dict(state['runtime_options']),
        'plan_path':str(plan),'plan_sha256':plan_hash,
        'new_runtime_options':{**state['runtime_options'],'runtime_plan':str(plan)},
        'scope':'Audited integer-leaf plans; muted isolated emulator, fresh current-candidate replay; no exact promotion.'}
    state['runtime_options']=record['new_runtime_options']
    state['config']['runtime_capture_plan_sha256']=plan_hash
    state['runtime_amendments'].append(record)
    campaign_state.atomic(revision/'option-amendment.json',record)
    campaign_state.Store(run/'campaign.json').save(state)
    launch['command'].extend(['--runtime-plan',str(plan)])
    campaign_state.atomic(run/'launch.json',launch)
    campaign_state.atomic(OUT/'option-deployment.json',record)
    print(json.dumps(record))
