import json

import pytest

from eval import coverage


def _panel(tmp_path, ids):
    p = tmp_path / "panel.jsonl"
    p.write_text("".join(json.dumps({"id": i, "class": i.split(":")[0]}) + "\n" for i in ids), encoding="utf-8")
    return coverage.load_panel(p)


def test_missing_rows_count_as_uncovered_and_are_listed(tmp_path):
    panel = _panel(tmp_path, ["a:1", "a:2", "b:1"])
    r = coverage.coverage(panel, [{"id": "a:1", "exact": True, "compiles": 3}], budgets=[2, 3])
    assert (r["covered"], r["n"]) == (1, 3)
    assert r["missing_rows"] == ["a:2", "b:1"]
    assert r["at_budget"] == {"2": 0, "3": 1}
    assert r["per_class"] == {"a": {"covered": 1, "n": 2}, "b": {"covered": 0, "n": 1}}


def test_refuses_foreign_and_duplicate_rows(tmp_path):
    panel = _panel(tmp_path, ["a:1"])
    with pytest.raises(coverage.CoverageError):
        coverage.coverage(panel, [{"id": "z:9", "exact": True, "compiles": 1}])
    with pytest.raises(coverage.CoverageError):
        coverage.coverage(panel, [{"id": "a:1", "exact": False, "compiles": 1}] * 2)


def test_cost_must_be_recorded(tmp_path):
    panel = _panel(tmp_path, ["a:1"])
    with pytest.raises(coverage.CoverageError):
        coverage.coverage(panel, [{"id": "a:1", "exact": True}])
    r = coverage.coverage(panel, [{"id": "a:1", "exact": True, "cost": {"tokens": 40}}], cost="tokens", budgets=[50])
    assert r["at_budget"] == {"50": 1}


def test_any_lost_case_refuses_whatever_is_gained(tmp_path):
    panel = _panel(tmp_path, ["a:1", "a:2", "a:3"])
    before = [{"id": "a:1", "exact": True, "compiles": 5}, {"id": "a:2", "exact": False, "compiles": 9},
              {"id": "a:3", "exact": False, "compiles": 9}]
    after = [{"id": "a:1", "exact": False, "compiles": 9}, {"id": "a:2", "exact": True, "compiles": 2},
             {"id": "a:3", "exact": True, "compiles": 2}]
    r = coverage.compare(panel, before, after)
    assert r["gained"] == ["a:2", "a:3"] and r["lost"] == ["a:1"]
    assert r["verdict"] == "refuse: loses covered cases"


def test_equal_coverage_is_decided_by_total_cost(tmp_path):
    panel = _panel(tmp_path, ["a:1", "a:2"])
    before = [{"id": "a:1", "exact": True, "compiles": 30}, {"id": "a:2", "exact": False, "compiles": 72}]
    cheaper = [{"id": "a:1", "exact": True, "compiles": 4}, {"id": "a:2", "exact": False, "compiles": 72}]
    assert coverage.compare(panel, before, cheaper)["verdict"] == "accept: same coverage, cheaper"
    assert coverage.compare(panel, before, before)["verdict"] == "keep baseline: no gain"


def test_saving_on_solved_cases_does_not_hide_spending_on_unsolved_ones(tmp_path):
    # audit 2026-10-03: 11 -> 10,009 total was accepted as "cheaper" when cost was read on shared successes only
    panel = _panel(tmp_path, ["a", "b"])
    before = [{"id": "a", "exact": True, "compiles": 10}, {"id": "b", "exact": False, "compiles": 1}]
    after = [{"id": "a", "exact": True, "compiles": 9}, {"id": "b", "exact": False, "compiles": 10000}]
    r = coverage.compare(panel, before, after)
    assert (r["total_cost_before"], r["total_cost_after"]) == (11, 10009)
    assert r["verdict"] == "keep baseline: no gain"


def test_an_incomplete_run_is_refused(tmp_path):
    panel = _panel(tmp_path, ["a", "b"])
    before = [{"id": "a", "exact": True, "compiles": 10}, {"id": "b", "exact": False, "compiles": 1}]
    assert coverage.compare(panel, before, before[:1])["verdict"] == "refuse: incomplete run"
    assert coverage.compare(panel, before[:1], before)["verdict"] == "refuse: incomplete run"


def test_panel_digest_binds_the_file(tmp_path):
    a = _panel(tmp_path, ["a:1"])
    b = _panel(tmp_path, ["a:1", "a:2"])
    assert a["sha256"] != b["sha256"]
