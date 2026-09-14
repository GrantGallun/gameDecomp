import json
import sqlite3
from pathlib import Path

import pytest

from eval import completion_campaign as campaign


def node(level=0, count=10):
    return {"status": "pending", "jobs": [], "dag_level": level, "instruction_count": count}


def result(source="first", exact=False):
    return {"source_sha256": source, "source": source + ".c", "attempt_id": 1,
            "score": 100 if exact else 90, "exact": exact, "frontier": []}


def test_frontend_rejected_exact_candidate_stays_in_model_repair():
    n = node()
    campaign.accept(n, {'name': 'intake'}, {**result(exact=True),
        'residual': {'compiled': True, 'frontend': {'passed': False, 'status': 'rejected'}}}, Path('r.json'))
    assert n['status'] == 'pending'
    assert campaign.next_profile(n, 2)['name'] == 'compile_recovery'


def test_unavailable_frontend_is_operational_blocker_not_model_work():
    n = node()
    campaign.accept(n, {'name': 'intake'}, {**result(exact=True),
        'residual': {'frontend': {'passed': None, 'status': 'unavailable'}}}, Path('r.json'))
    assert n['status'] == 'parked'
    assert n['blocker']['status'] == 'frontend_unavailable'
    assert campaign.next_profile(n, 2) is None


def test_fair_dag_order_and_distinct_retries():
    state = {"config": {"model_calls": 2}, "nodes": {"caller": node(1), "leaf": node()}}
    name, profile = campaign.choose(state)
    assert (name, profile["name"]) == ("leaf", "intake")
    campaign.accept(state["nodes"][name], profile, result(), Path("r.json"))
    assert campaign.choose(state)[0] == "caller"  # no monopoly
    campaign.accept(state["nodes"]["caller"], {"name": "intake"}, result(exact=True), Path("c.json"))
    visited = []
    while (job := campaign.choose(state)):
        name, profile = job
        visited.append(profile["name"])
        campaign.accept(state["nodes"][name], profile, result(), Path("r.json"))
    assert visited == [p["name"] for p in campaign.PROFILES if not p.get('type_transaction')]
    assert campaign.status(state) == "stalled_requires_new_strategy_or_evidence"
    # A new best source makes earlier transformations meaningful again.
    state["nodes"]["leaf"]["source_sha256"] = "improved"
    assert campaign.choose(state)[1]["name"] == "local_rewrites"


def test_budget_and_exact_statuses_never_mean_whole_game():
    state = {"config": {"model_calls": 0}, "nodes": {"f": node()}}
    assert campaign.status(state) == "paused_budget"
    state["nodes"]["f"]["status"] = "object_exact"
    assert campaign.status(state) == "cohort_objects_exact"
    state["nodes"]["f"]["status"] = "integrated"
    assert campaign.status(state) == "cohort_integrated"


def test_function_extent_match_waits_for_integration_not_more_source_repair():
    state = {"config": {"model_calls": 2}, "nodes": {"f": node()}}
    measured = {**result(), "verification": {"exact": False,
        "function_boundary": {"function_exact": True}}}
    campaign.accept(state["nodes"]["f"], {"name": "intake"}, measured, Path("r.json"))
    assert state["nodes"]["f"]["status"] == "function_exact_pending_integration"
    assert campaign.choose(state) is None
    assert campaign.status(state) == "awaiting_integration"


