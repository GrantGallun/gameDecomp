"""Real frozen-toolchain two-worker smoke and complete checkpoint roundtrip."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'staged-code'))
from eval import campaign_state, campaign_workers, fast_campaign


def main():
    run=ROOT.parent/'resume-pipeline-20260908'
    workspace=Path('/home/grant/decomp/campaign-fast-validation-20260911')
    workspace.mkdir(exist_ok=False)
    state=campaign_state.read(run/'campaign.json')
    checkpoint=ROOT/'checkpoint-smoke-v2.json'
    store=campaign_state.Store(checkpoint)
    initial=store.save(state)
    start=time.monotonic()
    assert campaign_state.read(checkpoint)==state
    load_seconds=time.monotonic()-start
    changed=copy.deepcopy(state)
    changed['nodes']['updateControllerPakFileDeleteErrorPrompt']['speed_roundtrip_probe']=True
    incremental=store.save(changed,changed=('updateControllerPakFileDeleteErrorPrompt',))
    assert campaign_state.read(checkpoint)==changed
    baseline=workspace/'baseline.sqlite'
    with sqlite3.connect(f'file:{(run/"campaign.sqlite").as_posix()}?mode=ro',uri=True) as src, sqlite3.connect(baseline) as dst:
        src.backup(dst)
    jobs=[]
    for slot_id,function in enumerate(('updateControllerPakFileDeleteErrorPrompt','initFallingActionProjectile')):
        slot=workspace/str(slot_id);slot.mkdir()
        cutoffs=campaign_workers.synchronize(baseline,slot/'worker.sqlite')
        repo=campaign_workers.isolate(Path(state['config']['repo']),slot/'repo',function)
        config={**state['config'],'model_calls':0}
        jobs.append({'id':'smoke-'+function,'slot':str(slot),'db':str(slot/'worker.sqlite'),
            'repo':str(repo),'function':function,'node':state['nodes'][function],
            'profile':{'name':'compile_recovery','model':False,'deterministic_budget':0},
            'config':config,'cutoffs':cutoffs,'pin_sha256':fast_campaign.campaign.digest(state['pins']),
            'model_lock':str(workspace/'model.lock'),'raw':str(ROOT/(function+'.v2.private.json'))})
    start=time.monotonic()
    with ProcessPoolExecutor(2,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
        list(pool.map(fast_campaign.worker,jobs))
    wall=time.monotonic()-start
    results=[]
    for job in jobs:
        raw=json.loads(Path(job['raw']).read_bytes())
        assert raw.get('calls_attempted',0)==0
        if raw.get('status')=='parked':
            raise AssertionError(raw['blocker'])
        assert raw['residual']['compiled'] and raw['residual']['frontend']['passed']
        amap,pmap=campaign_workers.merge(baseline,job['db'],job['cutoffs'],job['id'])
        canonical=campaign_workers.remap(raw,amap,pmap)
        with sqlite3.connect(baseline) as conn:
            assert conn.execute('SELECT source_sha256 FROM attempts WHERE id=?',(canonical['attempt_id'],)).fetchone()[0]==canonical['source_sha256']
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        results.append({'function':job['function'],'score':raw['score'],'exact':raw['exact'],
                        'performance':raw['performance'],'worker_seconds':raw['wall_seconds'],
                        'imported_attempts':len(amap),'source_sha256':raw['source_sha256']})
    report={'kind':'fast-runtime-real-smoke','initial_save_seconds':initial,'load_seconds':load_seconds,
        'incremental_save_seconds':incremental,'checkpoint_pointer_bytes':checkpoint.stat().st_size,
        'full_roundtrip_equal':True,'two_worker_wall_seconds':wall,'results':results,
        'model_calls':0,'live_campaign_modified':False,'private_baseline':str(baseline)}
    campaign_state.atomic(ROOT/'runtime-validation-v2.json',report)
    print(json.dumps(report,indent=2),flush=True)
    import replay_cache
    replay_cache.main()


if __name__=='__main__':
    main()
