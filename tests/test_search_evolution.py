"""Automatic proposals and advancement must be earned by compiler observations."""
from copy import deepcopy
import importlib

import pytest
from test_search_replay import node, world
from eval.search_replay import Policy


def api():
    return importlib.import_module("eval.search_evolution")


def research(nodes=None, name="research"):
    value = world(nodes)
    value["context"].update(task=name, partition="research")
    return value


def test_proposals_are_deterministic_bounded_and_respond_to_observed_progress():
    parent = Policy("S0", "breadth")
    w = research([node("root"), node("root/0", "root", score=18)])
    proposals = api().propose([w], parent)
    assert proposals == api().propose([w], parent)
    assert len(proposals) <= 4
    assert any(p.mode == "depth" and p.quantum == 8 for p in proposals)
    changed = research([node("root"), node("root/0", "root", score=12)])
    assert proposals != api().propose([changed], parent)


def test_evaluation_worlds_cannot_become_proposal_data():
    w = research()
    w["context"]["partition"] = "evaluation"
    with pytest.raises(ValueError, match="research"):
        api().propose([w], Policy("S0", "breadth"))


def test_proposer_samples_between_separated_positive_step_scales():
    w = research([node("root"), node("root/0", "root", score=10.25),
                  node("root/1", "root", 1, score=10.25),
                  node("root/2", "root", 2, score=19),
                  node("root/3", "root", 3, score=19)])
    proposals = api().propose([w], Policy("parent", "breadth"))
    assert any(p.mode == "depth" and p.quantum == 1.5 for p in proposals)


def test_known_development_regression_filters_a_fast_but_brittle_candidate():
    calibration = research([node("root"), node("root/0", "root", score=30),
                            node("root/1", "root", 1, score=11),
                            node("root/0/0", "root/0", score=100, exact=True)])
    regression = research([node("root", score=97), node("root/0", "root", score=97.5),
                           node("root/1", "root", 1, score=100, exact=True),
                           node("root/0/0", "root/0", score=97.5)], "known_plateau")
    regression["context"]["partition"] = "evaluation"
    choices = [Policy("greedy", "greedy"), Policy("moderate", "depth", 1)]
    selected = api().select([calibration], Policy("parent", "breadth"), budget=3, proposals=choices,
        regressions=[{"kind": "development-regression", "world": regression}])
    assert selected["candidate"]["name"] == "moderate"
    assert selected["policies"][1]["regression_lost"] == ["known_plateau"]
    assert regression["context"]["partition"] == "evaluation", "origin partition is preserved"


def test_regression_use_must_be_explicit_and_cannot_silently_relabel_evaluation():
    with pytest.raises(ValueError, match="development-regression"):
        api().select([research()], Policy("parent", "breadth"), budget=3,
                     regressions=[{"world": research(name="evaluation")}])


def test_selector_automatically_finds_the_motivating_exact_path():
    w = research([node("root"), node("root/0", "root", score=20),
                  node("root/1", "root", 1, score=15),
                  node("root/0/0", "root/0", score=100, exact=True)])
    selected = api().select([w], Policy("S0", "breadth"), budget=3)
    assert selected["candidate"]["mode"] == "greedy"
    assert selected["eligible"] and selected["candidate_results"][0]["exact"]


def test_missing_coverage_retains_parent():
    selected = api().select([research()], Policy("S0", "breadth"), budget=4)
    assert not selected["eligible"] and selected["candidate"]["name"] == "S0"


def test_gate_replays_actual_certificates_and_advances_on_gain():
    w = research([node("root"), node("root/0", "root", score=20),
                  node("root/1", "root", 1, score=15),
                  node("root/0/0", "root/0", score=100, exact=True)], "evaluation")
    w["context"]["partition"] = "evaluation"
    verdict = api().gate([w], [w], Policy("S0", "breadth"), Policy("S1", "greedy"), budget=3,
                         research_tasks={"research"})
    assert verdict["advance"] and verdict["gained"] == ["evaluation"] and verdict["lost"] == []


def test_efficiency_alone_does_not_advance():
    w = research([node("root"), node("root/0", "root", score=20),
                  node("root/1", "root", 1, score=15),
                  node("root/0/0", "root/0", score=100, exact=True)], "evaluation")
    w["context"]["partition"] = "evaluation"
    w["closed"] = ["root"]
    verdict = api().gate([w], [w], Policy("S0", "breadth"), Policy("S1", "greedy"), budget=4,
                         research_tasks={"research"})
    assert not verdict["advance"] and verdict["reason"] == "no certified gain"


