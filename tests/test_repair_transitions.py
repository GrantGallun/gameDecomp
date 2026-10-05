from copy import deepcopy
import json

import pytest

from eval.repair_graph import build_graph
from eval.repair_transitions import estimate, fit, value, validate_model
from eval.search_replay import Policy, digest, run
from eval.search_scheduler import Online
from solver.repair_rules import proposals, state_features
from test_repair_rules import SOURCE, VERDICT, FIX
from test_search_scheduler import compiler, context


def development_world(target="a"*64):
    register = next(r for r in proposals(SOURCE, "__osDequeueThread", VERDICT) if r["action"] == "register_storage:2")
    midway = {**VERDICT, "diff": (FIX / "address_reuse.diff").read_text(), "score": 99.938}
    alias = proposals(register["source"], "__osDequeueThread", midway)[0]
    scores = {SOURCE: 27.6, register["source"]: 99.938, alias["source"]: 100}
    base, _ = compiler(scores)
    def compile_one(source, *_):
        verdict = base(source, *_)
        verdict["verification"]["target_sha256"] = target
        verdict["diff"] = VERDICT["diff"] if source == SOURCE else midway["diff"] if source == register["source"] else ""
        return verdict
    def variants(source, diff):
        row = register if source == SOURCE else alias
        yield row["label"], row["family"], row["source"]
    ctx = {**context(SOURCE), "target_sha256": target, "task": "__osDequeueThread", "partition": "evaluation", "training_eligible": False}
    env = Online(SOURCE, compile_one, variants, ctx)
    assert run(env, Policy("depth", "depth"), 3)["exact"]
    return env.world


def test_two_step_transition_gets_credit_without_claiming_first_edit_exact():
    graph = build_graph([development_world()])
    model = fit(graph, development_targets={"a"*64})
    state = state_features(SOURCE, "__osDequeueThread", VERDICT)
    assert value(model, state, "register_storage:2", horizon=1)["exact_mass"] == 0
    two = value(model, state, "register_storage:2", horizon=2)
    assert two["exact_mass"] == .25
    assert two["outcomes"][0]["continuation"]["action"] == "address_reuse"
    assert two["outcomes"][0]["continuation"]["hypothetical"]


def test_repeated_observations_do_not_inflate_independent_targets():
    first = development_world()
    duplicate = deepcopy(first)
    duplicate["nodes"][0]["verdict"]["seconds"] = 5
    model = fit(build_graph([first,duplicate]), development_targets={"a"*64})
    state = state_features(SOURCE, "__osDequeueThread", VERDICT)
    row = estimate(model, state, "register_storage:2")
    assert row["support_targets"] == 1 and row["unknown_mass"] == .5


def test_separate_targets_add_support_and_unknown_actions_stay_unknown():
    model = fit(build_graph([development_world(), development_world("b"*64)]), development_targets={"a"*64,"b"*64})
    state = state_features(SOURCE, "__osDequeueThread", VERDICT)
    assert estimate(model, state, "register_storage:2")["support_targets"] == 2
    unknown = estimate(model, state, "register_storage:1")
    assert unknown["support_targets"] == 0 and unknown["unknown_mass"] == 1
    assert unknown["outcomes"] == []


def test_development_use_requires_explicit_target_declaration():
    graph = build_graph([development_world()])
    with pytest.raises(ValueError, match="development"):
        fit(graph, development_targets=set())


def test_models_are_serializable_bound_and_not_training_promotion():
    model = fit(build_graph([development_world()]), development_targets={"a"*64})
    restored = json.loads(json.dumps(model))
    assert validate_model(restored) == model and not model["training_eligible"]
    for row in model["rows"].values():
        assert "__osDequeueThread" not in json.dumps(row["state"])
        assert "var_a" not in json.dumps(row["state"])
    restored["rows"].clear()
    with pytest.raises(ValueError, match="checksum"):
        validate_model(restored)


def test_mislabeled_edge_is_not_learned_as_a_guarded_repair():
    w = development_world()
    w["nodes"][1]["family"] = "parameter_reuse"
    with pytest.raises(ValueError, match="guarded"):
        fit(build_graph([w]), development_targets={"a"*64})


def test_horizon_and_unknown_state_bounds():
    model = fit(build_graph([development_world()]), development_targets={"a"*64})
    state = state_features(SOURCE, "__osDequeueThread", VERDICT)
    with pytest.raises(ValueError, match="horizon"):
        value(model, state, "register_storage:2", horizon=0)
    assert value(model, {**state,"domain":"different"}, "register_storage:2", horizon=2)["exact_mass"] == 0


@pytest.mark.parametrize("change", ["mass", "support", "action", "state", "key"])
def test_rehashed_model_requires_valid_conditional_rows(change):
    model = fit(build_graph([development_world()]), development_targets={"a"*64})
    key, row = next(iter(model["rows"].items()))
    if change == "mass":
        row["outcomes"][0]["mass"], row["unknown_mass"] = 200, -99
    elif change == "support":
        row["support_targets"] = 99
    elif change == "action":
        row["action"] = "invented"
    elif change == "state":
        row["outcomes"][0]["state"]["function"] = "answer"
    else:
        model["rows"]["0"*64] = model["rows"].pop(key)
    model["sha256"] = digest({k:v for k,v in model.items() if k != "sha256"})
    with pytest.raises(ValueError):
        validate_model(model)
