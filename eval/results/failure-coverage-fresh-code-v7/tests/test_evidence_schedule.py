from solver import evidence_schedule as schedule


def test_cycles_are_scheduled_as_components_after_external_callees():
    graph = {"a": {"b", "leaf"}, "b": {"a"}, "caller": {"a"}, "leaf": set()}
    levels, groups = schedule.levels(graph)
    assert levels["leaf"] < levels["a"] == levels["b"] < levels["caller"]
    assert groups["a"] == groups["b"] == ["a", "b"]


def test_changed_leaf_invalidates_callers_transitively():
    evidence = {"leaf": {"best_attempt_id": 1},
                "parent": {"best_attempt_id": 2, "dependencies": ["leaf"]},
                "grandparent": {"best_attempt_id": 3, "dependencies": ["parent"]}}
    evidence["parent"]["dependency_fingerprints"] = schedule.pins(["leaf"], evidence)
    evidence["grandparent"]["dependency_fingerprints"] = schedule.pins(["parent"], evidence)
    assert schedule.stale_nodes(evidence) == []
    evidence["leaf"]["best_attempt_id"] = 4
    assert schedule.stale_nodes(evidence) == ["grandparent", "parent"]


def test_missing_dependency_never_counts_as_fresh():
    assert schedule.stale_nodes({"p": {"dependencies": ["missing"],
                                       "dependency_fingerprints": {"missing": None}}}) == ["p"]


def test_stale_contract_is_not_fed_to_caller():
    evidence = {"p": {"dependencies": ["missing"],
                       "verified_callee_contract": {"prototype": "int p(void);"}}}
    assert schedule.render_contracts(["p"], evidence) == ""
    evidence["p"]["dependencies"] = []
    assert "int p(void)" in schedule.render_contracts(["p"], evidence)
