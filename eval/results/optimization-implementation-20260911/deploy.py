"""Deploy the tested frozen revision at a drained, locked checkpoint."""
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
RUN=ROOT/'eval/results/resume-pipeline-20260908'
STAGE=OUT/'staged-code'
sys.path.insert(0,str(ROOT))
from eval import campaign_state, completion_campaign as campaign, frozen_wavefront


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest=json.loads((OUT/'staged-manifest.json').read_bytes())
    # Tests run on this exact staged tree. Later edits require fresh validation.
    for relative,entry in manifest.items():
        if sha(STAGE/relative)!=entry['new_sha256']:
            raise ValueError('staged file changed after manifest: '+relative)
    tests=(OUT/'staged-tests.log').read_text()
    if not re.search(r'^\d+ passed in [\d.]+s\s*$',tests,re.M):
        raise ValueError('staged release suite not passing')
    if not json.loads((OUT/'worker-smoke.json').read_bytes())['candidate_verdict_equality']:
        raise ValueError('real worker smoke not passing')
    with campaign.campaign_lock(RUN/'campaign.lock'):
        service=json.loads((RUN/'service.json').read_bytes())
        if service['status']!='paused' or service.get('worker_pid') or not (RUN/'service.pause').exists():
            raise ValueError('campaign must be paused and drained')
        state=campaign_state.read(RUN/'campaign.json')
        if state.get('inflight') or state.get('fast_inflight'):
            raise ValueError('unsettled work')
        frozen_wavefront.verify_files(state['pins'])
        if frozen_wavefront.model_digest(state['config']['endpoint'],state['config']['model'])!=state['model_digest']:
            raise ValueError('model weights changed')
        for relative,entry in manifest.items():
            live=RUN/'code'/relative
            if (sha(live) if live.exists() else None)!=entry['old_sha256']:
                raise ValueError('live file differs from staging base: '+relative)
        revision=RUN/'revisions/20260911-throughput-reuse'
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json','launch.json','service.json','service-control.json'):
            shutil.copy2(RUN/name,revision/name)
        campaign_state.atomic(revision/'campaign-full.json',state)
        previous=dict(state['fast_metrics'])
        for relative in manifest:
            live=RUN/'code'/relative
            if live.exists():
                backup=revision/'previous-code'/relative
                backup.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(live,backup)
            live.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(STAGE/relative,live)
        newpins=campaign._pins(RUN/'code',Path(state['config']['repo']))
        newpins.update({name:digest for name,digest in state['pins'].items()
                        if Path(name).is_relative_to(Path(state['config']['repo'])/'nonmatchings')})
        changedpins={name for name in newpins.keys()|state['pins'].keys()
                     if newpins.get(name)!=state['pins'].get(name)}
        allowed={str(RUN/'code'/relative) for relative in manifest}
        if not changedpins<=allowed:
            raise ValueError('unexpected input change outside deployment: '+str(changedpins-allowed))
        record={'kind':'throughput-reuse-amendment','applied_at':time.time(),
            'authorization':'User authorized implementation and trials of the optimization audit findings',
            'changed_files':manifest,'previous_metrics':previous,
            'validation':{'main_tests':str(OUT/'main-tests.log'),'staged_tests':str(OUT/'staged-tests.log'),
                          'real_workers':str(OUT/'worker-smoke.json')},
            'runtime_options':{**state['runtime_options'],'tasks_per_worker':8},
            'service_batch':200,
            'changes':['compact passing semantic reports','conservative explicit context headroom',
                'missing run-row payload synchronization','single-open full content hashes',
                'same-job compile artifact reuse with fresh gates','bound target exploration reuse',
                'eight jobs per process with scoped state','stage timing and live elapsed rates'],
            'inference_policy':'Existing per-profile effort, output/call budgets and one GPU slot retained',
            'acceptance':'Compiler, frontend, source bindings, semantic and exactness gates unchanged'}
        state['pins']=newpins
        state['runtime_options']=record['runtime_options']
        state.setdefault('runtime_amendments',[]).append(record)
        launch=json.loads((RUN/'launch.json').read_bytes())
        command=launch['command']
        if '--tasks-per-worker' in command:
            command[command.index('--tasks-per-worker')+1]='8'
        else:
            command.extend(['--tasks-per-worker','8'])
        command[command.index('--max-work-items')+1]='200'
        launch['runtime_amendment']=str(revision/'amendment.json')
        campaign_state.atomic(revision/'amendment.json',record)
        campaign_state.Store(RUN/'campaign.json').save(state)
        campaign_state.atomic(RUN/'launch.json',launch)
        frozen_wavefront.verify_files(newpins)
        print(json.dumps({'revision':str(revision),'changed_pins':len(changedpins),
                          'summary':state['summary'],'runtime_options':state['runtime_options']}),flush=True)


if __name__=='__main__':main()
