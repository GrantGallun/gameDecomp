import json
from pathlib import Path
import sqlite3
import sys
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'staged-code'))
from eval import campaign_state, campaign_workers, fast_campaign


def main():
    previous=json.loads((ROOT/'runtime-validation-v2.json').read_bytes())
    start=time.monotonic()
    state=campaign_state.read(ROOT/'checkpoint-smoke-v2.json')
    read_seconds=time.monotonic()-start
    workspace=Path('/home/grant/decomp/campaign-fast-validation-20260911')
    jobs=[]
    for i,row in enumerate(previous['results']):
        slot=workspace/str(i)
        with sqlite3.connect(slot/'worker.sqlite') as conn:
            cutoffs=campaign_workers.maxima(conn)
        cutoffs['attempts']-=row['imported_attempts']
        cutoffs=campaign_workers.synchronize(workspace/'baseline.sqlite',slot/'worker.sqlite',cutoffs)
        function=row['function']
        jobs.append({'id':'replay-'+function,'slot':str(slot),'db':str(slot/'worker.sqlite'),
            'repo':str(slot/'repo'),'function':function,'node':state['nodes'][function],
            'profile':{'name':'compile_recovery','model':False,'deterministic_budget':0},
            'config':{**state['config'],'model_calls':0},'cutoffs':cutoffs,
            'pin_sha256':fast_campaign.campaign.digest(state['pins']),
            'model_lock':str(workspace/'model.lock'),'raw':str(ROOT/(function+'.replay.private.json'))})
    start=time.monotonic()
    with ProcessPoolExecutor(2,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
        list(pool.map(fast_campaign.worker,jobs))
    results=[]
    for row,job in zip(previous['results'],jobs):
        raw=json.loads(Path(job['raw']).read_bytes())
        assert raw.get('status')!='parked',raw
        assert raw['source_sha256']==row['source_sha256'] and raw['score']==row['score'] and raw['exact']==row['exact']
        assert raw['residual']['frontend']['passed']
        assert raw['calls_attempted']==0
        results.append({'function':job['function'],'performance':raw['performance'],
                        'same_candidate_and_verdict':True})
    report={'checkpoint_load_seconds_after_reader_optimization':read_seconds,
            'wall_seconds':time.monotonic()-start,'results':results,'model_calls':0}
    campaign_state.atomic(ROOT/'cache-validation.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
