"""Amend the paused/drained live run with the failure-census profiles (address symbols, recertify) and pin read retry."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
RUN=ROOT/'eval/results/resume-pipeline-20260908'
STAGE=OUT/'staged-code'
REVISION='20260914-guard-before-load-linear'
AMENDMENT_KIND='regalloc-generator-hotfix'
RUNTIME_PREFIXES=('solver/','eval/')
sys.path.insert(0,str(STAGE))
from eval import campaign_state, completion_campaign as campaign, frozen_wavefront, fast_campaign

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    manifest=json.loads((OUT/'staged-manifest.json').read_bytes())
    tests=(OUT/'staged-tests.log').read_text()
    if not re.search(r'^\d+ passed in [\d.]+s(?: \([^\n]+\))?\s*$',tests,re.M) or 'FAILED ' in tests or 'ERROR ' in tests:
        raise ValueError('staged suite has not passed')
    for rel,entry in manifest.items():
        if sha(STAGE/rel)!=entry['new_sha256']:raise ValueError('stage changed: '+rel)
    with campaign.campaign_lock(RUN/'campaign.lock'):
        service=json.loads((RUN/'service.json').read_bytes())
        if service['status']!='paused' or service.get('worker_pid') or not (RUN/'service.pause').exists():
            raise ValueError('pause and drain first')
        state=campaign_state.read(RUN/'campaign.json')
        # The spinning job can never drain. A job with no durable raw result is resubmitted by the
        # controller's own recovery path on resume; it will then run under the fixed generator.
        # Finished-but-unimported results must still be imported first.
        stranded=[j for j in state.get('fast_inflight') or [] if not Path(j['raw']).exists()]
        if state.get('inflight') or len(stranded)!=len(state.get('fast_inflight') or []):
            raise ValueError('inflight work with results remains')
        frozen_wavefront.verify_files(state['pins'])
        if frozen_wavefront.model_digest(state['config']['endpoint'],state['config']['model'])!=state['model_digest']:
            raise ValueError('model identity changed')
        with sqlite3.connect(f'file:{RUN/"campaign.sqlite"}?mode=ro',uri=True) as conn:
            inventory=list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
        if campaign.digest(inventory)!=state['inventory_sha256']:raise ValueError('inventory changed')
        for rel,entry in manifest.items():
            live=RUN/'code'/rel
            if (sha(live) if live.exists() else None)!=entry['old_sha256']:
                raise ValueError('live file changed: '+rel)
        revision=RUN/'revisions'/REVISION
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json','launch.json','service.json','service-control.json'):
            shutil.copy2(RUN/name,revision/name)
        campaign_state.atomic(revision/'previous-state-store.json',{
            'store':str(RUN/'campaign.state.sqlite'),'pointer':json.loads((RUN/'campaign.json').read_bytes()),
            'restore':'Restore old code and this pointer together only while paused; old commits remain immutable.'})
        before_statuses={n:v['status'] for n,v in state['nodes'].items()}
        prior_summary=dict(state['summary'])
        prior_metrics=dict(state.get('fast_metrics',{}))
        for rel in manifest:
            live=RUN/'code'/rel
            if live.exists():
                backup=revision/'previous-code'/rel
                backup.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(live,backup)
        committed=False
        try:
            for rel in manifest:
                live=RUN/'code'/rel
                live.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(STAGE/rel,live)
            newpins=campaign._pins(RUN/'code',Path(state['config']['repo']))
            newpins.update({n:h for n,h in state['pins'].items()
                if Path(n).is_relative_to(Path(state['config']['repo'])/'nonmatchings')})
            changed={n for n in newpins.keys()|state['pins'].keys() if newpins.get(n)!=state['pins'].get(n)}
            expected={str(RUN/'code'/rel) for rel in manifest if rel.startswith(RUNTIME_PREFIXES) and Path(rel).suffix in {'.py','.json'}}
            if changed!=expected:raise ValueError('unexpected pin changes: '+str(changed^expected))
            record={'kind':AMENDMENT_KIND,'applied_at':time.time(),
                'authorization':'User asked to find why the pipeline stagnated overnight and patch the most common failures (2026-09-14). '
                                'Defect fix to the deployed regalloc_search generators; a worker spun for over an hour.',
                'changed_files':manifest,'changed_pins':sorted(changed),'validation':str(OUT),
                'prior_metrics':prior_metrics,'prior_summary':prior_summary,
                'stranded_jobs_resubmitted':[{k:j.get(k) for k in ('id','function')} | {'profile':j['profile']['name']} for j in stranded],
                'limits':'No candidate imports, no gate, profile, schedule or budget change. guard_before_load uses an unambiguous '
                         'guard-body line pattern (identical language). Stranded jobs without raw results are re-run by the '
                         'controller recovery path under the fixed code.'}
            state['pins']=newpins
            state.setdefault('runtime_amendments',[]).append(record)
            state.setdefault('fast_metrics',{}).setdefault('repair_yield',{'version':1,'totals':{},'by_size':{},'by_profile':{}})
            selected=fast_campaign.project(state)
            fast_campaign.summary(state,selected)
            assert before_statuses=={n:v['status'] for n,v in state['nodes'].items()}
            assert state['summary']['object_exact_or_integrated']==prior_summary['object_exact_or_integrated']
            frozen_wavefront.verify_files(newpins)
            campaign_state.atomic(revision/'amendment.json',record)
            campaign_state.Store(RUN/'campaign.json').save(state)
            committed=True
            launch=json.loads((RUN/'launch.json').read_bytes())
            launch['runtime_amendment']=str(revision/'amendment.json')
            campaign_state.atomic(RUN/'launch.json',launch)
            output={'revision':str(revision),'summary':state['summary'],'changed_pins':sorted(changed),
                    'checkpoint':json.loads((RUN/'campaign.json').read_bytes())['commit']}
            campaign_state.atomic(OUT/'deployment.json',output)
            print(json.dumps(output))
        except Exception:
            if not committed:
                for rel,entry in manifest.items():
                    live=RUN/'code'/rel
                    if entry['old_sha256'] is None:live.unlink(missing_ok=True)
                    else:shutil.copy2(revision/'previous-code'/rel,live)
            raise

if __name__=='__main__':main()
