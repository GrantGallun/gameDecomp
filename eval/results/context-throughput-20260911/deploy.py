"""Paused-boundary amendment: stable model allocation and inference timings."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
sys.path.insert(0,str(ROOT))
from eval import campaign_state,completion_campaign as campaign,frozen_wavefront

def main():
    with campaign.campaign_lock(RUN/'campaign.lock'):
        service=json.loads((RUN/'service.json').read_bytes())
        if service['status']!='paused' or service.get('worker_pid') or not (RUN/'service.pause').exists():
            raise ValueError('service is not safely paused')
        state=campaign_state.read(RUN/'campaign.json')
        if state.get('inflight') or state.get('fast_inflight'):raise ValueError('unsettled work')
        frozen_wavefront.verify_files(state['pins'])
        if frozen_wavefront.model_digest(state['config']['endpoint'],state['config']['model'])!=state['model_digest']:
            raise ValueError('model weights changed')
        revision=RUN/'revisions/20260911-stable-model-context'
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json','launch.json','service.json','service-control.json'):
            shutil.copy2(RUN/name,revision/name)
        campaign_state.atomic(revision/'campaign-full.json',state)
        changed={}
        for relative in ('solver/llm.py','eval/fast_runtime.py','eval/fast_campaign.py'):
            target=RUN/'code'/relative
            shutil.copy2(target,revision/('previous-'+target.name))
            shutil.copy2(ROOT/relative,target)
            shutil.copy2(target,revision/target.name)
            changed[str(target)]=hashlib.sha256(target.read_bytes()).hexdigest()
        record={'kind':'stable-model-context-amendment','applied_at':time.time(),
            'authorization':'User requested further throughput gains on the current run',
            'changed_files':changed,'inference_options':{'num_ctx':32768},
            'reason':'avoid full model reloads when requests alternate between 16k and 32k contexts',
            'previous_metrics':dict(state['fast_metrics']),
            'validation':'2154 tests passed in 45.45 seconds; paired replay and compiler receipts in '+str(Path(__file__).parent),
            'unchanged':'weights, precision, prompts, seeds, output budgets, inference concurrency and all verification gates'}
        state['pins'].update(changed)
        state.setdefault('runtime_amendments',[]).append(record)
        state['inference_options']={'num_ctx':32768}
        campaign_state.Store(RUN/'campaign.json').save(state)
        launch=json.loads((RUN/'launch.json').read_bytes())
        launch['runtime_amendment']=str(revision/'amendment.json')
        campaign_state.atomic(RUN/'launch.json',launch)
        campaign_state.atomic(revision/'amendment.json',record)
        print(json.dumps({'revision':str(revision),'summary':state['summary'],'changed_files':changed}),flush=True)

if __name__=='__main__':main()
