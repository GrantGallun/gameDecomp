import pytest

from eval import seal, seal_run
from test_seal import SCHEMA, make_db


def row(name, tu="t1"):
    return {"function": name, "tu": tu, "start": {"ledger": "kb", "attempt_id": 1, "source_sha256": "h"}}


def arm_that(sources_for_treatment=("a", "b"), compiles=25, crash_treatment=(), crash_control=()):
    def arm(r, kwargs):
        if kwargs and r["function"] in crash_treatment:
            raise RuntimeError("ido hung")
        if not kwargs and r["function"] in crash_control:
            raise RuntimeError("ido hung")
        return {"ref": {"ledger": "trial", "attempt_id": 2 if kwargs else 1}, "compiles": compiles,
                "sources": list(sources_for_treatment) if kwargs else ["a", "b"]}
    return arm


def test_every_function_covered_control_runs_before_treatment_per_function():
    calls = []

    def arm(r, kwargs):
        calls.append((r["function"], bool(kwargs)))
        return {"ref": {"ledger": "trial", "attempt_id": len(calls)}, "compiles": 25, "sources": ["x"]}

    outcomes, log = seal_run.run_split([row("a"), row("b")], arm, treatment={"narrow_updates": True})
    assert set(outcomes) == {"a", "b"}
    assert calls == [("a", False), ("a", True), ("b", False), ("b", True)]
    assert seal_run.spend_of(log) == {"a": {"control": 25, "treatment": 25}, "b": {"control": 25, "treatment": 25}}


def test_treatment_that_never_fires_is_flagged_not_read_as_no_effect():
    # the real dev failure: treatment compiled exactly control's sources in every function
    _, log = seal_run.run_split([row("a"), row("b")], arm_that(sources_for_treatment=("a", "b")), treatment={"x": True})
    assert seal_run.summarize(log)["diverged"] == 0
    # ...and fires when the treatment adds a candidate control never produced
    _, log = seal_run.run_split([row("a"), row("b")], arm_that(sources_for_treatment=("a", "b", "new")), treatment={"x": True})
    assert seal_run.summarize(log)["diverged"] == 2


def test_treatment_only_crash_keeps_control_result_and_is_counted_as_failure():
    outcomes, log = seal_run.run_split([row("ok"), row("bad")], arm_that(crash_treatment={"bad"}), treatment={"x": True})
    assert outcomes["bad"]["control"] == outcomes["bad"]["treatment"] == {"ledger": "trial", "attempt_id": 1}
    s = seal_run.summarize(log)
    assert s["treatment_errors"] == 1 and s["control_errors"] == 0
    assert not next(e for e in log if e["function"] == "bad")["diverged"]        # a crash never counts as firing


def test_control_crash_ties_at_the_pinned_start_and_is_counted():
    outcomes, log = seal_run.run_split([row("bad")], arm_that(crash_control={"bad"}), treatment={"x": True})
    assert outcomes["bad"]["control"] == outcomes["bad"]["treatment"] == row("bad")["start"]
    assert seal_run.summarize(log)["control_errors"] == 1


def test_pick_best_is_best_score_found_not_last_or_gradient_pick():
    attempts = {"s0": (1, 80.0, False), "s1": (2, 95.0, False), "s2": (3, 90.0, False), "s3": (4, 95.0, False)}
    assert seal_run.pick_best(attempts) == "s1"                                   # highest score, earliest on tie
    attempts["s4"] = (5, 100.0, True)
    assert seal_run.pick_best(attempts) == "s4"                                   # exact wins


def test_budget_is_a_per_function_cap_and_look_checks_actual_spend(tmp_path):
    conn = make_db()
    prereg = tmp_path / "p.md"
    prereg.write_text("x")
    m = seal.freeze({"kb": conn}, prereg=prereg, excluded={})
    names = sorted(seal.sealed_names(m))
    outcomes = {r["function"]: {"control": r["start"], "treatment": r["start"]} for r in m["sealed"]}
    tool = tmp_path / "t.py"
    tool.write_text("x")
    cap = seal_run.budget_of([], cap=24)
    spend = {n: {"control": 25, "treatment": 25} for n in names}
    spend[names[0]]["treatment"] = 60                                              # one arm overspends on one function
    with pytest.raises(ValueError, match="exceeds the cap"):
        seal.look(m, tmp_path / "l.jsonl", {"kb": conn}, tool="t", tool_files=[tool], outcomes=outcomes,
                  budget=cap, spend=spend)


def test_spec_changes_the_tool_hash_and_extras_are_inside_the_entry(tmp_path):
    conn = make_db()
    prereg = tmp_path / "p.md"
    prereg.write_text("x")
    m = seal.freeze({"kb": conn}, prereg=prereg, excluded={}, max_looks=3)
    outcomes = {r["function"]: {"control": r["start"], "treatment": r["start"]} for r in m["sealed"]}
    tool = tmp_path / "t.py"
    tool.write_text("same tree")
    cap, log = seal_run.budget_of([], cap=24), tmp_path / "l.jsonl"
    first = seal.look(m, log, {"kb": conn}, tool="t", tool_files=[tool], outcomes=outcomes, budget=cap,
                      spec={"treatment": {"a": True}, "budget": 24}, extra={"errors": {"f": "boom"}, "summary": {"diverged": 7}},
                      exclude={m["sealed"][0]["function"]})
    second = seal.look(m, log, {"kb": conn}, tool="t", tool_files=[tool], outcomes=outcomes, budget=cap,
                       spec={"treatment": {"b": True}, "budget": 24})
    assert first["tool_sha256"] != second["tool_sha256"] and not second["repeat_of_same_tool"]
    assert first["extra"]["errors"] == {"f": "boom"} and first["report_excluding"]["excluded"] == [m["sealed"][0]["function"]]
    assert seal.verify_ledger(log, m)["valid"]                                      # extras were hashed in, not appended after
