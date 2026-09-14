"""Amend only the paused runtime, preserving campaign history and old pins."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
sys.path.insert(0,str(ROOT))
from eval import campaign_state, completion_campaign as campaign, frozen_wavefront

def main():
    with campaign.campaign_lock(RUN/'campaign.lock'):
        service=json.loads((RUN/'service.json').read_bytes())
        if service['status']!='paused' or service.get('worker_pid') or not (RUN/'service.pause').exists():
            raise ValueError('service has not reached a durable pause')
        state=campaign_state.read(RUN/'campaign.json')
        if state.get('fast_inflight') or state.get('inflight'):raise ValueError('unsettled work remains')
        frozen_wavefront.verify_files(state['pins'])
        endpoint=state['config']['endpoint'].rsplit(':',1)[0]+':11435'
        if frozen_wavefront.model_digest(endpoint,state['config']['model'])!=state['model_digest']:
            raise ValueError('dedicated GPU endpoint has different model weights')
        server=json.loads((Path(__file__).parent/'server.json').read_bytes().decode('utf-8-sig'))
        if server['settings']['OLLAMA_KV_CACHE_TYPE']!='f16' or server['settings']['OLLAMA_NUM_PARALLEL']!='1':
            raise ValueError('wrong server settings')
        revision=RUN/'revisions/20260911-gpu-pipeline'
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json','launch.json','service.json','service-control.json'):
            shutil.copy2(RUN/name,revision/name)
        # The archived pointer names an immutable commit in the sibling store.
        # Export full metadata/nodes as an independently readable rollback copy.
        campaign_state.atomic(revision/'campaign-full.json',state)
        changed={}
        for name in ('fast_runtime.py','fast_campaign.py'):
            target=RUN/'code/eval'/name
            shutil.copy2(target,revision/('previous-'+name))
            shutil.copy2(ROOT/'eval'/name,target)
            shutil.copy2(target,revision/name)
            changed[str(target)]=hashlib.sha256(target.read_bytes()).hexdigest()
        record={'kind':'gpu-pipeline-runtime-amendment','applied_at':time.time(),
            'authorization':'User requested VRAM optimization and best use of RTX 5080 on the current run',
            'previous_config':dict(state['config']),'previous_metrics':dict(state['fast_metrics']),
            'changed_files':changed,'server':server,
            'validation':{'full_suite':'2152 passed in 59.97 seconds',
                'benchmark':str(Path(__file__).parent),'acceptance':'original compiler, frontend, semantics and exactness gates retained'},
            'dispatch':'rolling; two eligible model profiles plus one CPU profile; original priority within each resource lane',
            'limits':'three private workers; two model-preparation jobs feed one inference slot; original per-function budgets; no integration'}
        state['runtime_options']={'workers':3,'dispatch':'pipeline','model_parallel':1,'model_workers':2}
        state['config']['endpoint']=endpoint
        state['pins'].update(changed)
        state.setdefault('runtime_amendments',[]).append(record)
        campaign_state.Store(RUN/'campaign.json').save(state)
        launch=json.loads((RUN/'launch.json').read_bytes())
        command=launch['command']
        command[command.index('--endpoint')+1]=endpoint
        command[command.index('--workers')+1]='3'
        command+=['--dispatch','pipeline','--model-parallel','1','--model-workers','2']
        launch['endpoint']=endpoint
        launch['runtime_amendment']=str(revision/'amendment.json')
        campaign_state.atomic(RUN/'launch.json',launch)
        campaign_state.atomic(revision/'amendment.json',record)
        print(json.dumps({'revision':str(revision),'endpoint':endpoint,'options':state['runtime_options'],'summary':state['summary']}),flush=True)

if __name__=='__main__':main()
