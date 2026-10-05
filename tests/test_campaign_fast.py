import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from eval import campaign_state, campaign_workers


@pytest.fixture(autouse=True)
def scoped_runtime_wrappers():
    from eval import fast_runtime
    if fast_runtime._active is not None:
        fast_runtime._active.close()
    yield
    if fast_runtime._active is not None:
        fast_runtime._active.close()


def state():
    return {'kind':'resumable-completion-campaign','nodes':{
        'a':{'status':'pending','jobs':[], 'trace':['preserve']*100},
        'b':{'status':'object_exact','jobs':[]}},'status':'running','config':{'model_calls':3}}


def test_deterministic_dispatch_filters_model_jobs_in_pipeline_and_wave(monkeypatch):
    from eval import fast_campaign
    profiles = {'model_a': {'name': 'reasoned_alternative', 'model': True},
                'cpu_a': {'name': 'recertify@digest', 'model': False},
                'model_b': {'name': 'semantic_alternative', 'model': True},
                'cpu_b': {'name': 'regalloc_search', 'model': False}}
    queue = {'model_a': 0, 'cpu_a': 1, 'model_b': 2, 'cpu_b': 3}
    campaign_state = {'config': {'model_calls': 3},
                      'nodes': {name: {'profile': profile} for name, profile in profiles.items()},
                      'repair_queue': {'work_items': {name: {'function': name, 'priority': priority}
                                                      for name, priority in queue.items()}}}
    original = copy.deepcopy(campaign_state)
    monkeypatch.setattr(fast_campaign.campaign.repair_queue, 'next_profile',
                        lambda node, model_calls, legacy: node['profile'])
    for pipeline in (True, False):
        picked = fast_campaign.dispatch_items(campaign_state, [], 3, 2, 3,
                                              deterministic_only=True, pipeline=pipeline)
        assert [item['function'] for item in picked] == ['cpu_a', 'cpu_b']
    assert [item['function'] for item in fast_campaign.dispatch_items(
        campaign_state, [], 2, 2, 3, pipeline=True)] == ['model_a', 'model_b']
    assert [item['function'] for item in fast_campaign.dispatch_items(
        campaign_state, [], 2, 2, 3, pipeline=False)] == ['model_a', 'cpu_a']
    assert campaign_state == original


def test_deterministic_dispatch_exits_when_only_model_profiles_remain(monkeypatch):
    from eval import fast_campaign
    campaign_state = {'config': {'model_calls': 3},
                      'nodes': {'m': {'status': 'pending', 'profile': {'name': 'reasoned_alternative', 'model': True}}},
                      'repair_queue': {'work_items': {'m': {'function': 'm', 'priority': 0}}}}
    monkeypatch.setattr(fast_campaign.campaign.repair_queue, 'next_profile',
                        lambda node, model_calls, legacy: node['profile'])
    assert fast_campaign.dispatch_items(campaign_state, [], 3, 2, 3,
                                        deterministic_only=True, pipeline=True) == []
    assert 'm' in campaign_state['repair_queue']['work_items']


def test_deterministic_resume_rejects_existing_model_inflight():
    from eval import fast_campaign
    with pytest.raises(ValueError, match='model job already in flight'):
        fast_campaign.reject_model_inflight({'fast_inflight': [
            {'profile': {'name': 'reasoned_alternative', 'model': True}}]})
    fast_campaign.reject_model_inflight({'fast_inflight': [
        {'profile': {'name': 'regalloc_search', 'model': False}}]})


