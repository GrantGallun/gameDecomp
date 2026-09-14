"""Thin interactive repair runner keeps roots, outputs, and heldout safe."""

from __future__ import annotations

import json
import sqlite3

import pytest

from eval import agentrepair
from solver import refine, workspace


def _database(path):
    conn = sqlite3.connect(path)
    refine.ensure_schema(conn)
    conn.execute(
        "INSERT INTO functions (addr,name,state) VALUES (1,'f','attempted')")
    conn.commit()
    return conn


def test_source_for_attempt_checks_function_identity(tmp_path):
    db = tmp_path / "kb.sqlite"
    conn = _database(db)
    conn.execute(
        "INSERT INTO attempts (func_addr,iteration,source_code,compiled,"
        "created_at) VALUES (1,0,'int f(void){return 0;}',1,1)")
    attempt_id = conn.execute("SELECT id FROM attempts").fetchone()[0]

    assert "return 0" in agentrepair._source_for_attempt(conn, attempt_id, "f")
    with pytest.raises(ValueError, match="not other"):
        agentrepair._source_for_attempt(conn, attempt_id, "other")


def test_provider_factory_loads_model_agnostic_adapter():
    provider = agentrepair._load_provider(
        "solver.modelrepair:OllamaProvider")

    assert provider.provider_id == "ollama"
    assert callable(provider.generate)


def test_transport_failure_survives_worker_json_and_proposal_recording(monkeypatch,tmp_path):
    from solver import llm, modelrepair
    db=tmp_path/'kb.sqlite'
    _database(db).close()
    ws=tmp_path/'ws'
    ws.mkdir()
    monkeypatch.setattr(workspace,'bootstrap',lambda *a:ws)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'score',lambda *a,**k:workspace.Attempt(False,0,False,'compile error','','',77))
    requests=[]
    def send(request,timeout):
        requests.append(timeout)
        raise TimeoutError('synthetic transport')
    monkeypatch.setattr(llm.urllib.request,'urlopen',send)
    monkeypatch.setattr(llm.time,'sleep',lambda seconds:None)
    proposals=[]
    monkeypatch.setattr(modelrepair.attempt_receipts,'record_model_proposal',
        lambda *a,**k:proposals.append(k))
    out=tmp_path/'receipt.json'
    receipt=agentrepair.run(repo=tmp_path,db=db,function='f',source='int f(void){return 0;}',
        source_parent_attempt_id=None,out=out,best_source_out=tmp_path/'best.c',
        model='fake',endpoint='http://local',draws=1,depth=1,beam=1,max_calls=1,
        timeout=240,think='low',num_thread=1,temperature=0,num_predict=10,
        seed=1,cache_dir=None,verbose=False)
    result=json.loads(out.read_text())['result']
    assert result['calls_attempted']==1 and result['generations']==0
    assert requests==[240,240,240]
    assert result['transport_events'][0]['status']=='generation-error'
    attempts=result['transport_events'][0]['attempts']
    assert len(attempts)==3
    assert proposals[0]['status']=='generation-error'
    assert proposals[0]['sampling']['transport_events']==attempts
    assert receipt['result']['transport_events']==result['transport_events']


def test_resilient_worker_bounds_hypothesis_but_retains_full_proposal(monkeypatch,tmp_path):
    from solver import modelrepair, llm
    db=tmp_path/'kb.sqlite'
    _database(db).close()
    ws=tmp_path/'ws'
    ws.mkdir()
    monkeypatch.setattr(workspace,'bootstrap',lambda *a:ws)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    scored=[]
    def score(*a,**k):
        scored.append(a)
        return workspace.Attempt(True,100 if len(scored)>1 else 90,len(scored)>1,'','','',77,
                                 frontend={'passed':True})
    monkeypatch.setattr(workspace,'score',score)
    from eval import semantic_lane
    monkeypatch.setattr(semantic_lane,'Panel',lambda *a:None)
    raw=json.dumps({'kind':'expression','hypothesis':'long explanatory prose '*30,
                    'edits':[{'old':'return 1;','new':'return 2;'}]})
    monkeypatch.setattr(llm,'generate',lambda *a,**k:(raw,{'done_reason':'stop'}))
    proposals=[]
    monkeypatch.setattr(modelrepair.attempt_receipts,'record_model_proposal',lambda *a,**k:proposals.append(k))
    result=agentrepair.run(repo=tmp_path,db=db,function='f',source='int f(void){return 1;}',
        source_parent_attempt_id=None,out=tmp_path/'receipt.json',best_source_out=tmp_path/'best.c',
        model='test',endpoint='unused',draws=1,depth=1,beam=1,max_calls=1,timeout=1,think='low',
        num_thread=1,temperature=0,num_predict=10,seed=1,cache_dir=None,verbose=False,resilient=True)
    assert result['result']['compiling_children']==1
    assert proposals[0]['raw_response']==raw
    assert len(proposals[0]['hypothesis'])<=400


