"""The coordinator: rounds, generation identity, receipt provenance, and the spec freeze.

WHY THESE TESTS EXIST AND WHY THEY ARE HERE. `eval/results/codex-audit-20260921/reproduce.py` demonstrated
four coordinator defects against the code as it stood, and each one had the same shape: a run that looked
like it had done something and had not.

    rounds: 2            -> seven stages executed, ONE generation, round 1 both times
    S0/S1                -> written as literals in five places, so round 2 reused round 1's ids
    parent pointer       -> initialised to None and never written
    stages/{stage}.json  -> one path for every round, so round 2 skipped every stage as "resumed" and
                            published round 1's verdict as its own result

and, separately, the specification:

    expected_task_ids = evaluation["usable"]

which is the run describing what its own setup managed to do and then being graded against that
description. Measured: 13 tasks declared, 12 set up, one baseline row carrying an infrastructure error,
verdict `promote`.

No model, no compiler and no network is used here. Every stage is replaced on the INSTANCE, so the real
`run_round`/`run`/`record`/`receipt`/`stage_decide` code is what executes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.rsi_loop import STAGES, Experiment, _gate_rows                    # noqa: E402
from eval.rsi_transfer import eligible_panel                                # noqa: E402


def _rows(panel, *, gain=None, error_in=None, arm="baseline"):
    rows = []
    for i, name in enumerate(panel):
        if arm == "intervention" and gain is not None and i == gain:
            rows.append({"function": name, "exact": True})
        else:
            rows.append({"function": name, "exact": False})
    if error_in is not None and arm == error_in[0]:
        rows[error_in[1]] = {"function": panel[error_in[1]],
                             "error": "simulated infrastructure failure"}
    return rows


def _scripted(root: Path, *, rounds=2, panel=None, executed=None, gain=0, error_in=None):
    """An experiment whose stages are scripted but whose coordination is the real code."""
    panel = list(panel or [f"f{i}" for i in range(12)])
    experiment = Experiment(root, {"rounds": rounds, "experiment_id": "coordinator-test",
                                   "panel": {"split": "test", "functions": len(panel)}})
    experiment.production_matches = lambda: {"available": False}
    executed = executed if executed is not None else []

    def frozen():
        return {"generation": experiment.parent_id, "manifest": "test-manifest",
                "spec": {"expected_task_ids": panel, "split": "test", "draws_per_task": 1,
                         "kind": "frozen", "min_tasks": 12, "manifest_sha256": ""},
                "panel": panel}

    def evaluate():
        return {"declared_panel": panel, "usable": panel, "skipped": [],
                "rows": {"baseline": _rows(panel, error_in=error_in),
                         "intervention": _rows(panel, arm="intervention", gain=gain)},
                "delta": {"certified_matches": 1}}

    for stage in STAGES:
        if stage in ("frozen", "decide"):
            continue
        payload = evaluate() if stage == "evaluate" else {"stage": stage}
        setattr(experiment, f"stage_{stage.replace('-', '_')}",
                lambda payload=payload, stage=stage: (executed.append((experiment.round, stage)),
                                                      payload)[1])
    experiment.stage_frozen = lambda: (executed.append((experiment.round, "frozen")), frozen())[1]
    # `decide` is counted too, and it is the REAL gate: wrapping it is what lets a test assert how many
    # stages ran without replacing the thing under test.
    real_decide = experiment.stage_decide

    def decide():
        executed.append((experiment.round, "decide"))
        return real_decide()

    experiment.stage_decide = decide
    return experiment, panel, executed


# --- the spec freeze and the error row ---------------------------------------

def test_a_declared_panel_with_a_setup_failure_cannot_promote(tmp_path):
    """THE AUDIT'S CASE, RE-RUN AGAINST THE REAL GATE.

    13 tasks declared, 12 set up, one baseline row carrying an infrastructure error, and the
    remaining tasks gained 1 and lost none. Verdict before the fix: `promote`.
    """
    panel = [f"f{i}" for i in range(13)]
    experiment, _, _ = _scripted(tmp_path, panel=panel,
                                 error_in=("baseline", 1))
    experiment.record("frozen", experiment.stage_frozen())
    experiment.record("evaluate", experiment.stage_evaluate())
    outcome = experiment.stage_decide()
    assert outcome["verdict"] != "promote", outcome
    assert outcome["conditions"]["R5_budget_executed"] is False
    assert any("no draw count" in reason for reason in outcome["reasons"]), outcome["reasons"]
    assert outcome["infrastructure_errors"], "the receipt must name the failed row"


def test_the_same_panel_without_the_error_row_promotes(tmp_path):
    """The other half, so the test above is about the error row and not about a gate that never
    passes: the identical panel with every row drawn promotes."""
    panel = [f"f{i}" for i in range(12)]
    experiment, _, _ = _scripted(tmp_path, panel=panel)
    experiment.record("frozen", experiment.stage_frozen())
    experiment.record("evaluate", experiment.stage_evaluate())
    outcome = experiment.stage_decide()
    assert outcome["verdict"] == "promote", (outcome["verdict"], outcome["reasons"])
    assert outcome["spec_source"] == "frozen-receipt"
    assert outcome["spec"]["panel_size"] == 12


def test_the_expected_panel_comes_from_the_freeze_not_from_setup(tmp_path):
    """`expected_task_ids` must be the DECLARED panel even when only part of it set up."""
    panel = [f"f{i}" for i in range(13)]
    experiment, _, _ = _scripted(tmp_path, panel=panel, error_in=("baseline", 1))
    experiment.record("frozen", experiment.stage_frozen())
    evaluation = experiment.stage_evaluate()
    evaluation["usable"] = panel[:-1]                       # what setup actually produced
    experiment.record("evaluate", evaluation)
    outcome = experiment.stage_decide()
    assert outcome["spec"]["expected_task_ids"] == panel, "the run redefined its own panel"
    assert outcome["spec"]["panel_size"] == 13
    assert outcome["declared_panel"] == 13 and outcome["usable"] == 12


def test_an_undeclared_panel_is_ineligible_not_promotable(tmp_path):
    experiment, _, _ = _scripted(tmp_path)
    experiment.record("evaluate", experiment.stage_evaluate())     # no `frozen` receipt at all
    outcome = experiment.stage_decide()
    assert outcome["verdict"] == "ineligible"
    assert outcome["spec_source"] == "absent-no-declared-panel"


def test_a_row_with_an_error_is_not_a_completed_draw():
    """The unit, directly: an error row keeps its error and records NO draw count."""
    rows = _gate_rows([{"function": "a", "exact": False},
                       {"function": "b", "error": "boom"},
                       {"function": "c", "exact": True}])
    assert rows["a"] == {"exact": False, "draws": 1}
    assert rows["c"] == {"exact": True, "draws": 1}
    assert "draws" not in rows["b"], "an infrastructure failure was counted as a completed draw"
    assert rows["b"]["error"] == "boom"


# --- rounds, generation identity and the parent pointer ----------------------

def test_two_rounds_execute_two_rounds_and_advance_the_parent(tmp_path):
    experiment, panel, executed = _scripted(tmp_path, rounds=2)
    state = experiment.run()
    assert [round_number for round_number, _ in executed] == [1] * 7 + [2] * 7
    assert state["round"] == 3 and state["rounds_declared"] == 2
    assert state["stage"] == "accept"
    # Round 2 produced S2 from S1, and the pointer says so: the accepted generation is ACTIVE and the
    # state names the generation it came from.
    assert state["generation"] == "S2" and state["parent"] == "S1"
    assert state["candidate"] is None
    assert "accepted from S1" in state["gate_reason"]
    receipts = sorted(p.name for p in (tmp_path / "stages" / "r2").glob("*.json"))
    assert receipts == sorted(f"{stage}.json" for stage in STAGES)


def test_a_kept_parent_does_not_start_a_second_round(tmp_path):
    """A round that did not accept has nothing to build the next generation from."""
    experiment, _, executed = _scripted(tmp_path, rounds=3, gain=None)
    state = experiment.run()
    assert [round_number for round_number, _ in executed] == [1] * 7
    assert state["round"] == 1 and state["rounds_declared"] == 3
    assert state["stage"] == "done" and state["gate_verdict"] == "keep-baseline"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(event["event"] == "rounds_not_reached" for event in events)


def test_the_declared_round_count_is_not_ignored(tmp_path):
    one, _, executed_one = _scripted(tmp_path / "one", rounds=1)
    one.run()
    assert [r for r, _ in executed_one] == [1] * 7
    two, _, executed_two = _scripted(tmp_path / "two", rounds=2)
    two.run()
    assert [r for r, _ in executed_two] == [1] * 7 + [2] * 7


def test_a_resumed_round_reruns_nothing_and_reports_the_same_state(tmp_path):
    experiment, _, executed = _scripted(tmp_path, rounds=2)
    first = dict(experiment.run())
    before = list(executed)
    second = dict(experiment.run())
    assert executed == before, "resume re-executed a stage that already had a receipt"
    for key in ("stage", "generation", "parent", "candidate", "round", "gate_verdict"):
        assert second[key] == first[key], f"resume changed {key}"
    assert first["generation"] == "S2" and first["parent"] == "S1"


def test_an_interrupted_round_resumes_at_the_stage_that_did_not_finish(tmp_path):
    experiment, panel, executed = _scripted(tmp_path, rounds=1)
    # Interrupted after `assemble`: the earlier stages have receipts and must not run again. The
    # receipts are written directly so that this test does not itself execute the stages it is
    # claiming were already done.
    experiment.record("frozen", {"generation": "S0", "manifest": "test-manifest",
                                 "spec": {"expected_task_ids": panel, "split": "test",
                                          "draws_per_task": 1, "kind": "frozen", "min_tasks": 12}})
    for stage in ("research", "verify", "assemble"):
        experiment.record(stage, {"stage": stage})
    experiment.run()
    assert [stage for _, stage in executed] == ["candidate-frozen", "evaluate", "decide"], executed
    resume_receipts = json.loads((tmp_path / "state.json").read_text())
    assert resume_receipts["stage"] == "accept"


# --- receipt provenance ------------------------------------------------------

def test_a_receipt_written_by_a_different_configuration_is_rejected(tmp_path):
    first = Experiment(tmp_path, {"rounds": 1, "experiment_id": "coordinator-test"})
    first.record("frozen", {"generation": "S0", "spec": {"expected_task_ids": ["a"]}})
    changed = Experiment(tmp_path, {"rounds": 1, "experiment_id": "coordinator-test",
                                    "gate": {"min_tasks": 3}})
    assert changed.config_sha256 != first.config_sha256
    assert changed.receipt("frozen") is None, "a receipt from another configuration was reused"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    rejections = [event for event in events if event["event"] == "stage_receipt_rejected"]
    assert rejections and "configuration changed" in json.dumps(rejections[-1])


def test_a_receipt_from_another_round_is_not_this_round_s(tmp_path):
    experiment, _, _ = _scripted(tmp_path, rounds=2)
    experiment.record("frozen", experiment.stage_frozen())
    experiment.state["round"] = 2
    assert experiment.receipt("frozen") is None
    # ...and it is still there for the round that wrote it.
    experiment.state["round"] = 1
    round_one = experiment.receipt("frozen")
    assert round_one is not None and round_one["spec"]["expected_task_ids"]


def test_legacy_receipts_are_marked_historical_not_rewritten(tmp_path):
    """The pre-round layout cannot say which round it belongs to. It is reused for round 1, named in
    the state as historical, and left on disk exactly as it was found."""
    legacy_dir = tmp_path / "stages"
    legacy_dir.mkdir(parents=True)
    original = {"stage": "frozen", "at": 1.0, "generation": "S0"}
    (legacy_dir / "frozen.json").write_text(json.dumps(original), encoding="utf-8")
    experiment = Experiment(tmp_path, {"rounds": 1, "experiment_id": "coordinator-test"})
    payload = experiment.receipt("frozen")
    assert payload is not None and payload["_round_scoped"] is False
    assert payload["_legacy_path"].endswith("frozen.json")
    experiment.publish()
    assert json.loads((tmp_path / "state.json").read_text())["legacy_receipts"] == ["frozen"]
    assert json.loads((legacy_dir / "frozen.json").read_text()) == original


def test_a_legacy_receipt_is_not_used_for_a_later_round(tmp_path):
    legacy_dir = tmp_path / "stages"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "frozen.json").write_text(json.dumps({"stage": "frozen"}), encoding="utf-8")
    experiment = Experiment(tmp_path, {"rounds": 2, "experiment_id": "coordinator-test"})
    experiment.state["round"] = 2
    assert experiment.receipt("frozen") is None


# --- the real freeze stage, against the real verifier ------------------------

def test_the_real_frozen_stage_emits_a_manifest_the_real_verifier_can_read(tmp_path):
    """THE EMITTER AND THE VERIFIER ARE TESTED AGAINST EACH OTHER, not against fixtures.

    Every other coordinator test scripts `stage_frozen` out, so the shape it actually writes was never
    checked by the function that has to read it. It was wrong twice: the `verifier` literal spread
    `hash_artifact` AFTER `path`, so the manifest named `solver/workspace.py` as the verifier and
    `eval/tool_agent.py` nowhere; and the memory component recorded an absence with no stated intent, so
    the verifier could not tell "no notes yet, which is normal at S0" from "a file went missing".
    """
    from eval import generation_manifest as gm

    base, adapter = tmp_path / "model", tmp_path / "adapter"
    base.mkdir(), adapter.mkdir()
    (base / "config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")
    experiment = Experiment(tmp_path / "run", {
        "experiment_id": "freeze-test", "panel": {"split": "test", "functions": 2},
        "downstream": {"base": str(base), "adapter": str(adapter)}})
    receipt = experiment.stage_frozen()
    assert receipt["panel"], "the frozen stage must declare the evaluation panel"
    assert receipt["verified"] is not False or receipt["problems"] == [], "no false alarm on S0"

    report = gm.verify(tmp_path / "run" / "generations", "S0")
    assert report["problems"] == [], report["problems"]
    # The two verifier identities are both named, and neither overwrote the other.
    checked = json.dumps(report["checked"])
    assert "eval/tool_agent.py" in checked or "tool_agent.py" in checked
    assert "workspace.py" in checked
    # The absent notebook is CONSISTENT, not missing: the emitter said why it is absent.
    assert any(entry.get("kind") == "declared-absent" for entry in report["checked"]), report["checked"]
    # And it is NOT `verified`: the inline prompt/schema digests carry no content, so the identity cannot
    # be recomputed. That is stated, not folded into a pass.
    assert report["verified"] is False and report["status"] == "unverified", report["status"]


# --- research exclusion versus the evaluator's right to execute --------------

def test_the_evaluator_may_execute_the_frozen_split_that_research_may_not_see():
    names, held_out = ["t1", "t2"], {"t1", "t2"}
    kept, report = eligible_panel(names, motivating=set(), held_out=held_out)
    assert kept == [] and report["held_out_exclusion_applied"] is True
    kept_eval, report_eval = eligible_panel(names, motivating=set(), held_out=held_out,
                                           purpose="evaluation")
    assert kept_eval == names, "the isolated evaluator refused to execute its own panel"
    assert report_eval["held_out_exclusion_applied"] is False


def test_the_motivating_exclusion_applies_to_both_roles():
    kept, _ = eligible_panel(["a", "b"], motivating={"a"}, held_out=set(), purpose="evaluation")
    assert kept == ["b"]
    with pytest.raises(ValueError):
        eligible_panel(["a"], motivating=set(), held_out=set(), purpose="whatever")
