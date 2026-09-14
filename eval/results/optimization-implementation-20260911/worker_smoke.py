"""Four real MIPS jobs in one process, alternating isolated DBs and functions."""
import hashlib
import json
from pathlib import Path
import resource
import sqlite3
import sys
import time

OUT=Path(__file__).resolve().parent
STAGE=OUT/'staged-code'
sys.path.insert(0,str(STAGE))
from eval import campaign_state, campaign_workers, fast_campaign
from solver import workspace


def main():
    previous=OUT.parent/'campaign-speed-20260911'
    state=campaign_state.read(previous/'checkpoint-smoke-v2.json')
    baseline=Path('/home/grant/decomp/campaign-fast-validation-20260911/baseline.sqlite')
    native=Path('/home/grant/decomp/optimization-worker-smoke-20260911')
    native.mkdir(exist_ok=False)
    assert workspace.__file__.startswith(str(STAGE))
    pins={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in STAGE.rglob('*.py')}
    jobs=[]
    for i,function in enumerate(('updateControllerPakFileDeleteErrorPrompt','initFallingActionProjectile')):
        slot=native/str(i)
        slot.mkdir()
        cutoffs=campaign_workers.synchronize(baseline,slot/'worker.sqlite')
        repo=campaign_workers.isolate(Path(state['config']['repo']),slot/'repo',function)
        jobs.append({'id':function,'slot':str(slot),'db':str(slot/'worker.sqlite'),
            'repo':str(repo),'function':function,'node':state['nodes'][function],
            'profile':{'name':'local_rewrites','model':False,'deterministic_budget':8,'deterministic_depth':1},
            'config':{**state['config'],'model_calls':0},'cutoffs':cutoffs,
            'pin_sha256':fast_campaign.campaign.digest(pins),'model_lock':str(native/'model.lock')})
    originals=(sqlite3.connect,workspace.sh)
    results=[]
    for arm in ('cold','warm'):
        for template in jobs:
            job={**template,'id':arm+'-'+template['id'],
                 'raw':str(OUT/(arm+'-'+template['function']+'.private.json'))}
            campaign_workers.synchronize(baseline,Path(job['db']),job['cutoffs'])
            started=time.monotonic()
            fast_campaign.worker(job)
            raw=json.loads(Path(job['raw']).read_bytes())
            assert raw.get('status')!='parked',raw.get('blocker')
            assert raw['residual']['compiled'] and raw['residual']['frontend']['passed']
            assert raw.get('calls_attempted',0)==0
            assert (sqlite3.connect,workspace.sh)==originals
            assert workspace._verified_build_cache_pin is None
            record={'arm':arm,'function':job['function'],'seconds':time.monotonic()-started,
                'source_sha256':raw['source_sha256'],'score':raw['score'],'exact':raw['exact'],
                'semantic_validation':raw.get('semantic_validation'),
                'performance':raw['performance'],'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            if arm=='warm':
                cold=next(r for r in results if r['function']==job['function'])
                for key in ('source_sha256','score','exact','semantic_validation'):
                    assert record[key]==cold[key],(job['function'],key)
            results.append(record)
            print(json.dumps({k:v for k,v in record.items() if k!='semantic_validation'}),flush=True)
    campaign_state.atomic(OUT/'worker-smoke.json',{'same_process':True,'jobs':results,
        'candidate_verdict_equality':True,'model_calls':0,'live_modified':False})


if __name__=='__main__':main()
