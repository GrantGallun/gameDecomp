"""Explicitly authorized, paused-boundary amendment of the existing campaign."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
RUN=ROOT.parent/'resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import completion_campaign as campaign
from eval import frozen_wavefront


def main():
    revision=RUN/'revisions'/'20260911-incremental-parallel'
    with campaign.campaign_lock(RUN/'campaign.lock'):
        service=json.loads((RUN/'service.json').read_bytes())
        if service['status']!='paused' or service.get('worker_pid') or not (RUN/'service.pause').exists():
            raise ValueError('service has not reached a durable pause')
        state=json.loads((RUN/'campaign.json').read_bytes())
        if state.get('inflight') or state.get('fast_inflight'):
            raise ValueError('unsettled work remains')
        frozen_wavefront.verify_files(state['pins'])
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json','launch.json','service.json','service-control.json'):
            shutil.copy2(RUN/name,revision/name)
        added={}
        for name in ('campaign_state.py','campaign_workers.py','fast_runtime.py','fast_campaign.py'):
            source=ROOT/'staged-code'/'eval'/name
            if source.read_bytes() != (PROJECT/'eval'/name).read_bytes():
                raise ValueError('staged and tested runtime differ: '+name)
            destination=RUN/'code'/'eval'/name
            if destination.exists():
                raise ValueError('unexpected existing runtime file: '+str(destination))
            shutil.copy2(source,destination)
            shutil.copy2(source,revision/name)
            added[str(destination)]=hashlib.sha256(destination.read_bytes()).hexdigest()
        from eval import campaign_state
        from eval import campaign_workers
        import sqlite3
        # Reuse the full native validation copies after removing every private
        # append and synchronizing with the still-paused authoritative database.
        # This avoids copying 1.3 GB over the Windows mount once per worker.
        worker_root=Path('/home/grant/decomp/campaign-workers-20260911')
        worker_root.mkdir(exist_ok=False)
        with sqlite3.connect(RUN/'campaign.sqlite') as conn:
            cutoffs=campaign_workers.maxima(conn)
        for slot in ('0','1'):
            directory=worker_root/slot
            directory.mkdir()
            shutil.copy2(Path('/home/grant/decomp/campaign-fast-validation-20260911')/slot/'worker.sqlite', directory/'worker.sqlite')
            synchronized=campaign_workers.synchronize(RUN/'campaign.sqlite',directory/'worker.sqlite',cutoffs)
            campaign_state.atomic(directory/'database-cutoffs.json',synchronized)
        record={'kind':'incremental-parallel-runtime-amendment','applied_at':time.time(),
            'authorization':'User requested all speed improvements deployed to the current run',
            'prior_checkpoint':str(revision/'campaign.json'),'added_files':added,
            'controller':'eval.fast_campaign','workers':2,
            'worker_root':'/home/grant/decomp/campaign-workers-20260911',
            'dispatch':'two distinct functions per wave using original evidence-v1 priorities/profiles; ordered import',
            'unchanged':'model, per-function model budgets, semantic budgets, acceptance gates, original game sources',
            'storage':'content-addressed incremental snapshots; export via eval.campaign_state',
            'validation':{'full_suite':'see release-validation.json',
                'real_compiler':str(ROOT/'runtime-validation-v2.json'),
                'cache_replay':str(ROOT/'cache-validation.json')}}
        old=state.get('runtime_amendments',[])
        if not isinstance(old,list):raise ValueError('unknown amendment format')
        state['runtime_amendments']=[*old,record]
        state['pins'].update(added)
        state['fast_metrics']={}
        started=time.monotonic()
        campaign_state.Store(RUN/'campaign.json').save(state)
        assert campaign_state.read(RUN/'campaign.json')==state
        record['migration_seconds']=time.monotonic()-started
        launch=json.loads((RUN/'launch.json').read_bytes())
        command=launch['command']
        command[command.index('eval.completion_campaign')]='eval.fast_campaign'
        command += ['--workers','2','--worker-root',record['worker_root']]
        launch['runtime_amendment']=str(revision/'amendment.json')
        campaign_state.atomic(RUN/'launch.json',launch)
        campaign_state.atomic(revision/'amendment.json',record)
        print(json.dumps(record,indent=2),flush=True)


if __name__=='__main__':main()