@pytest.mark.skipif(__import__('os').name == 'nt', reason='WSL controller uses flock')
def test_controller_deterministic_session_stops_with_model_work_remaining(tmp_path, monkeypatch):
    from eval import fast_campaign as fast
    main = tmp_path/'main.sqlite'; database(main)
    with sqlite3.connect(main) as conn:
        conn.execute("INSERT INTO functions(addr,name) VALUES (2,'g')")
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo = tmp_path/'repo'; repo.mkdir()
    nodes = {}
    for name in ('f', 'g'):
        source = repo/'nonmatchings'/name/'source.c'
        source.parent.mkdir(parents=True)
        source.write_text('int '+name+'(void){return 0;}')
        nodes[name] = {'status': 'pending', 'source': str(source),
                       'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                       'attempt_id': 1, 'jobs': [], 'residual': {'compiled': True}}
    path = tmp_path/'campaign.json'
    (tmp_path/'campaign-artifacts').mkdir()
    args = SimpleNamespace(resume=True, scheduler='evidence-v1', workers=2,
        state=path, repo=repo, db=main, project=tmp_path, model='fake', endpoint='offline',
        model_calls=3, timeout=10, num_predict=100, worker_root=tmp_path/'workers',
        max_work_items=5, dispatch='pipeline', model_workers=2, model_parallel=1,
        tasks_per_worker=1, reasoned_effort='profile', integrate=True,
        runtime_plan=tmp_path/'capture-plan.json', deterministic_only=True)
    config = {k: str(getattr(args,k)) if k in {'repo','db','project'} else getattr(args,k)
              for k in ('repo','db','project','model','endpoint','model_calls','timeout','num_predict','scheduler')}
    options = {'dispatch':'pipeline','workers':2,'model_parallel':1,'model_workers':2,
               'tasks_per_worker':1,'reasoned_effort':'profile','integrate':True,
               'runtime_plan':str(args.runtime_plan)}
    value = {'kind':'resumable-completion-campaign','config':config,'nodes':nodes,'pins':{},
             'runtime_options':options,'inventory_sha256':fast.campaign.digest(inventory),
             'model_digest':'pinned'}
    campaign_state.atomic(path,value)
    monkeypatch.setattr(fast.campaign,'_pins',lambda *a:{})
    monkeypatch.setattr(fast.campaign.frozen_wavefront,'model_digest',
                        lambda *a: pytest.fail('model endpoint contacted'))
    monkeypatch.setattr(fast.campaign_integration,'sweep',
                        lambda *a,**kw: pytest.fail('integration ran'))
    monkeypatch.setattr(fast.campaign_runtime,'sweep',
                        lambda *a,**kw: pytest.fail('runtime capture ran'))
    # The checkpoint reloads nodes, so choose by their immutable source path.
    def profile(node, *_):
        from solver import repair_queue
        cpu = Path(node['source']).parent.name == 'f'
        return {'name':'recertify@digest' if cpu else 'reasoned_alternative',
                'model':not cpu, 'lane':'byte', 'evidence_key':repair_queue.evidence_key(node)}
    monkeypatch.setattr(fast.campaign.repair_queue,'next_profile',profile)
    def project(state):
        state['repair_queue']={'work_items': {name:{'function':name,'priority':priority}
            for priority,name in enumerate(('g','f')) if not state['nodes'][name]['jobs']}}
        return 'global-work' if state['repair_queue']['work_items'] else None
    monkeypatch.setattr(fast,'project',project)
    dispatched=[]
    class Pool:
        def __init__(self,*a,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def submit(self,fn,job):
            dispatched.append((job['function'],job['profile']['model']))
            append(job['db'],job['id'])
            raw={'source':job['node']['source'],'source_sha256':job['node']['source_sha256'],
                 'attempt_id':2,'exact':False,'score':50.,'wall_seconds':1.,
                 'residual':{'compiled':True},
                 'performance':{'model_seconds':0.,'model_queue_seconds':0.}}
            campaign_state.atomic(job['raw'],raw)
            from concurrent.futures import Future
            future=Future();future.set_result(job['raw'])
            return future
    monkeypatch.setattr(fast,'ProcessPoolExecutor',Pool)
    result=fast.run(args)
    assert dispatched==[('f',False)]
    assert result['config']==config and result['runtime_options']==options
    assert result['fast_metrics']['last_session']['global_work_remaining'] is True
    assert result['fast_metrics']['last_session']['completed_items']==1
    assert result['fast_metrics']['last_session']['deterministic_work_remaining'] is False
    assert result['fast_metrics']['last_session']['stopped_reason']=='no_deterministic_work'
    assert result['summary']['work_remaining'] is True
    assert 'g' in result['repair_queue']['work_items']


def test_incremental_roundtrip_and_previous_pointer(tmp_path):
    path = tmp_path/'campaign.json'
    value = state()
    campaign_state.atomic(path,value)
    assert campaign_state.read(path) == value
    store = campaign_state.Store(path)
    store.save(value)
    previous = tmp_path/'previous.json'
    previous.write_bytes(path.read_bytes())
    old = copy.deepcopy(value)
    value['nodes']['a']['jobs'].append({'result':'failed'})
    value['status'] = 'paused_budget'
    store.save(value,changed=('a',))
    assert campaign_state.read(path) == value
    assert campaign_state.read(previous) == old
    assert campaign_state.Store(path).refs['b'] == store.refs['b']
    with sqlite3.connect(store.db) as conn:
        assert conn.execute('SELECT count(*) FROM commits').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM objects').fetchone()[0] == 5


def test_pointer_failure_preserves_committed_state(tmp_path,monkeypatch):
    path = tmp_path/'campaign.json'
    value = state()
    store = campaign_state.Store(path)
    store.save(value)
    previous = copy.deepcopy(value)
    value['nodes']['a']['trace'] = ['new']
    def fail(*args):
        raise OSError('simulated interruption before pointer replace')
    monkeypatch.setattr(campaign_state,'atomic',fail)
    with pytest.raises(OSError):
        store.save(value,changed=('a',))
    assert campaign_state.read(path) == previous


def test_identical_node_blobs_do_not_alias_after_load(tmp_path):
    path=tmp_path/'campaign.json'
    value=state()
    value['nodes']['b']=copy.deepcopy(value['nodes']['a'])
    campaign_state.Store(path).save(value)
    restored=campaign_state.read(path)
    restored['nodes']['a']['jobs'].append('a only')
    assert restored['nodes']['b']['jobs']==[]


@pytest.mark.parametrize('damage',['object','commit','missing'])
def test_corrupt_checkpoint_rejected(tmp_path,damage):
    path = tmp_path/'campaign.json'
    store = campaign_state.Store(path)
    store.save(state())
    with sqlite3.connect(store.db) as conn:
        if damage == 'object':
            import zlib
            conn.execute('UPDATE objects SET payload=?',(zlib.compress(b'{}'),))
        elif damage == 'commit':
            conn.execute("UPDATE commits SET manifest='{}'")
        else:
            conn.execute('DELETE FROM objects')
    with pytest.raises((ValueError,TypeError)):
        campaign_state.read(path)


def database(path):
    conn=sqlite3.connect(path)
    conn.executescript(Path('kb/schema.sql').read_text())
    conn.execute("INSERT INTO functions(addr,name) VALUES (1,'f')")
    conn.execute("INSERT INTO attempt_runs VALUES ('old','test','','{}',0)")
    conn.execute("INSERT INTO attempts(id,func_addr,iteration,run_id,source_code,compiled,created_at) VALUES(1,1,0,'old','old',1,0)")
    conn.commit()
    conn.close()


def append(private,run):
    with sqlite3.connect(private) as conn:
        conn.execute('INSERT INTO attempt_runs VALUES (?,\'test\',\'\',\'{}\',1)',(run,))
        conn.execute("INSERT INTO attempts(id,func_addr,iteration,run_id,parent_attempt_id,source_code,compiled,sampling,created_at) VALUES(2,1,1,?,1,?,1,'{\"proposal_id\":1}',1)",(run,run))
        conn.execute("INSERT INTO model_proposals(id,run_id,parent_attempt_id,child_attempt_id,prompt_context,prompt_sha256,raw_response,status,created_at) VALUES (1,?,1,2,'p','h','r','valid',1)",(run,))
        conn.execute("INSERT INTO attempt_edges VALUES (1,2,'test','','',1)")


def test_two_workers_merge_collision_replay_and_resync(tmp_path):
    main=tmp_path/'main.sqlite'; database(main)
    left,right=tmp_path/'left.sqlite',tmp_path/'right.sqlite'
    a=campaign_workers.synchronize(main,left); b=campaign_workers.synchronize(main,right)
    append(left,'left'); append(right,'right')
    assert campaign_workers.merge(main,left,a,'job1') == ({2:2},{1:1})
    assert campaign_workers.merge(main,right,b,'job2') == ({2:3},{1:2})
    assert campaign_workers.merge(main,right,b,'job2') == ({2:3},{1:2})
    with sqlite3.connect(main) as conn:
        assert conn.execute('SELECT id,parent_attempt_id,source_code FROM attempts ORDER BY id').fetchall() == [(1,None,'old'),(2,1,'left'),(3,1,'right')]
        assert json.loads(conn.execute('SELECT sampling FROM attempts WHERE id=3').fetchone()[0])['proposal_id']==2
        assert conn.execute('SELECT child_attempt_id FROM model_proposals ORDER BY id').fetchall()==[(2,),(3,)]
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    updated=campaign_workers.synchronize(main,right,b)
    assert updated=={'attempts':3,'model_proposals':2}
    with sqlite3.connect(right) as conn:
        assert conn.execute('SELECT id,source_code FROM attempts ORDER BY id').fetchall()==[(1,'old'),(2,'left'),(3,'right')]


def test_import_transaction_rolls_back_on_run_collision(tmp_path):
    main=tmp_path/'main.sqlite'; database(main)
    private=tmp_path/'private.sqlite'; cutoff=campaign_workers.synchronize(main,private)
    append(private,'new')
    with sqlite3.connect(main) as conn:
        conn.execute("INSERT INTO attempt_runs VALUES ('new','different','','{}',1)")
    with pytest.raises(ValueError,match='identity collision'):
        campaign_workers.merge(main,private,cutoff,'job')
    with sqlite3.connect(main) as conn:
        assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM campaign_worker_imports').fetchone()[0]==0


@pytest.mark.skipif(__import__('os').name=='nt',reason='WSL runtime uses flock')
def test_cache_hits_pin_invalidation_and_fresh_frontend_path(tmp_path,monkeypatch):
    from eval import fast_runtime, semantic_lane
    from solver import workspace, llm, type_constraints, compile_obligations
    calls={'build':0,'layout':0,'semantic':0}
    ws=tmp_path/'ws';ws.mkdir()
    (ws/'candidate.c').write_text('int f(void){return 1;}')
    script=ws/'build.sh';script.write_text('build')
    def shell(cmd,cwd=None,timeout=300):
        calls['build']+=1
        (cwd/'candidate.o').write_bytes(b'object')
        return 0,'score'
    def measure(repo,ws,source,function,target,**kwargs):
        calls['layout']+=1
        return {'source_sha256':'old','layouts':{'X':[]},'receipt_path':'unused'}
    def semantic(panel,state):
        calls['semantic']+=1
        return {'status':'observed_failure','source_sha256':hashlib.sha256(state.source.encode()).hexdigest()}
    monkeypatch.setattr(workspace,'sh',shell)
    monkeypatch.setattr(type_constraints,'measure',measure)
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a:[])
    monkeypatch.setattr(semantic_lane.Panel,'__call__',semantic)
    monkeypatch.setattr(llm,'generate',lambda *a,**k:('x',{}))
    metrics=fast_runtime.install(tmp_path/'cache','pin1',tmp_path/'model.lock')
    cmd=f'. /fake/activate && bash {script} candidate.c'
    workspace.sh(cmd,cwd=ws)
    (ws/'candidate.o').unlink()
    workspace.sh(cmd,cwd=ws)
    assert (ws/'candidate.o').read_bytes()==b'object'
    assert calls['build']==1 and metrics['compile_hits']==1
    (ws/'candidate.c').write_text('changed')
    workspace.sh(cmd,cwd=ws)
    assert calls['build']==2
    one=type_constraints.measure(tmp_path,ws,'int x;','f','t')
    two=type_constraints.measure(tmp_path,ws,'int y;','f','t')
    assert calls['layout']==1 and one['source_sha256']!=two['source_sha256']
    assert Path(two['receipt_path']).exists()
    (ws/'candidate_object_dump_normalized.s').write_text('asm')
    candidate=SimpleNamespace(source='c',object_path=ws/'candidate.o',attempt=SimpleNamespace(compiled=True,frontend={'passed':True}))
    panel=SimpleNamespace(identity='panel1')
    semantic_lane.Panel.__call__(panel,candidate)
    semantic_lane.Panel.__call__(panel,candidate)
    panel.identity='panel2'
    semantic_lane.Panel.__call__(panel,candidate)
    assert calls['semantic']==2 and metrics['semantic_hits']==1
    # A new pin namespace cannot hit an old cache record (fresh process in use).
    monkeypatch.setattr(workspace,'sh',shell)
    monkeypatch.setattr(type_constraints,'measure',measure)
    monkeypatch.setattr(semantic_lane.Panel,'__call__',semantic)
    monkeypatch.setattr(llm,'generate',lambda *a,**k:('x',{}))
    metrics=fast_runtime.install(tmp_path/'cache','pin2',tmp_path/'model.lock')
    workspace.sh(cmd,cwd=ws)
    assert calls['build']==3 and metrics['compile_hits']==0


def test_build_artifacts_are_only_what_this_build_wrote(tmp_path):
    # Motivating residual: a 25 MB compile-cache entry for resolveRaceCourseSurfaceCollisionWithVelocity
    # held ~60 `<fn>_agentrepair_*` outputs of OTHER candidates, all matched by the `<stem>*` glob.
    from eval import fast_runtime
    ws = tmp_path
    source = ws/'fn.c'; source.write_text('int f(void){return 1;}')
    (ws/'fn_agentrepair_retained_1_diff').write_text('debris from another candidate')
    (ws/'fn.o').write_bytes(b'stale object')
    (ws/'fn_meta.json').write_text('{}')
    before = fast_runtime._artifact_stamps(ws, source)
    import os, time
    time.sleep(0.02)
    (ws/'fn.o').unlink(); (ws/'fn.o').write_bytes(b'new object')          # rewritten
    (ws/'fn_object_dump_normalized.s').write_text('asm')                    # created
    assert fast_runtime.built_artifacts(ws, source, before) == ['fn.o', 'fn_object_dump_normalized.s']
    assert fast_runtime.built_artifacts(ws, source, fast_runtime._artifact_stamps(ws, source)) == []


def test_artifact_encoding_roundtrips_and_old_hex_entries_still_replay():
    from eval import fast_runtime
    data = b'\x00object\xff' * 1000
    packed = fast_runtime.pack_artifact(data)
    assert len(packed) < len(data.hex()) / 10
    assert fast_runtime.unpack_artifact(packed, fast_runtime.ARTIFACT_ENCODING) == data
    assert fast_runtime.unpack_artifact(data.hex(), None) == data
    with pytest.raises(ValueError):
        fast_runtime.unpack_artifact(packed, 'lz4')


@pytest.mark.skipif(__import__('os').name=='nt',reason='WSL runtime uses flock')
def test_compile_cache_excludes_workspace_debris_and_replays_old_entries(tmp_path,monkeypatch):
    from eval import fast_runtime
    from solver import workspace
    calls={'build':0}
    ws=tmp_path/'ws';ws.mkdir()
    (ws/'candidate.c').write_text('int f(void){return 1;}')
    (ws/'candidate_agentrepair_retained_1_diff').write_text('x'*100000)
    script=ws/'build.sh';script.write_text('build')
    def shell(cmd,cwd=None,timeout=300):
        calls['build']+=1
        (cwd/'candidate.o').write_bytes(b'object')
        return 0,'score'
    monkeypatch.setattr(workspace,'sh',shell)
    fast_runtime.install(tmp_path/'cache','pin1',tmp_path/'model.lock')
    cmd=f'. /fake/activate && bash {script} candidate.c'
    workspace.sh(cmd,cwd=ws)
    [value]=list((tmp_path/'cache'/'compile').glob('*/*/value.json'))
    record=json.loads(value.read_text())['value']
    assert sorted(record['artifacts'])==['candidate.o'] and record['encoding']==fast_runtime.ARTIFACT_ENCODING
    # An entry written in the old hex format replays unchanged.
    old={'returncode':0,'stdout':'score','artifacts':{'candidate.o':b'old object'.hex()}}
    value.write_text(json.dumps({'sha256':fast_runtime.key(old),'value':old}))
    (ws/'candidate.o').unlink()
    workspace.sh(cmd,cwd=ws)
    assert (ws/'candidate.o').read_bytes()==b'old object' and calls['build']==1


def test_stale_evidence_and_external_source_rejected(tmp_path):
    from eval.fast_campaign import validate_job
    from solver import repair_queue
    source=tmp_path/'source.c';source.write_text('int f(void){return 0;}')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    node={'status':'pending','source':str(source),'source_sha256':digest,'jobs':[], 'residual':{'compiled':True}}
    job={'node':copy.deepcopy(node),'profile':{'evidence_key':repair_queue.evidence_key(node)}}
    result={'source':str(source),'source_sha256':digest}
    validate_job(node,job,result)
    node['semantic_validation']={'status':'observed_failure'}
    with pytest.raises(ValueError,match='stale'):
        validate_job(node,job,result)
    node.pop('semantic_validation')
    source.write_text('changed')
    with pytest.raises(ValueError,match='outside controller'):
        validate_job(node,job,result)


@pytest.mark.parametrize('dispatch', ['wave','pipeline'])
@pytest.mark.parametrize('tasks_per_worker',[1,8])
def test_controller_commits_two_private_results_and_resumes_without_reimport(tmp_path,monkeypatch,dispatch,tasks_per_worker):
    from eval import fast_campaign as fast
    main=tmp_path/'main.sqlite';database(main)
    with sqlite3.connect(main) as conn:
        conn.execute("INSERT INTO functions(addr,name) VALUES (2,'g')")
        inventory=list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    repo=tmp_path/'repo';repo.mkdir()
    nodes={}
    for function in ('f','g'):
        ws=repo/'nonmatchings'/function;ws.mkdir(parents=True)
        source=ws/'source.c';source.write_text('int '+function+'(void){return 0;}')
        nodes[function]={'status':'pending','source':str(source),
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'attempt_id':1,'jobs':[{'profile':'intake'}],'instruction_count':3,'dag_level':0,
            'residual':{'compiled':False}}
    path=tmp_path/'campaign.json';(tmp_path/'campaign-artifacts').mkdir()
    args=SimpleNamespace(resume=True,scheduler='evidence-v1',workers=2,state=path,repo=repo,
        db=main,project=tmp_path,model='fake',endpoint='fake',model_calls=0,timeout=10,
        num_predict=100,worker_root=tmp_path/'workers',max_work_items=2,dispatch=dispatch,
        tasks_per_worker=tasks_per_worker)
    config={k:str(getattr(args,k)) if k in {'repo','db','project'} else getattr(args,k)
            for k in ('repo','db','project','model','endpoint','model_calls','timeout','num_predict','scheduler')}
    value={'kind':'resumable-completion-campaign','config':config,'nodes':nodes,'pins':{},
        'inventory_sha256':fast.campaign.digest(inventory),'model_digest':None}
    campaign_state.atomic(path,value)
    monkeypatch.setattr(fast.campaign,'_pins',lambda *a:{})
    monkeypatch.setattr(fast.campaign.frozen_wavefront,'model_digest',lambda *a:None)
    class Pool:
        def __init__(self,*a,**kw):assert kw['max_tasks_per_child']==tasks_per_worker
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def submit(self,fn,job):
            append(job['db'],job['id'])
            raw={'source':job['node']['source'],'source_sha256':job['node']['source_sha256'],
                 'attempt_id':2,'exact':False,'score':50.,'wall_seconds':1.,
                 'residual':{'compiled':True,'frontend':{'passed':True}},
                 'performance':{'model_seconds':0.,'model_queue_seconds':0.}}
            campaign_state.atomic(job['raw'],raw)
            from concurrent.futures import Future
            future=Future();future.set_result(job['raw'])
            return future
    monkeypatch.setattr(fast,'ProcessPoolExecutor',Pool)
    result=fast.run(args)
    assert result['runtime_options']['tasks_per_worker']==tasks_per_worker
    assert result['fast_metrics']['completed_items']==2
    assert result['fast_inflight']==[]
    assert {n['attempt_id'] for n in result['nodes'].values()}=={2,3}
    assert campaign_state.read(path)==json.loads(json.dumps(result))
    args.tasks_per_worker=8 if tasks_per_worker==1 else 1
    with pytest.raises(ValueError,match='explicit amendment'):
        fast.run(args)
    args.tasks_per_worker=tasks_per_worker
    args.max_work_items=0
    resumed=fast.run(args)
    assert resumed['fast_metrics']['completed_items']==2
    with sqlite3.connect(main) as conn:
        assert conn.execute('SELECT count(*) FROM campaign_worker_imports').fetchone()[0]==2


def test_resource_dispatch_keeps_gpu_busy_without_duplicate_or_reordered_profiles(monkeypatch):
    from eval import fast_campaign as fast
    nodes={name:{'model':name.startswith('m')} for name in ('c1','c2','m1','m2','m3')}
    value={'nodes':nodes,'repair_queue':{'work_items':{
        name:{'function':name,'priority':(i,)} for i,name in enumerate(nodes)}}}
    monkeypatch.setattr(fast.campaign.repair_queue,'next_profile',lambda n,*a:n)
    selected=fast.dispatch_items(value,[],3,2,3)
    assert [r['function'] for r in selected]==['m1','m2','c1']
    jobs=[{'function':'m1','profile':{'model':True}}, {'function':'m2','profile':{'model':True}}]
    assert fast.dispatch_items(value,jobs,1,2,3)[0]['function']=='c1'
    # When CPU capacity is already occupied, the next model receives the slot.
    jobs=[{'function':'c1','profile':{'model':False}}]
    assert fast.dispatch_items(value,jobs,1,1,3)[0]['function']=='m1'
    assert fast.dispatch_items(value,jobs,0,1,3)==[]


@pytest.mark.skipif(__import__('os').name=='nt',reason='WSL runtime uses flock')
def test_model_leases_bound_concurrency_and_release_after_exception(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from eval.fast_runtime import model_lease
    lock=threading.Lock();active=0;peak=0
    def request(i):
        nonlocal active,peak
        try:
            with model_lease(tmp_path/'model.lock',2):
                with lock:active+=1;peak=max(peak,active)
                time.sleep(.03)
                with lock:active-=1
                if i==0:raise RuntimeError('transport failed')
        except RuntimeError:pass
    with ThreadPoolExecutor(5) as pool:list(pool.map(request,range(10)))
    assert peak==2 and active==0
    with model_lease(tmp_path/'model.lock',2):pass


@pytest.mark.skipif(__import__('os').name=='nt',reason='WSL runtime uses flock')
def test_fixed_context_and_model_timing_accounting(tmp_path,monkeypatch):
    from eval import fast_runtime,semantic_lane
    from solver import llm,type_constraints,workspace
    calls=[]
    def generate(*args,**kwargs):
        calls.append(kwargs)
        return 'candidate',{'_request_options':kwargs,'_cache_hit':len(calls)>1,
            'load_duration':2_000_000_000,'prompt_eval_duration':3_000_000_000,
            'eval_duration':4_000_000_000,'prompt_eval_count':128,'eval_count':64}
    monkeypatch.setattr(llm,'generate',generate)
    # Register originals so the process-local runtime wrappers cannot leak
    # across unit tests (production workers are fresh processes).
    monkeypatch.setattr(type_constraints,'measure',type_constraints.measure)
    monkeypatch.setattr(workspace,'sh',workspace.sh)
    monkeypatch.setattr(semantic_lane.Panel,'__call__',semantic_lane.Panel.__call__)
    metrics=fast_runtime.install(tmp_path/'cache','pin',tmp_path/'model.lock')
    llm.generate(prompt='small',num_predict=6000)
    llm.generate(prompt='large'*10000,num_predict=6000)
    assert [c['num_ctx'] for c in calls]==[32768,32768]
    assert metrics['model_calls']==2 and len(metrics['model_requests'])==2
    assert metrics['model_load_seconds']==2 and metrics['model_prompt_seconds']==3
    assert metrics['model_generation_seconds']==4 and metrics['model_generated_tokens']==64
    assert metrics['model_prompt_tokens']==128