def test_frozen_heldout_is_refused(tmp_path):
    frozen = tmp_path / "set.json"
    frozen.write_text(json.dumps({
        "dev": [], "heldout": [{"function": "secret"}]}))

    with pytest.raises(ValueError, match="held-out"):
        agentrepair._refuse_frozen_heldout(frozen, "secret")
    agentrepair._refuse_frozen_heldout(frozen, "devFunction")

    sets_dir = tmp_path / "sets"
    sets_dir.mkdir()
    (sets_dir / "one.json").write_text(frozen.read_text())
    with pytest.raises(ValueError, match="held-out"):
        agentrepair._refuse_frozen_heldout(sets_dir, "secret")


def test_run_writes_receipt_and_best_source_on_exact_root(monkeypatch, tmp_path):
    db = tmp_path / "kb.sqlite"
    _database(db).close()
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(agentrepair.workspace, "bootstrap",
                        lambda _repo, _function: ws)
    monkeypatch.setattr(agentrepair.workspace, "target_asm",
                        lambda _ws, _function: "glabel f\njr ra\nnop")

    def score(*_args, **_kwargs):
        return workspace.Attempt(True, 100.0, True, "", "", "", 77)

    monkeypatch.setattr(agentrepair.workspace, "score", score)
    out = tmp_path / "receipt.json"
    best = tmp_path / "best.c"
    receipt = agentrepair.run(
        repo=tmp_path, db=db, function="f",
        source="int f(void) { return 0; }", source_parent_attempt_id=None,
        out=out, best_source_out=best, model="test",
        endpoint="provider://unused", draws=1, depth=2, beam=2,
        max_calls=3, timeout=1, think="low", num_thread=1,
        temperature=0.0, num_predict=10, seed=1, cache_dir=None,
        verbose=False)

    assert receipt["result"]["exact"] is True
    assert receipt["result"]["calls_attempted"] == 0
    assert json.loads(out.read_text())["run_id"].startswith("agentrepair-")
    assert best.read_text() == "int f(void) { return 0; }"


def test_resilient_worker_scores_constraint_children_and_preserves_parent(monkeypatch,tmp_path):
    from solver import type_constraints, type_plan
    from eval import semantic_lane
    db=tmp_path/'kb.sqlite'; _database(db).close()
    ws=tmp_path/'ws'; ws.mkdir()
    source='void f(void *p) { p->unk0=1; }'
    child='void f(void *p) { *(int *)p=1; }'
    monkeypatch.setattr(workspace,'bootstrap',lambda *a:ws)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    scored=[]
    def score(*args,**kwargs):
        scored.append(kwargs)
        return workspace.Attempt(len(scored)>1,70 if len(scored)>1 else 0,False,'','','',
            70+len(scored),frontend={'passed':len(scored)>1},compiler_recipe={'target':'build/src/f.o'})
    monkeypatch.setattr(workspace,'score',score)
    monkeypatch.setattr(type_constraints,'measure',lambda *a:{'layouts':{},'probe_object_sha256':'probe','receipt_path':'layout.json'})
    monkeypatch.setattr(type_constraints,'solve',lambda *a,**k:{'plans':[{'hypothesis':'test'}],'status':'candidates'})
    monkeypatch.setattr(type_plan,'apply',lambda *a:(child,{'kind':'test-plan'}))
    monkeypatch.setattr(semantic_lane,'Panel',lambda *a:None)
    receipt=agentrepair.run(repo=tmp_path,db=db,function='f',source=source,source_parent_attempt_id=None,
        out=tmp_path/'result.json',best_source_out=tmp_path/'best.c',model='test',endpoint='unused',
        draws=1,depth=1,beam=2,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,
        num_predict=10,seed=1,cache_dir=None,verbose=False,resilient=True)
    assert receipt['result']['constraint_candidates']==receipt['result']['constraint_compiling_children']==1
    assert receipt['result']['best_attempt_id']==72
    assert scored[1]['parent_attempt_id']==71 and scored[1]['strategy']=='agentrepair-type-constraints'
    assert receipt['result']['calls_attempted']==0


