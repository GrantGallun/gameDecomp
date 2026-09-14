import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from eval import campaign_workers, frozen_wavefront, progress_app


def test_sync_preserves_all_run_configs_including_unattached_history(tmp_path):
    main, private = tmp_path/'main.sqlite', tmp_path/'private.sqlite'
    with sqlite3.connect(main) as conn:
        conn.executescript(Path('kb/schema.sql').read_text())
        conn.executemany('INSERT INTO attempt_runs VALUES (?,?,?,?,?)',
                         [(str(i),'history','model',json.dumps({'i':i,'data':'x'*2000}),i) for i in range(510)])
    prior = campaign_workers.synchronize(main,private)
    with sqlite3.connect(main) as conn:
        conn.executemany('INSERT INTO attempt_runs VALUES (?,?,?,?,?)',
                         [('new'+str(i),'unattached','other',json.dumps({'new':i}),i) for i in range(510)])
    with sqlite3.connect(private) as conn:
        conn.execute("INSERT INTO attempt_runs VALUES ('private-only','local','','{}',0)")
    for _ in range(2):
        prior = campaign_workers.synchronize(main,private,prior)
        with sqlite3.connect(main) as upstream, sqlite3.connect(private) as worker:
            assert upstream.execute('SELECT * FROM attempt_runs ORDER BY id').fetchall() == worker.execute(
                'SELECT * FROM attempt_runs ORDER BY id').fetchall()
            assert worker.execute('PRAGMA foreign_key_check').fetchall() == []