def test_resume_and_input_change(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE functions(name,addr,size,insn_count)")
        conn.execute("INSERT INTO functions VALUES('f',4096,16,4)")
    monkeypatch.setattr(campaign.callgraph, "edges", lambda conn: ({}, {}))
    pinned = tmp_path / "input"
    pinned.write_text("v1")
    monkeypatch.setattr(campaign, "_pins", lambda *a: campaign.frozen_wavefront.file_hashes([pinned]))
    calls = []

    def execute(**kw):
        calls.append(kw["profile"]["name"])
        return result()

    monkeypatch.setattr(campaign, "execute", execute)
    kwargs = dict(repo=tmp_path, project=tmp_path, db=db, state_path=tmp_path / "state.json",
                  functions=("f",), model_calls=0, max_work_items=1)
    first = campaign.run(**kwargs)
    assert first["status"] == "paused_budget"
    second = campaign.run(**kwargs, resume=True)
    assert calls == ["intake", "local_rewrites"]
    assert len(second["nodes"]["f"]["jobs"]) == 2
    pinned.write_text("v2")
    third = campaign.run(**kwargs, resume=True)
    assert third["status"] == "paused_inputs_changed"
    assert len(calls) == 2


def test_lock_refuses_concurrent_controller(tmp_path):
    with campaign.campaign_lock(tmp_path / "lock"):
        with pytest.raises(OSError):
            with campaign.campaign_lock(tmp_path / "lock"):
                pytest.fail("second owner acquired campaign")


def test_parked_target_does_not_block_siblings_or_retry():
    state = {"config": {"model_calls": 2}, "nodes": {"hw": node(), "f": node(1)}}
    campaign.accept(state["nodes"]["hw"], {"name": "intake"},
                    {"status": "parked", "blocker": {"opcode": "mfc0"}}, Path("hw.json"))
    assert campaign.choose(state)[0] == "f"
    assert campaign.next_profile(state["nodes"]["hw"], 2) is None


def test_completed_durable_job_recovers_without_reexecution(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE functions(name,addr,size,insn_count)")
        conn.execute("INSERT INTO functions VALUES('f',4096,16,4)")
    monkeypatch.setattr(campaign.callgraph, "edges", lambda conn: ({}, {}))
    monkeypatch.setattr(campaign, "_pins", lambda *a: {})
    kwargs = dict(repo=tmp_path, project=tmp_path, db=db, state_path=tmp_path / "state.json",
                  functions=("f",), model_calls=0)
    state = campaign.run(**kwargs, max_work_items=0)
    receipt = tmp_path / "completed.json"
    receipt.write_text(json.dumps(result(exact=True)))
    state["inflight"] = {"function": "f", "profile": "intake", "receipt": str(receipt)}
    kwargs["state_path"].write_text(json.dumps(state))
    monkeypatch.setattr(campaign, "execute", lambda **kw: pytest.fail("reexecuted durable result"))
    resumed = campaign.run(**kwargs, resume=True, max_work_items=1)
    assert resumed["status"] == "cohort_objects_exact"
    assert len(resumed["nodes"]["f"]["jobs"]) == 1


def test_intake_routes_do_loop_failure_to_existing_lowering(tmp_path, monkeypatch):
    from solver import workspace
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "base.c").write_text('void f(void) { do { work(); } while (ready()); }')
    monkeypatch.setattr(campaign.workspace, "bootstrap", lambda *a: ws)
    monkeypatch.setattr(campaign.workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(campaign.m2c_context, "seed_variants", lambda *a: ([], []))
    monkeypatch.setattr(campaign.project_headers, "preflight_variants", lambda *a: [])
    observed = []
    def score(ws, repo, tag, source, **kw):
        observed.append((source, kw))
        if "do {" in source:
            return workspace.Attempt(False, 0, False, "", "ERROR: The C file contains a do-while loop.", "", 1)
        return workspace.Attempt(True, 90, False, "diff", "", "", 2)
    monkeypatch.setattr(campaign.workspace, "score", score)
    result = campaign._intake(repo=tmp_path, db=db, function="f", node={}, out=tmp_path / "r.json")
    assert result["score"] == 90
    assert "for (;;)" in observed[1][0]
    assert observed[1][1]["parent_attempt_id"] == 1


def test_historical_intake_reapplies_header_context(tmp_path, monkeypatch):
    from solver import workspace
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    source = "void f(Task *t) {}"
    contextual = '#include "task.h"\n' + source
    monkeypatch.setattr(campaign.workspace, "bootstrap", lambda *a: tmp_path)
    monkeypatch.setattr(campaign.workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(campaign.agentrepair, "_source_for_attempt", lambda *a: source)
    monkeypatch.setattr(campaign.project_headers, "preflight_variants",
                        lambda *a: [("project_header:task.h", contextual)])
    observed = []
    def score(ws, repo, tag, candidate, **kw):
        observed.append((candidate, kw))
        compiled = candidate == contextual
        return workspace.Attempt(compiled, 90 if compiled else 0, False,
                                 "", "" if compiled else "unknown Task", "", len(observed))
    monkeypatch.setattr(campaign.workspace, "score", score)
    result = campaign._intake(repo=tmp_path, db=db, function="f",
                             node={"seed_attempt_id": 42}, out=tmp_path / "r.json")
    assert result["score"] == 90
    assert [row[0] for row in observed] == [source, contextual]
    assert all(row[1]["parent_attempt_id"] == 42 for row in observed)


def test_forked_intake_keeps_failed_context_and_correct_parent(tmp_path, monkeypatch):
    import hashlib
    from solver import workspace
    db = tmp_path / 'db.sqlite'
    sqlite3.connect(db).close()
    raw = 'void f(? x) {}'
    contextual = '#include "task.h"\nvoid f(Task *x) { x->unknown = 1; }'
    monkeypatch.setattr(campaign.workspace, 'bootstrap', lambda *a: tmp_path)
    monkeypatch.setattr(campaign.workspace, 'target_asm', lambda *a: 'glabel f')
    monkeypatch.setattr(campaign.agentrepair, '_source_for_attempt', lambda conn, id, fn: {42:raw,43:contextual}[id])
    monkeypatch.setattr(campaign.project_headers, 'preflight_variants', lambda *a: [])
    parents = []
    def score(*args, **kw):
        parents.append(kw['parent_attempt_id'])
        return workspace.Attempt(False, 0, False, '', 'syntax error', '', 100 + len(parents))
    monkeypatch.setattr(campaign.workspace, 'score', score)
    n = {'seed_attempt_id': 42, 'seed_frontier': [{'attempt_id': 43,
         'source_sha256': hashlib.sha256(contextual.encode()).hexdigest()}]}
    measured = campaign._intake(repo=tmp_path, db=db, function='f', node=n, out=tmp_path/'r.json')
    assert parents == [42,43]
    assert measured['attempt_id'] == 102
    assert [s['attempt_id'] for s in measured['frontier']] == [102,101]
    n['seed_frontier'][0]['source_sha256'] = 'tampered'
    with pytest.raises(ValueError, match='identity changed'):
        campaign._intake(repo=tmp_path, db=db, function='f', node=n, out=tmp_path/'bad.json')


def test_unsupported_integration_does_not_block_exact_siblings(tmp_path, monkeypatch):
    def prepare(**kw):
        if kw["entries"][0]["function"] == "needs_shared_types":
            raise ValueError("shared declarations")
        return tmp_path / "manifest.json"
    monkeypatch.setattr(campaign.prepare_integration, "prepare", prepare)
    entries = [{"function": "needs_shared_types"}, {"function": "ordinary"}]
    eligible, blocked = campaign.preflight_integration(repo=tmp_path, db=tmp_path / "db",
        entries=entries, artifacts=tmp_path, tag="test")
    assert eligible == [entries[1]]
    assert blocked[0]["functions"] == ["needs_shared_types"]


def test_exact_context_draft_reconciled_and_recompiled_before_integration(tmp_path, monkeypatch):
    from solver import workspace
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    raw = "extern int g;\nvoid f(void) {g=1;}"
    clean = '#include "g.h"\nvoid f(void) {g=1;}'
    (tmp_path / "base.c").write_text(raw)
    monkeypatch.setattr(campaign.workspace, "bootstrap", lambda *a: tmp_path)
    monkeypatch.setattr(campaign.workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(campaign.m2c_context, "seed_variants", lambda *a: ([("context", raw)], []))
    monkeypatch.setattr(campaign.project_headers, "preflight_variants", lambda *a: [("reconciled", clean)])
    observed = []
    def score(ws, repo, tag, candidate, **kw):
        observed.append(candidate)
        return workspace.Attempt(True, 100, True, "", "", "", len(observed))
    monkeypatch.setattr(campaign.workspace, "score", score)
    result = campaign._intake(repo=tmp_path, db=db, function="f", node={}, out=tmp_path / "r.json")
    assert result["attempt_id"] == 2
    assert observed == [raw, clean]
    assert (tmp_path / "r.best.c").read_text() == clean


def test_extra_prototypes_cannot_preempt_exact_real_header_type_repair(tmp_path, monkeypatch):
    from solver import workspace
    db = tmp_path / 'db.sqlite'
    sqlite3.connect(db).close()
    raw = 'void cb(void *);\nvoid f(void) {work(cb);}'
    real = '#include "callbacks.h"\nvoid f(void) {work(cb);}'
    (tmp_path / 'base.c').write_text(raw)
    monkeypatch.setattr(campaign.workspace, 'bootstrap', lambda *a: tmp_path)
    monkeypatch.setattr(campaign.workspace, 'target_asm', lambda *a: 'glabel f')
    monkeypatch.setattr(campaign.m2c_context, 'seed_variants', lambda *a: ([], []))
    monkeypatch.setattr(campaign.project_headers, 'preflight_variants', lambda *a: [('real-header', real)])
    def score(ws, repo, tag, candidate, **kw):
        return workspace.Attempt(True, 100, True, '', '', '', 1 if candidate == raw else 2,
            frontend={'passed': candidate == raw, 'status': 'passed' if candidate == raw else 'rejected'})
    monkeypatch.setattr(campaign.workspace, 'score', score)
    measured = campaign._intake(repo=tmp_path, db=db, function='f', node={}, out=tmp_path/'r.json')
    assert measured['attempt_id'] == 2 and measured['exact'] is False
    assert measured['residual']['exact'] is True
    assert [s['attempt_id'] for s in measured['frontier']] == [2]


def test_noncompiling_source_skips_instruction_rewrites():
    item = {**node(), "source_sha256": "draft", "residual": {"compiled": False}}
    assert campaign.next_profile(item, 0)["name"] == "compile_recovery"
    campaign.accept(item, campaign.next_profile(item, 0),
                    {**result("draft"), "residual": {"compiled": False}}, Path("r.json"))
    assert campaign.next_profile(item, 1)["name"] == "schema_patch"
    assert campaign.next_profile(item, 0) is None


def test_compile_recovery_context_churn_yields_to_model():
    item = {'status':'pending', 'source_sha256':'third-context',
            'residual':{'compiled':False, 'frontend':{'passed':False}},
            'jobs':[{'profile':'compile_recovery','source_sha256':'first-context'},
                    {'profile':'compile_recovery','source_sha256':'second-context'}]}
    assert campaign.next_profile(item, 1)['name'] == 'schema_patch'
    assert campaign.next_profile(item, 0) is None
    item['jobs'].append({'profile':'schema_patch','source_sha256':'third-context'})
    item['source_sha256'] = 'model-child'
    assert campaign.next_profile(item, 1)['name'] == 'compile_recovery'


def test_compile_recovery_retries_only_for_changed_source_and_hands_off():
    item = {**node(), 'source_sha256': 'draft', 'residual': {'compiled': False}}
    profile = campaign.next_profile(item, 0)
    campaign.accept(item, profile, {**result('draft'), 'residual': {'compiled': False}}, Path('r.json'))
    assert campaign.next_profile(item, 0) is None
    item['source_sha256'] = 'new-draft'
    assert campaign.next_profile(item, 0)['name'] == 'compile_recovery'
    campaign.accept(item, profile, {**result('compiled'),
        'residual': {'compiled': True, 'frontend': {'passed': True}}}, Path('c.json'))
    assert campaign.next_profile(item, 0)['name'] == 'semantic_handoff'


def test_zero_model_recovery_dispatches_actual_resilient_worker(tmp_path, monkeypatch):
    import hashlib
    source = 'void f(void *p) { p->unk0 = 1; }'
    path = tmp_path / 'candidate.c'
    path.write_text(source)
    item = {**node(), 'source': str(path), 'attempt_id': 42,
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'residual': {'compiled': False}}
    calls = []
    def worker(**kwargs):
        calls.append(kwargs)
        return {'result': {'best_attempt_id': 43, 'best_source_path': str(path),
            'best_source_sha256': item['source_sha256'],
            'best_residual': {'compiled': True, 'weighted_progress_score': 90}}}
    monkeypatch.setattr(campaign.agentrepair, 'run', worker)
    config = dict(model='test', endpoint='unused', model_calls=0, timeout=1, num_predict=100)
    profile = campaign.next_profile(item, 0)
    measured = campaign.execute(repo=tmp_path, db=tmp_path/'db', function='f', node=item,
        profile=profile, config=config, out=tmp_path/'r.json')
    assert measured['attempt_id'] == 43
    assert calls[0]['resilient'] is True and calls[0]['max_calls'] == 0
    assert calls[0]['deterministic_budget'] == 0 and calls[0]['compile_only'] is False
    assert calls[0]['source_parent_attempt_id'] == 42


def test_compile_sweep_covers_failures_without_spending_on_byte_polish():
    compiling = {**node(), 'source_sha256':'a', 'residual':{'compiled':True}}
    failing = {**node(count=100), 'source_sha256':'b', 'residual':{'compiled':False}}
    state = {'config':{'model_calls':2,'compile_sweep':True}, 'nodes':{'easy':compiling,'hard':failing}}
    assert campaign.choose(state)[0] == 'hard'
    failing['residual']['compiled'] = True
    assert campaign.choose(state) is None
    assert campaign.status(state) == 'compile_sweep_complete'
    failing.update(status='parked', residual={'compiled':True,
        'frontend':{'passed':None,'status':'unavailable'}})
    assert campaign.status(state) == 'compile_sweep_stalled'


@pytest.mark.parametrize("final_union_fails", [False, True])
def test_integration_bisects_and_requires_surviving_union(tmp_path, monkeypatch, final_union_fails):
    batches = []
    def prepare(**kw):
        batch = [v["function"] for v in kw["entries"]]
        batches.append(batch)
        return batch
    monkeypatch.setattr(campaign.prepare_integration, "prepare", prepare)
    def run(**kw):
        failed = "bad" in kw["manifest"] or (final_union_fails and kw["manifest"] == ["a", "c"])
        return {"status": "build_failed" if failed else "rom_exact", "whole_rom_verified": not failed}
    monkeypatch.setattr(campaign.integration_gate, "run", run)
    entries = [{"function": n} for n in ("a", "bad", "c")]
    accepted, records = campaign.integrate_candidates(repo=tmp_path, db=tmp_path / "db",
        entries=entries, artifacts=tmp_path)
    assert batches[-1] == ["a", "c"]
    assert accepted == ([] if final_union_fails else [entries[0], entries[2]])


def test_explicit_build_stop_prevents_further_integration_attempts(tmp_path, monkeypatch):
    log = tmp_path / "log"
    log.write_text("STOP. Do not continue.")
    calls = []
    monkeypatch.setattr(campaign.prepare_integration, "prepare", lambda **kw: tmp_path / "manifest")
    def run(**kw):
        calls.append(kw)
        return {"status": "build_failed", "whole_rom_verified": False, "build_log": str(log)}
    monkeypatch.setattr(campaign.integration_gate, "run", run)
    accepted, records = campaign.integrate_candidates(repo=tmp_path, db=tmp_path / "db",
        entries=[{"function": "a"}, {"function": "b"}], artifacts=tmp_path)
    assert not accepted and len(calls) == 1
    assert records[-1]["status"] == "integration_halted"
