"""The budget-enforcement reproduction, run against the real `eval.budget_ledger`.

Phase A item 3 of `docs/deepseek-next-experiment-20260921.md`. The audit's program -- `Caps(compiles=10)`
with two 8-charges -- reported `spent.compiles == 16` and `remaining()["compiles"] == -6.0` against the
ledger as it stood, for three reasons: `reserve()` ignored outstanding reservations, `spend()` never
consulted a cap, and a spend had no operation identity that could survive `Ledger.open()`.

This script runs that program and the two interruption cases against the module as it is now. It exits
NON-ZERO if an overrun is still possible without an event that says so, or if any check fails.

Deterministic and self-contained: temporary ledger files, no model calls, no network, no compiles. The
checked-in receipts under `eval/results/` are read-only here -- nothing is appended to them.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval.budget_ledger import BudgetExceeded, Caps, Ledger


def budget_view(ledger: Ledger) -> dict:
    """The fields a reader needs, without the wall-clock values that would make the receipt vary."""
    snapshot = ledger.snapshot()
    return {"caps": {k: v for k, v in snapshot["caps"].items() if k in ("compiles", "model_calls",
                                                                      "evaluation_calls",
                                                                      "evaluation_compiles")},
            "spent": snapshot["spent"], "remaining": snapshot["remaining"]["compiles"],
            "reserved": snapshot["reserved"], "available": snapshot["available"],
            "overrun": snapshot["overrun"],
            "overruns": [{k: record[k] for k in ("amounts", "overrun_by", "reason", "recorded",
                                                 "detected_on_replay") if k in record}
                         for record in snapshot["overruns"]]}


def unmarked_overruns(ledger: Ledger) -> dict:
    """Kinds where `spent` is past the cap with no overrun record naming them. This is the defect state.

    "Never possible for `spent` to exceed a cap without an event that says so" is exactly this being
    empty for every ledger the script builds.
    """
    recorded = {kind for record in ledger.snapshot()["overruns"] for kind in record.get("amounts", {})}
    return {kind: round(amount - getattr(ledger.caps, kind), 3)
            for kind, amount in ledger.spent.items()
            if getattr(ledger.caps, kind) and amount - getattr(ledger.caps, kind) > 1e-9
            and kind not in recorded}


def _refusal(call) -> str | None:
    try:
        call()
    except BudgetExceeded as exc:
        return str(exc)
    return None


def main() -> int:
    checks: dict = {}
    ledgers: list[Ledger] = []

    with tempfile.TemporaryDirectory(prefix="budget-reproduction-") as tmp:
        root = Path(tmp)

        # 1. THE AUDIT'S PROGRAM. Both boundaries that used to let it through must refuse it now.
        path = root / "budget.jsonl"
        ledger = Ledger.open(path, Caps(compiles=10))
        ledgers.append(ledger)
        ledger.reserve("one", compiles=8)
        reserve_refusal = _refusal(lambda: ledger.reserve("two", compiles=8))
        ledger.spend("one", compiles=8)
        spend_refusal = _refusal(lambda: ledger.spend("two", compiles=8))
        checks["the_10_cap_16_spend_case_is_refused"] = {
            "ok": (reserve_refusal is not None and spend_refusal is not None
                   and ledger.spent == {"compiles": 8} and "two" not in ledger.reservations
                   and ledger.snapshot()["overrun"] == {}),
            "second_reservation_refused": reserve_refusal,
            "second_spend_refused": spend_refusal,
            "state_after_the_refusals": budget_view(ledger)}

        # 2. THE WORK REALLY RAN: it is recorded, and the recording is impossible to miss.
        recorded = ledger.record_overrun("two", reason="the 8 compiles had already run", compiles=8)
        checks["a_real_overrun_is_recorded_and_visible"] = {
            "ok": (recorded["overrun"] is True and recorded["overrun_by"] == {"compiles": 6}
                   and ledger.snapshot()["spent"] == {"compiles": 16}
                   and ledger.snapshot()["overrun"] == {"compiles": 6}
                   and len(ledger.snapshot()["overruns"]) == 1),
            "recorded": {"charged": recorded["charged"], "overrun_by": recorded["overrun_by"]},
            "state_after_the_overrun": budget_view(ledger)}

        # 3. A RESTART NEITHER RESETS USAGE NOR RECHARGES A REPORTED OPERATION.
        resumed = Ledger.open(path, Caps(compiles=10))
        ledgers.append(resumed)
        repeat = resumed.spend("two", compiles=8)
        check = resumed.spend("one", compiles=8)
        checks["a_restart_keeps_usage_and_repeats_charge_nothing"] = {
            "ok": (resumed.spent == {"compiles": 16} and resumed.spent == ledger.spent
                   and repeat["already_charged"] is True and not repeat["charged"]
                   and check["already_charged"] is True),
            "spent_after_replay": resumed.spent, "repeat_report": {"already_charged":
                                                                   repeat["already_charged"]}}

        # 4. INTERRUPTED AFTER `reserve` BUT BEFORE `spend`: the reservation is reused, charged once.
        interrupted_path = root / "interrupted.jsonl"
        first = Ledger.open(interrupted_path, Caps(compiles=10, model_calls=4))
        ledgers.append(first)
        first.reserve("stage-a", compiles=6, model_calls=2)
        after_restart = Ledger.open(interrupted_path, Caps(compiles=10, model_calls=4))
        ledgers.append(after_restart)
        reuse = after_restart.reserve("stage-a", compiles=6, model_calls=2)
        charged = after_restart.spend("stage-a", compiles=6, model_calls=2)
        checks["interrupted_after_reserve_resumes_once"] = {
            "ok": (reuse["reused"] is True and charged["charged"] == {"compiles": 6, "model_calls": 2}
                   and after_restart.spent == {"compiles": 6, "model_calls": 2}
                   and len(after_restart.events) == 2),
            "reservation_reused": reuse["reused"], "charged_on_resume": charged["charged"],
            "events": len(after_restart.events)}

        # 5. INTERRUPTED AFTER A PARTIAL `spend`: the reported part survives, the rest is charged once.
        partial_path = root / "partial.jsonl"
        partial = Ledger.open(partial_path, Caps(compiles=10))
        ledgers.append(partial)
        partial.reserve("stage-b", compiles=8)
        partial.spend("stage-b", compiles=3)
        resumed_partial = Ledger.open(partial_path, Caps(compiles=10))
        ledgers.append(resumed_partial)
        final = resumed_partial.spend("stage-b", compiles=8)
        checks["interrupted_after_a_partial_spend_charges_only_the_increase"] = {
            "ok": (resumed_partial.spent == {"compiles": 8} and final["charged"] == {"compiles": 5}
                   and final["already_charged"] is False),
            "spent_after_restart": {"compiles": 3}, "charged_when_the_stage_re_reports": final["charged"],
            "spent_at_the_end": resumed_partial.spent}

        # 6. EVALUATION WORK IS CHARGED TO THE EVALUATION COUNTERS, AND RESEARCH CAPACITY IS NOT EATEN.
        evaluation_path = root / "evaluation.jsonl"
        caps = Caps(model_calls=4, compiles=10, evaluation_calls=12, evaluation_compiles=20)
        evaluation = Ledger.open(evaluation_path, caps)
        ledgers.append(evaluation)
        evaluation.reserve_evaluation("evaluate", calls=6, compiles=10)
        evaluation.reserve("evaluate", compiles=10)          # paired_transfer's research-shaped call
        evaluation.reserve("evaluate-setup", compiles=4)
        setup = evaluation.spend("evaluate-setup", compiles=4)
        arms = evaluation.spend("evaluate", compiles=6)
        checks["evaluation_counters_are_charged_separately"] = {
            "ok": (setup["account"] == "evaluation" and arms["account"] == "evaluation"
                   and evaluation.spent == {"evaluation_compiles": 10}
                   and evaluation.remaining()["compiles"] == 10),
            "setup": {"account": setup["account"], "routed": setup["routed"],
                      "charged": setup["charged"]},
            "arms": {"account": arms["account"], "routed": arms["routed"], "charged": arms["charged"]},
            "spent": evaluation.spent,
            "research_compiles_remaining": evaluation.remaining()["compiles"]}

        # 7. A LEDGER THE OLD CODE WROTE CANNOT BE READ AS CLEAN: replay names the breach it records.
        legacy_path = root / "legacy.jsonl"
        legacy_path.write_text("".join(json.dumps(event) + "\n" for event in [
            {"type": "reserve", "stage": "one", "amounts": {"compiles": 8}, "at": 1000.0,
             "schema_version": 1},
            {"type": "reserve", "stage": "two", "amounts": {"compiles": 8}, "at": 1001.0,
             "schema_version": 1},
            {"type": "spend", "stage": "one", "amounts": {"compiles": 8}, "at": 1002.0,
             "schema_version": 1},
            {"type": "spend", "stage": "two", "amounts": {"compiles": 8}, "at": 1003.0,
             "schema_version": 1},
        ]), encoding="utf-8")
        legacy = Ledger.open(legacy_path, Caps(compiles=10))
        ledgers.append(legacy)
        checks["a_legacy_ledger_shows_its_overrun_on_replay"] = {
            "ok": (legacy.spent == {"compiles": 16} and legacy.snapshot()["overrun"] == {"compiles": 6}
                   and bool(legacy.snapshot()["overruns"])
                   and legacy.snapshot()["overruns"][0]["detected_on_replay"] is True),
            "spent": legacy.spent, "overrun": legacy.snapshot()["overrun"],
            "detected_on_replay": legacy.snapshot()["overruns"][0]["detected_on_replay"]}

        unmarked = {name: unmarked_overruns(ledger) for name, ledger in
                    zip(("measured", "resumed", "interrupted", "partial", "partial-resumed",
                         "evaluation", "legacy"), ledgers)}
        unmarked = {name: excess for name, excess in unmarked.items() if excess}

    # 8. THE GATE ITSELF FIRES. `overrun_still_possible` is what decides the exit code, so it is shown
    #    to report a breach on a ledger that really has one and no record of it, rather than passing
    #    because it never looks. Same shape the old code produced: 16 charged against a cap of 10.
    unrecorded = Ledger(path=Path("<in-memory>"), caps=Caps(compiles=10))
    unrecorded.spent = {"compiles": 16}
    detected = unmarked_overruns(unrecorded)
    checks["the_overrun_gate_fires_on_an_unrecorded_breach"] = {
        "ok": detected == {"compiles": 6}, "detected": detected,
        "overruns": unrecorded.snapshot()["overruns"]}

    overrun_still_possible = bool(unmarked)
    result = {"defect": "Caps(compiles=10) with two 8-charges reached spent.compiles == 16",
              "overrun_still_possible": overrun_still_possible,
              "unmarked_overruns": unmarked,
              "checks": checks,
              "all_checks_passed": all(check["ok"] for check in checks.values())}
    receipt = Path(__file__).with_name("budget-reproduction.json")
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if overrun_still_possible or not result["all_checks_passed"]:
        print("\nREPRODUCTION FAILED: the ledger still permits an overrun, or a check did not hold.",
              file=sys.stderr)
        return 1
    print("\nREPRODUCTION PASSED: every over-run in a 10-cap ledger is either refused or an explicit "
          "overrun event.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
