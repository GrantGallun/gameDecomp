from pathlib import Path

from solver.repair_rules import action_key, proposals, state_features

FIX = Path(__file__).parent / "fixtures"
SOURCE = (FIX / "register_storage.c").read_text()
DIFF = (FIX / "register_storage.diff").read_text()
VERDICT = {"compiled": True, "exact": False, "score": 27.6, "diff": DIFF}


def test_real_guards_and_components_are_exposed_without_compilation():
    rows = proposals(SOURCE, "__osDequeueThread", VERDICT)
    assert {r["family"] for r in rows} == {"register_storage", "parameter_reuse"}
    keys = {r["action"] for r in rows}
    assert "register_storage:2" in keys and "register_storage:1" in keys
    features = state_features(SOURCE, "__osDequeueThread", VERDICT)
    assert features["residual"] == "spills"
    assert sorted(keys) == features["available"]


def test_features_are_invariant_to_function_local_names_and_score():
    renamed = SOURCE.replace("__osDequeueThread", "unlink").replace("var_a2", "link").replace("var_a3", "node")
    assert state_features(SOURCE, "__osDequeueThread", VERDICT) == state_features(renamed, "unlink", {**VERDICT, "score": 98})


def test_observed_child_prerequisites_reveal_address_reuse():
    row = next(r for r in proposals(SOURCE, "__osDequeueThread", VERDICT) if r["action"] == "register_storage:2")
    next_verdict = {**VERDICT, "diff": (FIX / "address_reuse.diff").read_text(), "score": 99.938}
    state = state_features(row["source"], "__osDequeueThread", next_verdict)
    assert state["residual"] == "load_base" and state["available"] == ["address_reuse"]


def test_failed_or_exact_states_have_no_applicable_actions():
    for verdict in ({**VERDICT, "compiled": False}, {**VERDICT, "exact": True}):
        assert proposals(SOURCE, "__osDequeueThread", verdict) == []


def test_unknown_families_are_not_assigned_an_invented_component_key():
    assert action_key("commutative", SOURCE, SOURCE) is None


def test_recipe_context_and_certificate_only_mismatch_are_preserved():
    first = state_features(SOURCE, "__osDequeueThread", {**VERDICT, "diff": "", "score": 100})
    assert first["residual"] == "certificate_only"
    second = state_features(SOURCE, "__osDequeueThread", {**VERDICT,
        "compiler_recipe": {"settings": {"C_OPT": "-O1"}}})
    assert second["domain"] != first["domain"]
