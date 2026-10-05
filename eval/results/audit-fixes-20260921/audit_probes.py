"""THE AUDIT'S OWN PROBES, re-run against the current code.

Copied from `eval/results/codex-audit-20260921/reproduce.py` with ONE change: the result file name.
The audit's recorded `reproductions.json` is the BEFORE and is left untouched, so every claim can be
compared against the after instead of being overwritten by it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval import generation_manifest as gm
from eval.budget_ledger import Caps, Ledger
from eval.rsi_loop import Experiment, STAGES
from eval.rsi_transfer import eligible_panel
from eval.tool_agent import Context, run_episode
from eval.tool_agent_probe import compile_state
from eval.tool_registry import ACTIONS


def main():
    result = {}
    intake = ROOT / "eval/results/intake-20260921"
    old = json.loads((intake / "wide-intake-traced.json").read_text())
    new = json.loads((intake / "wide-intake-voidfix.json").read_text())
    a = {r["function"]: r for r in old["rows"]}
    b = {r["function"]: r for r in new["rows"]}
    gains = [n for n in a if not a[n]["sequence"]["compiled"] and b[n]["sequence"]["compiled"]]
    def counts(rows):
        return {
            "ido_compiled": sum(r["sequence"]["compiled"] for r in rows),
            "ido_and_frontend_passed": sum(r["sequence"]["compiled"] and
                r["diagnostic_trace"][-1]["clang"] == "passed" for r in rows),
            "exact": sum(r["sequence"]["exact"] for r in rows),
        }
    result["intake"] = {
        "same_functions": set(a) == set(b),
        "changed_starting_source_hashes": sum(a[n]["draft_sha256"] != b[n]["draft_sha256"] for n in a),
        "before": counts(old["rows"]), "after": counts(new["rows"]),
        "gains": [{"function": n, "final_frontend": b[n]["diagnostic_trace"][-1]} for n in gains],
    }

    seen = []
    class Policy:
        def choose(self, ctx, history):
            seen.append({"candidate": ctx.candidate, "diff": ctx.diff,
                         "derived_state": compile_state(ctx.candidate, history)})
            return ("redraft", {}) if len(seen) == 1 else ("stop", {"reason": "audit"})
    ctx = Context(function="audit_f", candidate="PARENT", diff="PARENT_DIFF",
        initial_verdict={"compiled": True, "exact": False, "score": 80, "diff": "PARENT_DIFF"},
        compile_fn=lambda source: {"compiled": True, "exact": False, "score": 20, "diff": "REJECTED_CHILD_DIFF"})
    trace = run_episode(ctx, Policy(), budget=2, runners={ACTIONS["redraft"].runner:
        lambda context, params: {"status": "ok", "changed": True, "source": "CHILD"}})
    result["rejected_child_observation"] = {"decisions": seen,
        "transform_step": trace.steps[1].as_dict(),
        "parent_hash": hashlib.sha256(b"PARENT").hexdigest()}

    with tempfile.TemporaryDirectory(prefix="decomp-audit-") as tmp:
        root = Path(tmp)
        base = root / "model"
        base.mkdir()
        gm.freeze(root / "manifests", gm.Generation(id="directory-model", created_at="audit",
            base_model={"path": str(base), "files": []}))
        gm.freeze(root / "manifests", gm.Generation(id="inline-prompt", created_at="audit",
            prompts={"system": {"sha256": gm.sha256_text("prompt"), "chars": 6}}))
        result["manifest_verification"] = {}
        for name in ("directory-model", "inline-prompt"):
            try:
                result["manifest_verification"][name] = gm.verify(root / "manifests", name)
            except Exception as exc:
                result["manifest_verification"][name] = {"error": type(exc).__name__, "message": str(exc)}

        ledger = Ledger.open(root / "budget.jsonl", Caps(compiles=10))
        # THE OVERRUN IS THE CLAIM, SO THE REFUSAL HAS TO BE CAPTURED RATHER THAN CRASHING THE PROBE.
        # Before the fix all four calls returned and `spent.compiles` reached 16 against a cap of 10;
        # the after is a `BudgetExceeded` at the step that would have committed the sixteenth compile.
        budget = {"steps": [], "refused_at": None, "error": None}
        for label, call in (("reserve one=8", lambda: ledger.reserve("one", compiles=8)),
                            ("reserve two=8", lambda: ledger.reserve("two", compiles=8)),
                            ("spend one=8", lambda: ledger.spend("one", compiles=8)),
                            ("spend two=8", lambda: ledger.spend("two", compiles=8))):
            try:
                call()
                budget["steps"].append({"step": label, "outcome": "allowed"})
            except Exception as exc:                            # noqa: BLE001
                budget["steps"].append({"step": label, "outcome": "refused",
                                        "error": f"{type(exc).__name__}: {exc}"})
                budget["refused_at"] = label
                budget["error"] = f"{type(exc).__name__}: {exc}"
                break
        budget["snapshot"] = ledger.snapshot()
        budget["spent_compiles"] = ledger.spent.get("compiles", 0)
        budget["within_cap"] = ledger.spent.get("compiles", 0) <= ledger.caps.compiles
        result["budget_overrun"] = budget

        experiment = Experiment(root / "cycle", {"rounds": 2})
        executed = []
        for stage in STAGES:
            def action(stage=stage):
                executed.append(stage)
                return {"decision": "accept", "verdict": "promote", "reasons": []} if stage == "decide" else {}
            setattr(experiment, "stage_" + stage.replace("-", "_"), action)
        first = dict(experiment.run())
        second = dict(experiment.run())
        result["two_rounds_and_resume"] = {"configured_rounds": 2, "stages_executed": executed,
            "first": {k: first.get(k) for k in ("stage", "generation", "candidate", "round")},
            "resumed": {k: second.get(k) for k in ("stage", "generation", "candidate", "round")}}

        gate = Experiment(root / "gate", {"panel": {"split": "test"}, "gate": {"kind": "frozen", "min_tasks": 12}})
        names = [f"f{i}" for i in range(13)]
        usable = names[:-1]
        arms = {
            "baseline": [{"function": n, "exact": False} for n in usable],
            "intervention": [{"function": n, "exact": i == 0} for i, n in enumerate(usable)],
        }
        arms["baseline"][1] = {"function": usable[1], "error": "simulated infrastructure failure"}
        gate.record("evaluate", {"panel": names, "usable": usable,
            "skipped": [{"function": names[-1], "reason": "simulated setup failure"}], "rows": arms})
        gate.production_matches = lambda: {"available": False}
        outcome = gate.stage_decide()
        result["incomplete_panel_gate"] = {"declared_panel": len(names), "usable": len(usable),
            "baseline_execution_errors": 1, "verdict": outcome["verdict"],
            "conditions": outcome.get("conditions"), "reasons": outcome.get("reasons")}

    result["test_panel_selection"] = eligible_panel(["t1", "t2"], motivating=set(), held_out={"t1", "t2"})
    # THE SAME CALL, FOR THE OTHER CALLER. The audit's case above is the RESEARCH purpose and the
    # answer is unchanged (a held-out function must not motivate an intervention). The evaluator is a
    # different role: it exists to execute the frozen split, and asked the same question it must keep
    # both tasks -- otherwise the isolated evaluator refuses to measure what it was sealed to measure.
    result["test_panel_selection_evaluation_purpose"] = eligible_panel(
        ["t1", "t2"], motivating=set(), held_out={"t1", "t2"}, purpose="evaluation")
    watched = ["eval/tool_agent.py", "eval/rsi_loop.py", "eval/rsi_transfer.py",
               "eval/generation_manifest.py", "eval/budget_ledger.py", "eval/intake_probe.py",
               "eval/intake_runners.py", "solver/source_type_declarations.py"]
    result["code_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in watched}
    path = Path(__file__).with_name("audit-probes-after.json")
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