@pytest.mark.parametrize("damage", ["overlap", "context", "incomplete", "certificate", "duplicate", "empty"])
def test_gate_rejects_invalid_or_contaminated_panels(damage):
    w = research([node("root"), node("root/0", "root", score=100, exact=True)], "evaluation")
    w["context"]["partition"] = "evaluation"
    candidate = deepcopy(w)
    exclusions = {"research"}
    parents, candidates = [w], [candidate]
    if damage == "overlap": exclusions.add("evaluation")
    if damage == "context": candidate["context"]["compiler_sha256"] = "f" * 64
    if damage == "incomplete": candidate["nodes"] = candidate["nodes"][:1]
    if damage == "certificate": candidate["nodes"][-1]["verdict"]["verification"]["exact"] = False
    if damage == "duplicate": candidates.append(candidate)
    if damage == "empty": parents, candidates = [], []
    with pytest.raises(ValueError):
        api().gate(parents, candidates, Policy("S0", "breadth"), Policy("S1", "greedy"), budget=3,
                   research_tasks=exclusions)


@pytest.mark.parametrize("conflict", ["verdict", "exhaustion"])
def test_paired_environment_contradictions_cannot_count_as_policy_improvement(conflict):
    parent = research([node("root"), node("root/0", "root", score=20)], "evaluation")
    parent["context"]["partition"] = "evaluation"
    child = deepcopy(parent)
    if conflict == "verdict":
        child["nodes"][1] = node("root/0", "root", score=100, exact=True)
    else:
        parent["closed"] = ["root"]
        child["nodes"].append(node("root/1", "root", 1, score=100, exact=True))
    with pytest.raises(ValueError, match="conflicting"):
        api().gate([parent], [child], Policy("parent", "breadth"), Policy("child", "breadth"),
                   budget=2, research_tasks={"research"})


def test_selector_cannot_trade_an_incumbent_exact_for_another_task():
    easy_for_breadth = research([node("root"), node("root/0", "root", score=90),
        node("root/1", "root", 1, score=100, exact=True),
        node("root/0/0", "root/0", score=95)], "retention")
    easy_for_greedy = research([node("root"), node("root/0", "root", score=20),
        node("root/1", "root", 1, score=11),
        node("root/0/0", "root/0", score=100, exact=True)], "gain")
    result = api().select([easy_for_breadth, easy_for_greedy], Policy("parent", "breadth"),
                          budget=3, proposals=[Policy("candidate", "greedy")])
    assert result["candidate"]["name"] == "parent"
    assert result["policies"][1]["lost"] == ["retention"]


def test_observed_infrastructure_error_invalidates_selection_and_evaluation():
    w = research([node("root"), node("root/0", "root", compiled=False, score=0)])
    w["nodes"][-1]["verdict"]["error"] = "compiler process crashed"
    assert not api().select([w], Policy("parent", "breadth"), budget=2)["eligible"]
    w["context"]["partition"] = "evaluation"
    with pytest.raises(ValueError, match="infrastructure"):
        api().gate([w], [w], Policy("parent", "breadth"), Policy("candidate", "greedy"),
                   budget=2, research_tasks=set())


def test_compiler_exception_is_logged_and_retains_infrastructure_classification(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    from solver import workspace
    conn = sqlite3.connect(":memory:")
    conn.executescript((Path(__file__).resolve().parents[1] / "kb/schema.sql").read_text())
    conn.execute("INSERT INTO functions(addr,name) VALUES(1,'probe')")
    conn.commit()
    def fail(*args, **kwargs):
        raise RuntimeError("compiler unavailable")
    monkeypatch.setattr(workspace, "score", fail)
    verdict = api().compile_logged(tmp_path, tmp_path, "probe", "int probe;", conn=conn, run_id="test")
    assert verdict["error"] == "compiler-infrastructure-exception"
    assert not verdict["compiled"] and not verdict["exact"]
    assert conn.execute("SELECT compiler_stderr FROM attempts WHERE id=?", (verdict["receipt_id"],)).fetchone() == (
        "RuntimeError: compiler unavailable",)


def test_later_round_must_retain_previously_earned_exact_matches():
    first = research([node("root"), node("root/0", "root", score=20),
                      node("root/1", "root", 1, score=15),
                      node("root/0/0", "root/0", score=100, exact=True)], "old_panel")
    first["context"]["partition"] = "evaluation"
    second = research([node("root"), node("root/0", "root", score=20),
                       node("root/1", "root", 1, score=100, exact=True),
                       node("root/0/0", "root/0", score=25)], "new_panel")
    second["context"]["partition"] = "evaluation"
    parent, child = Policy("S1", "greedy"), Policy("S2", "depth", 10)
    decision = api().gate([second], [second], parent, child, budget=3, research_tasks=set(),
                         retention_parent=[first], retention_candidate=[first])
    assert decision["gained"] == ["new_panel"]
    assert not decision["advance"] and decision["retention_lost"] == ["old_panel"]