def test_resilient_worker_records_probe_unavailability(monkeypatch,tmp_path):
    from solver import type_constraints
    from eval import semantic_lane
    db=tmp_path/'kb.sqlite'; _database(db).close()
    ws=tmp_path/'ws'; ws.mkdir()
    monkeypatch.setattr(workspace,'bootstrap',lambda *a:ws)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace,'score',lambda *a,**k:workspace.Attempt(False,0,False,'','','',71))
    def unavailable(*a): raise ValueError('no target compiler')
    monkeypatch.setattr(type_constraints,'measure',unavailable)
    monkeypatch.setattr(semantic_lane,'Panel',lambda *a:None)
    receipt=agentrepair.run(repo=tmp_path,db=db,function='f',source='void f(void *p) { p->unk0=1; }',
        source_parent_attempt_id=None,out=tmp_path/'result.json',best_source_out=tmp_path/'best.c',
        model='test',endpoint='unused',draws=1,depth=1,beam=2,max_calls=0,timeout=1,think='low',
        num_thread=1,temperature=0,num_predict=10,seed=1,cache_dir=None,verbose=False,resilient=True)
    assert any(r.get('stage')=='type-constraints' and r.get('status')=='unavailable' for r in receipt['context_reports'])


def test_resilient_worker_runs_existing_compile_recovery_without_model(monkeypatch,tmp_path):
    from solver import compile_recovery, repair_context
    db=tmp_path/'kb.sqlite'; _database(db).close()
    ws=tmp_path/'ws'; ws.mkdir()
    source='int f(void) { return missing(); }'
    child='#include "api.h"\n'+source
    monkeypatch.setattr(workspace,'bootstrap',lambda *a:ws)
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'glabel f\njr ra\nnop')
    scored=[]
    def score(*args,**kwargs):
        scored.append((args[3],kwargs))
        good=args[3]==child
        return workspace.Attempt(good,70 if good else 0,False,'','','',
            80+len(scored),frontend={'passed':good})
    monkeypatch.setattr(workspace,'score',score)
    monkeypatch.setattr(repair_context,'project',lambda *a:(
        '#include "metadata.h"\n'+source,{'stage':'test-context'}))
    def variants(*args):
        assert args[-2]==source and args[-1].receipt_id==81
        return [('compatible-headers',child),('duplicate',child)], [{'stage':'header-context-recovery'}]
    monkeypatch.setattr(compile_recovery,'variants',variants)
    receipt=agentrepair.run(repo=tmp_path,db=db,function='f',source=source,
        source_parent_attempt_id=None,out=tmp_path/'result.json',best_source_out=tmp_path/'best.c',
        model='test',endpoint='unused',draws=1,depth=1,beam=2,max_calls=0,timeout=1,think='low',
        num_thread=1,temperature=0,num_predict=10,seed=1,cache_dir=None,verbose=False,resilient=True)
    assert len(scored)==3
    assert scored[2][1]['parent_attempt_id']==81
    assert scored[2][1]['strategy']=='agentrepair-compile-recovery:compatible-headers'
    assert receipt['result']['best_attempt_id']==83 and receipt['result']['calls_attempted']==0
    assert any(r.get('recovery') for r in receipt['context_reports'])
