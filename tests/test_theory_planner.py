from copy import deepcopy

import pytest

from eval.repair_planner import replay_planner, run_planner
from eval.search_replay import digest, validate_world
from eval.theory_planner import TheoryOnline
from solver.repair_theory import validate_map
from test_repair_planner import empty_model
from test_repair_theory import MEMBERS, SIGNATURE, route, verdict
from test_search_scheduler import compiler, context


def setup(*,prior_worlds=(),context_change=None,alternatives=True):
    scores = {"start":-1,"failed":-1,"partial":-1,"exact":100}
    base,receipts = compiler(scores)
    def callback(source,*args):
        result = base(source,*args)
        observed = verdict(*({"start":[SIGNATURE,MEMBERS],"failed":[SIGNATURE,MEMBERS],
                              "partial":[MEMBERS],"exact":[]}[source]))
        result["frontend"].update(observed["frontend"])
        return result
    def routes(source,verdict):
        if source == "start":
            return [route("first","failed"),*([route("different","partial")] if alternatives else [])]
        if source == "partial":
            return [{**route("members","exact"),"addresses":["members"]}]
        return []
    ctx = {**context(),**(context_change or {})}
    env = TheoryOnline("start",callback,lambda *_:iter(()),ctx,inspect_routes=routes,prior_worlds=prior_worlds)
    return env,receipts,routes


def test_negative_approach_triggers_different_route_and_partial_failed_parent_continues():
    env,receipts,routes = setup()
    result = run_planner(env,empty_model(),budget=5)
    assert result["exact"] and result["compiles"] == 4
    assert [s for s,p in receipts] == ["start","failed","partial","exact"]
    assert receipts[-1][1] == 3
    assert [e["local_result"] for e in result["theory"]["effects"]] == ["prediction-not-met","prediction-supported","prediction-supported"]
    assert result["decisions"][1]["reason"] == "theory-alternative"
    assert result["decisions"][1]["theory"]["why"] == "different guarded approach after negative result"
    assert result["theory"]["goal_status"] == "observed-exact"
    for mapping in result["theory"]["maps"].values():
        validate_map(mapping)
    assert replay_planner(env.world,empty_model(),lambda *_:iter(()),budget=5,
        environment_factory=lambda *a,**kw:TheoryOnline(*a,**kw,inspect_routes=routes)) == result


def test_identical_failures_stop_with_open_goal_and_explicit_exhaustion():
    env,receipts,_ = setup(alternatives=False)
    result = run_planner(env,empty_model(),budget=20)
    assert len(receipts) == 2 and not result["exact"]
    assert result["stop"] == "exhausted"
    assert result["theory"]["goal_status"] == "open-current-routes-exhausted"
    assert "impossible" not in result["theory"]["goal_status"]


def test_previous_negative_source_is_not_recompiled_in_same_context():
    old,_,_ = setup(alternatives=False)
    run_planner(old,empty_model(),budget=2)
    env,receipts,_ = setup(prior_worlds=[old.world])
    result = run_planner(env,empty_model(),budget=5)
    assert [s for s,p in receipts] == ["start","partial","exact"]
    assert result["theory"]["suppressed"]


def test_noncompiling_partial_champion_and_tested_routes_remain_inspectable():
    env,_,_ = setup()
    result = run_planner(env,empty_model(),budget=3)
    assert result["theory"]["best_intake_id"] == "root/1"
    states = {r["route"]:r["status"] for r in result["theory"]["route_outcomes"] if r["parent"] == "root"}
    assert states == {"first":"tested-prediction-not-met","different":"tested-local-support"}


def test_intake_champion_prefers_a_certified_exact_child():
    callback,_ = compiler({"start":50,"exact":100})
    env = TheoryOnline("start",callback,lambda *_:iter([("edit","ordinary","exact")]),context(),inspect_routes=lambda *_:[])
    result = run_planner(env,empty_model(),budget=3)
    assert result["theory"]["best_intake_id"] == result["best_id"] == "root/0"


def test_certified_route_has_no_remaining_untested_candidate():
    env,_,_ = setup()
    result = run_planner(env,empty_model(),budget=5)
    assert next(r for r in result["theory"]["route_outcomes"] if r["route"] == "members")["remaining_candidates"] == 0


def test_changed_compiler_context_allows_new_test_of_previously_negative_source():
    old,_,_ = setup(alternatives=False)
    run_planner(old,empty_model(),budget=2)
    env,receipts,_ = setup(prior_worlds=[old.world],context_change={"compiler_sha256":"f"*64})
    run_planner(env,empty_model(),budget=2)
    assert [s for s,p in receipts] == ["start","failed"]


@pytest.mark.parametrize("change",["missing","unavailable","unverified-object","missing-tool"])
def test_inconclusive_prior_receipt_does_not_suppress_a_new_attempt(change):
    old,_,_ = setup(alternatives=False)
    run_planner(old,empty_model(),budget=2)
    v = old.world["nodes"][1]["verdict"]
    if change == "missing":
        v.pop("frontend")
        v.pop("verification")
    elif change == "unavailable":
        v["frontend"].update(passed=None,status="unavailable",diagnostics="checker unavailable")
    else:
        v["frontend"].update(passed=True,status="passed",diagnostics="")
        v["verification"].update(status="unverified",error="unsupported object")
        if change == "missing-tool":
            v["stderr"] = "bash: tools/ido-recomp/linux/cc: No such file or directory"
            v["verification"] = None
        else:
            v["compiled"] = True
    env,receipts,_ = setup(prior_worlds=[old.world])
    run_planner(env,empty_model(),budget=2)
    assert [s for s,p in receipts] == ["start","failed"]


def test_failed_parent_permission_is_explicit_and_action_history_only():
    env,_,_ = setup()
    run_planner(env,empty_model(),budget=3)
    w = deepcopy(env.world)
    validate_world(w)
    del w["allow_failed_parents"]
    with pytest.raises(ValueError,match="parent"):
        validate_world(w)
    w["allow_failed_parents"] = True
    del w["history_kind"]
    with pytest.raises(ValueError,match="action history"):
        validate_world(w)


@pytest.mark.parametrize("budget",[0,1,2])
def test_theory_never_overrides_compile_budget(budget):
    env,receipts,_ = setup()
    result = run_planner(env,empty_model(),budget=budget)
    assert len(receipts) == result["compiles"] == budget and not result["exact"]
