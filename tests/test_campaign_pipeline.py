"""Exercise completion order, slot reuse and pause with durable private receipts."""
from concurrent.futures import Future
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from eval import campaign_state, fast_campaign as fast
from test_campaign_fast import database, append


@pytest.mark.parametrize('pause', [False,True])
def test_rolling_completion_and_pause_drain(tmp_path,monkeypatch,pause):
    main=tmp_path/'main.sqlite';database(main)
    names=['f','g','h','i'];repo=tmp_path/'repo';repo.mkdir()
    with sqlite3.connect(main) as conn:
        for addr,name in enumerate(names[1:],2):conn.execute('INSERT INTO functions(addr,name) VALUES (?,?)',(addr,name))
        inventory=list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    nodes={}
    for name in names:
        directory=repo/'nonmatchings'/name;directory.mkdir(parents=True)
        source=directory/'source.c';source.write_text('int '+name+'(void){return 0;}')
        nodes[name]={'status':'pending','source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'attempt_id':1,'jobs':[{'profile':'intake'}],'instruction_count':3,'dag_level':0,'residual':{'compiled':False}}
    path=tmp_path/'campaign.json';(tmp_path/'campaign-artifacts').mkdir()
    args=SimpleNamespace(resume=True,scheduler='evidence-v1',workers=2,state=path,repo=repo,db=main,
        project=tmp_path,model='fake',endpoint='fake',model_calls=0,timeout=10,num_predict=100,
        worker_root=tmp_path/'workers',max_work_items=4,dispatch='pipeline',model_parallel=1)
    config={k:str(getattr(args,k)) if k in {'repo','db','project'} else getattr(args,k)
            for k in ('repo','db','project','model','endpoint','model_calls','timeout','num_predict','scheduler')}
    campaign_state.atomic(path,{'kind':'resumable-completion-campaign','config':config,'nodes':nodes,'pins':{},
        'inventory_sha256':fast.campaign.digest(inventory),'model_digest':None})
    monkeypatch.setattr(fast.campaign,'_pins',lambda *a:{})
    monkeypatch.setattr(fast.campaign.frozen_wavefront,'model_digest',lambda *a:None)
    submitted=[];imported=[];futures={};jobs_by_name={}
    class Pool:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def submit(self,fn,job):
            name=job['function']
            # Reuse is allowed only after the preceding slot occupant imported.
            for old in submitted:
                if jobs_by_name[old]['slot']==job['slot']:assert old in imported
            submitted.append(name);jobs_by_name[name]=job
            with sqlite3.connect(job['db']) as conn:
                run=job['id']
                conn.execute("INSERT INTO attempt_runs VALUES (?,'test','','{}',1)",(run,))
                attempt_id=conn.execute("INSERT INTO attempts(func_addr,iteration,run_id,parent_attempt_id,source_code,compiled,sampling,created_at) VALUES(1,1,?,1,?,1,'{}',1)",(run,run)).lastrowid
            campaign_state.atomic(job['raw'],{'source':job['node']['source'],'source_sha256':job['node']['source_sha256'],
                'attempt_id':attempt_id,'exact':False,'score':50.,'wall_seconds':1.,'residual':{'compiled':True},
                'performance':{'model_seconds':0.,'model_queue_seconds':0.}})
            future=Future();futures[name]=future
            if name!='f':future.set_result(job['raw'])
            return future
    def wait_for_ready(pending,**kw):
        # f is deliberately slow until every other item has been dispatched,
        # or pause asks the controller to settle the outstanding worker.
        if len(submitted)==4 or (tmp_path/'service.pause').exists():
            if not futures['f'].done():futures['f'].set_result(jobs_by_name['f']['raw'])
        ready={f for f in pending if f.done()}
        assert ready, 'controller failed to refill available capacity'
        return ready,set(pending)-ready
    original_accept=fast.campaign.accept
    def accept(node,profile,result,receipt):
        name=Path(node['source']).parent.name
        original_accept(node,profile,result,receipt)
        imported.append(name)
        # Retire this test node so four independent items bound the run.
        node['status']='parked'
        if pause and name=='g':(tmp_path/'service.pause').touch()
    monkeypatch.setattr(fast,'ProcessPoolExecutor',Pool)
    monkeypatch.setattr(fast,'wait',wait_for_ready)
    monkeypatch.setattr(fast.campaign,'accept',accept)
    value=fast.run(args)
    assert imported[0]=='g' and submitted==(['f','g'] if pause else names)
    assert len(imported)==len(submitted) and value['fast_inflight']==[]
    assert campaign_state.read(path)==json.loads(json.dumps(value))
    with sqlite3.connect(main) as conn:
        assert conn.execute('SELECT count(*) FROM campaign_worker_imports').fetchone()[0]==len(imported)
