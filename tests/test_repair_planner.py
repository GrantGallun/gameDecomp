from copy import deepcopy

import pytest

from eval.repair_graph import build_graph
from eval.repair_planner import ActionOnline, replay_planner, run_planner
from eval.repair_transitions import fit
from eval.search_replay import Policy, Replay, merge_worlds, run
from eval.search_scheduler import Online
from solver.repair_rules import proposals
from test_repair_rules import SOURCE, VERDICT, FIX
from test_repair_transitions import development_world
from test_search_scheduler import compiler, context


def empty_model():
    return fit(build_graph([]), development_targets=set())


def test_no_support_falls_back_to_existing_depth_search():
    scores = {"start":10,"a":11,"b":40,"shared":50}
    def variants(source,diff):
        for child in {"start":["a","b"],"a":["shared"],"b":["shared"]}.get(source,[]):
            yield child,"unmodeled",child
    base, base_rows = compiler(scores)
    candidate, candidate_rows = compiler(scores)
    policy = Policy("depth", "depth", 1.18754)
    old = Online("start",base,variants,context())
    new = ActionOnline("start",candidate,variants,context())
    before = run(old,policy,5)
    after = run_planner(new,empty_model(),budget=5,fallback=policy)
    assert before["trace"] == after["trace"] and base_rows == candidate_rows
    assert all(d["reason"] == "fallback" for d in after["decisions"])


def test_two_step_plan_preserves_low_score_intermediate_and_actual_parent():
    learned = fit(build_graph([development_world()]),development_targets={"a"*64})
    register = next(r for r in proposals(SOURCE,"__osDequeueThread",VERDICT) if r["action"]=="register_storage:2")
    midway = {**VERDICT,"diff":(FIX/"address_reuse.diff").read_text()}
    alias = proposals(register["source"],"__osDequeueThread",midway)[0]
    scores = {SOURCE:27.6,"decoy":80,register["source"]:15,alias["source"]:100}
    base, receipts = compiler(scores)
    def compile_one(source,*args):
        result=base(source,*args)
        result["diff"] = VERDICT["diff"] if source==SOURCE else midway["diff"] if source==register["source"] else ""
        return result
    def variants(source,diff):
        if source==SOURCE:
            yield "decoy","unmodeled","decoy"
            yield register["label"],register["family"],register["source"]
        elif source==register["source"]:
            yield alias["label"],alias["family"],alias["source"]
    env=ActionOnline(SOURCE,compile_one,variants,{**context(SOURCE),"task":"__osDequeueThread"})
    result=run_planner(env,learned,budget=3)
    assert result["exact"] and result["compiles"]==3
    assert [r[0] for r in receipts]==[SOURCE,register["source"],alias["source"]]
    assert receipts[-1][1]==2
    assert result["decisions"][0]["prediction"]["exact_mass"]==.25
    assert len(build_graph([env.world])["nodes"])==3


def test_hypothetical_continuation_is_never_compiled_without_new_guard_evidence():
    learned=fit(build_graph([development_world()]),development_targets={"a"*64})
    register=next(r for r in proposals(SOURCE,"__osDequeueThread",VERDICT) if r["action"]=="register_storage:2")
    callback,rows=compiler({SOURCE:27.6,register["source"]:60})
    def compile_one(source,*args):
        verdict=callback(source,*args)
        verdict["diff"]=VERDICT["diff"] if source==SOURCE else ""
        return verdict
    def variants(source,diff):
        for row in proposals(source,"__osDequeueThread",{**VERDICT,"diff":diff}):
            if row["action"]=="register_storage:2" or row["family"]=="address_reuse":
                yield row["label"],row["family"],row["source"]
    result=run_planner(ActionOnline(SOURCE,compile_one,variants,{**context(SOURCE),"task":"__osDequeueThread"}),learned,budget=8)
    assert not result["exact"] and len(rows)==2 and result["stop"]=="exhausted"


def test_zero_budget_and_infrastructure_error_are_explicit():
    calls=[]
    def fail(*args):
        calls.append(args)
        raise RuntimeError("broken compiler")
    env=ActionOnline("start",fail,lambda *_:iter(()),context())
    assert run_planner(env,empty_model(),budget=0)["compiles"]==0 and not calls
    result=run_planner(env,empty_model(),budget=2)
    assert result["compiles"]==1 and not result["complete"]
    assert result["stop"]=="infrastructure-error"


def test_infinite_duplicate_stream_is_bounded_and_not_called_exhausted():
    callback,rows=compiler({"start":10})
    def variants(*args):
        while True:
            yield "duplicate","unmodeled","start"
    result=run_planner(ActionOnline("start",callback,variants,context()),empty_model(),budget=3)
    assert len(rows)==1 and not result["complete"] and result["stop"]=="preview-limit"


def test_preview_does_not_compile_and_cannot_commit_twice():
    callback,rows=compiler({"start":10,"child":20})
    env=ActionOnline("start",callback,lambda *_:iter([("edit","unmodeled","child")]),context())
    env.start()
    proposal=env.propose("root",8)[0]
    assert len(rows)==1
    assert "root" not in env.world["closed"]
    env.commit(proposal["id"])
    assert "root" in env.world["closed"]
    with pytest.raises(ValueError,match="proposal"):
        env.commit(proposal["id"])


def test_score_100_without_exact_certificate_does_not_end_search():
    callback,rows=compiler({"start":99})
    def compile_one(*args):
        result=callback(*args)
        result["score"]=100
        return result
    result=run_planner(ActionOnline("start",compile_one,lambda *_:iter(()),context()),empty_model(),budget=1)
    assert result["best_score"]==100 and not result["exact"]


def test_prediction_respects_remaining_depth():
    learned = fit(build_graph([development_world()]), development_targets={"a"*64})
    register = next(r for r in proposals(SOURCE,"__osDequeueThread",VERDICT) if r["action"]=="register_storage:2")
    callback, _ = compiler({SOURCE:27.6, register["source"]:60})
    def compile_one(source,*args):
        verdict = callback(source,*args)
        verdict["diff"] = VERDICT["diff"]
        return verdict
    env = ActionOnline(SOURCE,compile_one,lambda *_:iter([(register["label"],register["family"],register["source"])]),
                       {**context(SOURCE),"task":"__osDequeueThread"},max_depth=1)
    result = run_planner(env,learned,budget=3)
    assert result["decisions"][0]["prediction"]["exact_mass"] == 0
    assert result["decisions"][0]["reason"] == "fallback"


def test_action_histories_cannot_masquerade_as_fixed_generator_replays():
    callback, _ = compiler({"start":10})
    env = ActionOnline("start",callback,lambda *_:iter(()),context())
    run_planner(env,empty_model(),budget=1)
    with pytest.raises(ValueError,match="action history"):
        Replay(env.world)
    with pytest.raises(ValueError,match="action history"):
        merge_worlds([env.world])


def test_proposal_aware_replay_requires_the_exact_observed_action_sequence():
    def variants(*args):
        yield "child", "unmodeled", "child"
    callback, _ = compiler({"start":10,"child":30})
    env = ActionOnline("start",callback,variants,context())
    result = run_planner(env,empty_model(),budget=2)
    assert replay_planner(env.world,empty_model(),variants,budget=2) == result
    with pytest.raises(ValueError,match="replay"):
        replay_planner(env.world,empty_model(),lambda *_:iter([("different","unmodeled","unknown")]),budget=2)
