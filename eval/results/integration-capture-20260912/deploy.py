"""Install tested code, then explicitly bind the authorized integration option."""
import importlib.util
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'staged-code'))
spec=importlib.util.spec_from_file_location('impact_deploy',OUT.parent/'impact-20260912/deploy.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT=OUT
module.STAGE=OUT/'staged-code'
module.REVISION='20260912-integration'
module.AMENDMENT_KIND='integration-amendment'
module.main()

from eval import campaign_state, completion_campaign as campaign, frozen_wavefront
run=module.RUN
with campaign.campaign_lock(run/'campaign.lock'):
    service=json.loads((run/'service.json').read_bytes())
    assert service['status']=='paused' and not service.get('worker_pid') and (run/'service.pause').exists()
    state=campaign_state.read(run/'campaign.json')
    assert not state.get('inflight') and not state.get('fast_inflight')
    frozen_wavefront.verify_files(state['pins'])
    launch=json.loads((run/'launch.json').read_bytes())
    assert '--integrate' not in launch['command']
    assert not state['runtime_options'].get('integrate',False)
    record={'kind':'integration-option-amendment','applied_at':time.time(),
        'authorization':'User accepted automatic isolated integration and source-bound reconciliation.',
        'previous_runtime_options':dict(state['runtime_options']),
        'new_runtime_options':{**state['runtime_options'],'integrate':True},
        'scope':'Disposable full-ROM builds; no canonical source installation; existing exact gates unchanged.'}
    state['runtime_options']=record['new_runtime_options']
    state['runtime_amendments'].append(record)
    campaign_state.atomic(run/'revisions/20260912-integration/option-amendment.json',record)
    campaign_state.Store(run/'campaign.json').save(state)
    launch['command'].append('--integrate')
    campaign_state.atomic(run/'launch.json',launch)
    campaign_state.atomic(OUT/'option-deployment.json',record)
    print(json.dumps(record))