def test_full_hash_detects_same_size_restored_mtime_and_nonfiles(tmp_path):
    path = tmp_path/'input'; path.write_bytes(b'original')
    pins = {str(path):hashlib.sha256(path.read_bytes()).hexdigest()}
    frozen_wavefront.verify_files(pins)
    before = path.stat()
    path.write_bytes(b'modified')
    os.utime(path, ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files(pins)
    path.unlink()
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files(pins)
    path.mkdir()
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files(pins)


@pytest.mark.skipif(os.name=='nt',reason='symlink/FIFO behavior validated on WSL')
def test_full_hash_checks_symlink_target_and_rejects_fifo_without_blocking(tmp_path):
    target = tmp_path/'target'; target.write_bytes(b'original')
    link = tmp_path/'link'; link.symlink_to(target)
    pins = {str(link):hashlib.sha256(target.read_bytes()).hexdigest()}
    frozen_wavefront.verify_files(pins)
    alternate = tmp_path/'alternate'; alternate.write_bytes(b'changed')
    link.unlink(); link.symlink_to(alternate)
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files(pins)
    fifo = tmp_path/'fifo'; os.mkfifo(fifo)
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files({str(fifo):'not-regular'})


def test_live_rates_use_matching_controller_and_do_not_count_downtime(tmp_path):
    checkpoint = {'kind':'campaign-checkpoint-index-v1','updated_at':100,'health':{},
        'fast_metrics':{'session_seconds':100.,'session_live_since':100.,'session_pid':123,
                        'improved_items':2,'exact_items':1,'improvements_per_hour':72.}}
    (tmp_path/'campaign.json').write_text(json.dumps(checkpoint))
    service = {'status':'running','worker_pid':123,'heartbeat_at':119}
    (tmp_path/'service.json').write_text(json.dumps(service))
    metrics = progress_app.snapshot(tmp_path,now=120)['metrics']
    assert metrics['session_seconds']==120 and metrics['improvements_per_hour']==60
    assert metrics['exact_per_hour']==30
    # No mutation/double counting across repeated UI polls.
    assert progress_app.snapshot(tmp_path,now=120)['metrics']==metrics
    service['worker_pid']=456
    (tmp_path/'service.json').write_text(json.dumps(service))
    assert progress_app.snapshot(tmp_path,now=120)['metrics']['session_seconds']==100
    service.update(worker_pid=123,status='paused')
    (tmp_path/'service.json').write_text(json.dumps(service))
    assert progress_app.snapshot(tmp_path,now=120)['metrics']['session_seconds']==100
    service.update(status='running',heartbeat_at=1)
    (tmp_path/'service.json').write_text(json.dumps(service))
    assert progress_app.snapshot(tmp_path,now=120)['metrics']['session_seconds']==100


@pytest.mark.skipif(os.name=='nt',reason='runtime uses WSL flock')
def test_reused_worker_restores_wrappers_authorizer_and_pin_after_failures(tmp_path,monkeypatch):
    from eval import fast_campaign, fast_runtime, semantic_lane
    from solver import llm, workspace, type_constraints, mips_differential, compiler_recipe
    from functools import lru_cache
    if fast_runtime._active is not None:
        fast_runtime._active.close()
    recipe_cache=lru_cache(maxsize=4)(lambda value:value)
    monkeypatch.setattr(compiler_recipe,'_resolve',recipe_cache)
    original_connect = sqlite3.connect
    monkeypatch.setattr(llm,'generate',lambda *a,**kw:('edit',{}))
    monkeypatch.setattr(workspace,'_verified_build_cache_pin','before')
    originals = [(llm,'generate'),(workspace,'sh'),(type_constraints,'measure'),
                 (semantic_lane.Panel,'__init__'),(semantic_lane.Panel,'__call__'),
                 (mips_differential,'bind_target'),(mips_differential,'parse')]
    # Target helper names may evolve; include all installed attributes via the
    # relevant stable entry points and the explicit workspace pin marker.
    originals = [(obj,name,getattr(obj,name)) for obj,name in originals if hasattr(obj,name)]
    jobs=[]
    for i in range(4):
        slot=tmp_path/str(i);slot.mkdir();db=slot/'worker.sqlite'
        with sqlite3.connect(db) as conn:
            conn.executescript(Path('kb/schema.sql').read_text())
            conn.execute('CREATE TABLE guard(value TEXT)')
        source=slot/'candidate.c';source.write_text('int f(void){return 0;}')
        jobs.append({'slot':str(slot),'db':str(db),'repo':str(slot),'function':f'f{i}',
            'raw':str(slot/'raw.json'),'pin_sha256':f'pin{i}','model_lock':str(tmp_path/'model.lock'),
            'node':{'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()},
            'profile':{},'config':{}})
    calls=[]
    def execute(**kw):
        index=int(kw['function'][1:]);calls.append(index)
        assert workspace._verified_build_cache_pin==f'pin{index}'
        assert recipe_cache.cache_info().currsize==0
        recipe_cache(index)
        with sqlite3.connect(kw['db']) as conn:
            with pytest.raises(sqlite3.DatabaseError,match='authorized'):
                conn.execute("INSERT INTO guard VALUES ('denied')")
        if index:
            # The prior DB no longer inherits its previous job's authorizer.
            with sqlite3.connect(jobs[index-1]['db']) as conn:
                conn.execute("INSERT INTO guard VALUES ('allowed')")
        llm.generate(prompt='bounded',num_predict=10)
        if index==1:
            raise KeyError('unexpected worker failure')
        return dict(kw['node'])
    monkeypatch.setattr(fast_campaign.campaign,'execute',execute)
    atomic=fast_campaign.campaign_state.atomic
    def failed_atomic(path,value):
        if str(path)==jobs[2]['raw']:
            raise OSError('raw receipt write interrupted')
        return atomic(path,value)
    monkeypatch.setattr(fast_campaign.campaign_state,'atomic',failed_atomic)
    for i,job in enumerate(jobs):
        if i in (1,2):
            with pytest.raises(KeyError if i==1 else OSError):
                fast_campaign._worker(job)
        else:
            fast_campaign._worker(job)
            assert json.loads(Path(job['raw']).read_bytes())['performance']['model_calls']==1
        assert sqlite3.connect is original_connect
        assert workspace._verified_build_cache_pin=='before'
        assert recipe_cache.cache_info().currsize==0
        assert all(getattr(obj,name) is original for obj,name,original in originals)
    assert calls==[0,1,2,3]
