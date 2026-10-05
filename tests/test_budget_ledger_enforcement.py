"""Budget enforcement at the invocation boundary: the ceiling has to refuse work, and a restart has to
know what was already charged.

The motivating case is measured, not hypothetical. Against `eval/budget_ledger.py` as it stood, this
exact program reported `spent.compiles == 16` against a cap of 10 and `remaining()["compiles"] == -6.0`:

    ledger = Ledger.open(path, Caps(compiles=10))
    ledger.reserve("one", compiles=8)
    ledger.reserve("two", compiles=8)      # ignored the 8 already reserved
    ledger.spend("one", compiles=8)
    ledger.spend("two", compiles=8)        # never consulted a cap at all

Three defects produced it, all in one file: `reserve()` summed only `spent`, `spend()` had no cap check,
and neither had an operation identity that survives `Ledger.open()`. Each test below names the defect it
pins, and each driving case is asserted in the direction that FIRES -- a refused reservation, a refused
spend, an overrun that becomes a visible event, an already-charged operation that charges nothing --
rather than only in the direction where the ledger declines.

The `sandbox` fixture uses `tempfile.mkdtemp`, like `tests/test_rsi_foundations.py`: pytest's `tmp_path`
raises PermissionError while scanning `%TEMP%\\pytest-of-grant` on this box.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from eval import budget_ledger as bl


@pytest.fixture
def sandbox():
    path = Path(tempfile.mkdtemp(prefix="budget-enforcement-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- #
# THE MEASURED 10-CAP / 16-SPEND CASE
# --------------------------------------------------------------------------- #

def test_the_measured_ten_cap_sixteen_spend_case_is_refused_at_both_boundaries(sandbox):
    """The audit's program. Neither the second reservation nor the second spend may proceed."""
    path = sandbox / "budget.jsonl"
    ledger = bl.Ledger.open(path, bl.Caps(compiles=10))

    ledger.reserve("one", compiles=8)
    with pytest.raises(bl.BudgetExceeded) as excinfo:
        ledger.reserve("two", compiles=8)
    refusal = str(excinfo.value)
    assert "compiles" in refusal and "already reserved 8" in refusal
    assert "two" not in ledger.reservations                 # the stage that cannot reserve does not start
    assert ledger.spent == {}                               # and a refused reservation charges nothing

    # The refusals are legible in the snapshot: 8 is committed by the reservation, 2 is available.
    snapshot = ledger.snapshot()
    assert snapshot["spent"] == {}
    assert snapshot["remaining"]["compiles"] == 10          # NOT spent; the reservation is not a charge
    assert snapshot["remaining"]["reserved"] == {"compiles": 8}
    assert snapshot["remaining"]["available"]["compiles"] == 2
    assert snapshot["overruns"] == [] and snapshot["overrun"] == {}

    ledger.spend("one", compiles=8)
    assert ledger.spent == {"compiles": 8}
    assert ledger.remaining()["available"]["compiles"] == 2  # the spent reservation no longer claims

    # DEFECT 2, in the direction that fires: 8 + 8 > 10, so the charge is refused before it is recorded.
    with pytest.raises(bl.BudgetExceeded) as excinfo:
        ledger.spend("two", compiles=8)
    assert "compiles" in str(excinfo.value) and "record_overrun" in str(excinfo.value)
    snapshot = ledger.snapshot()
    assert snapshot["spent"] == {"compiles": 8}             # the refused charge left no trace
    assert snapshot["remaining"]["compiles"] == 2
    assert snapshot["overruns"] == [] and snapshot["overrun"] == {}

    # ... and the overrun the refused spend describes is an explicit event when the work really ran.
    recorded = ledger.record_overrun("two", reason="the 8 compiles had already run before the cap check",
                                     compiles=8)
    assert recorded["overrun"] is True
    assert recorded["charged"] == {"compiles": 8}
    assert recorded["overrun_by"] == {"compiles": 6}
    snapshot = ledger.snapshot()
    assert snapshot["spent"] == {"compiles": 16}            # the real usage is recorded, not lost
    assert snapshot["remaining"]["compiles"] == -6
    assert snapshot["overrun"] == {"compiles": 6}           # impossible to miss
    assert len(snapshot["overruns"]) == 1
    record = snapshot["overruns"][0]
    assert record["reason"].startswith("the 8 compiles had already run")
    assert record["amounts"] == {"compiles": 8}
    assert record["cap"] == {"compiles": 10}
    assert record["detected_on_replay"] is False

    # A refused reservation and a refused spend left no events behind; only real work did.
    assert [event["type"] for event in ledger.events] == ["reserve", "spend", "overrun"]


def test_an_overrun_needs_a_reason_and_a_normal_spend_never_becomes_one(sandbox):
    """The resolution of refuse-vs-record: caught early it refuses, already done it is a named event."""
    ledger = bl.Ledger.open(sandbox / "budget.jsonl", bl.Caps(compiles=10))
    ledger.spend("stage", compiles=10)                       # exactly the cap, allowed
    with pytest.raises(bl.BudgetExceeded):
        ledger.spend("stage", compiles=11)                   # the reported total grows past the cap
    assert ledger.spent == {"compiles": 10}
    assert ledger.snapshot()["overruns"] == []

    with pytest.raises(ValueError):
        ledger.record_overrun("stage", reason="   ", compiles=11)
    assert ledger.spent == {"compiles": 10}                  # no reason, no record, no charge
    assert ledger.snapshot()["overruns"] == []

    recorded = ledger.record_overrun("stage", reason="the 11th compile ran before the ledger was told",
                                     compiles=11)
    assert recorded["charged"] == {"compiles": 1}            # only the increase: a running total
    assert recorded["overrun_by"] == {"compiles": 1}
    snapshot = ledger.snapshot()
    assert snapshot["spent"] == {"compiles": 11}
    assert snapshot["remaining"]["compiles"] == -1
    assert snapshot["overrun"] == {"compiles": 1}
    assert snapshot["overruns"][0]["spent_before"] == {"compiles": 10.0}
    assert snapshot["overruns"][0]["spent_after"] == {"compiles": 11.0}

    # A charge that stays inside the cap is written as an ordinary spend, so an overrun record always
    # means an overrun happened.
    ledger_two = bl.Ledger.open(sandbox / "second.jsonl", bl.Caps(compiles=10))
    inside = ledger_two.record_overrun("stage", reason="reported after the fact, still inside the cap",
                                       compiles=4)
    assert inside["overrun"] is False and inside["overrun_by"] == {}
    assert ledger_two.snapshot()["overruns"] == []
    assert [event["type"] for event in ledger_two.events] == ["spend"]


# --------------------------------------------------------------------------- #
# RESERVATION SUMS
# --------------------------------------------------------------------------- #

def test_outstanding_reservations_sum_so_two_stages_cannot_reserve_the_same_capacity(sandbox):
    """DEFECT 1: `reserve()` compared `spent + amount` with the cap and ignored what was held."""
    ledger = bl.Ledger.open(sandbox / "budget.jsonl", bl.Caps(compiles=10, model_calls=4))

    assert ledger.reserve("a", compiles=6, model_calls=2)["reused"] is False
    # exactly the cap is allowed: the ceiling is a ceiling, not a margin
    assert ledger.reserve("b", compiles=4, model_calls=2)["reused"] is False
    assert ledger.remaining()["reserved"] == {"compiles": 10, "model_calls": 4}
    assert ledger.available()["compiles"] == 0
    assert ledger.available()["model_calls"] == 0
    # `remaining()[kind]` keeps its old meaning: cap minus SPENT, not minus reservations
    assert ledger.remaining()["compiles"] == 10
    assert ledger.remaining()["model_calls"] == 4

    with pytest.raises(bl.BudgetExceeded) as excinfo:
        ledger.reserve("c", compiles=1)
    assert "genuinely available" in str(excinfo.value)
    with pytest.raises(bl.BudgetExceeded):
        ledger.reserve("c", model_calls=1)
    assert set(ledger.reservations) == {"a", "b"}

    # Re-entering a held stage is a lookup, not a new decision, and does not consume capacity twice.
    assert ledger.reserve("a", compiles=6, model_calls=2)["reused"] is True
    assert ledger.remaining()["reserved"] == {"compiles": 10, "model_calls": 4}

    # Releasing one stage genuinely frees its share and nothing else.
    ledger.release("b")
    assert set(ledger.reservations) == {"a"}
    assert ledger.available()["compiles"] == 4
    assert ledger.available()["model_calls"] == 2
    assert ledger.reserve("c", compiles=4, model_calls=2)["reused"] is False


def test_reserving_an_unknown_or_negative_amount_records_nothing(sandbox):
    """A caller error is loud, and it is not bookkeeping: the reservation table stays clean."""
    path = sandbox / "budget.jsonl"
    ledger = bl.Ledger.open(path, bl.Caps(compiles=10))
    with pytest.raises(ValueError):
        ledger.reserve("bad", bananas=1)
    with pytest.raises(ValueError):
        ledger.reserve("bad", compiles=-1)
    with pytest.raises(ValueError):
        ledger.spend("bad", bananas=1)
    with pytest.raises(ValueError):
        ledger.record_overrun("bad", reason="unknown kind", bananas=1)
    assert ledger.reservations == {} and ledger.spent == {}
    assert not path.exists()                                 # nothing was appended


# --------------------------------------------------------------------------- #
# OPERATION IDENTITY ACROSS A RESTART
# --------------------------------------------------------------------------- #

def test_a_replayed_ledger_does_not_charge_a_repeated_operation_id(sandbox):
    """DEFECT 3: a spend had no identity, so re-reporting it after a restart charged it twice."""
    path = sandbox / "budget.jsonl"
    caps = bl.Caps(compiles=10)
    ledger = bl.Ledger.open(path, caps)

    first = ledger.spend("stage-x", op="op-1", compiles=4)
    assert first["charged"] == {"compiles": 4}
    assert first["already_charged"] is False and first["previously_charged"] is False

    resumed = bl.Ledger.open(path, caps)
    assert resumed.spent == {"compiles": 4}                  # a restart does not reset usage
    repeated = resumed.spend("stage-x", op="op-1", compiles=4)
    assert repeated["already_charged"] is True               # the caller can tell the two apart
    assert repeated["charged"] == {} and repeated["previously_charged"] is True
    assert resumed.spent == {"compiles": 4}
    assert len(resumed.events) == 1                          # a repeat report is not even an event

    # A larger report for the SAME operation is the interrupted stage re-reporting its true total:
    # only the increase is charged.
    grown = resumed.spend("stage-x", op="op-1", compiles=6)
    assert grown["charged"] == {"compiles": 2}
    assert resumed.spent == {"compiles": 6}

    # A genuinely separate run is a separate operation and is charged in full.
    second = resumed.spend("stage-x", op="op-1:round2", compiles=3)
    assert second["charged"] == {"compiles": 3}
    assert resumed.spent == {"compiles": 9}

    third = bl.Ledger.open(path, caps)
    assert third.spent == {"compiles": 9}
    assert third.operations == {"op-1": {"compiles": 6}, "op-1:round2": {"compiles": 3}}
    assert third.spend("stage-x", op="op-1", compiles=6)["already_charged"] is True
    assert third.spent == {"compiles": 9}

    # Without an explicit op the STAGE is the identity, so the resume path existing callers use
    # (`spend(stage, ...)` again after a crash) is idempotent too.
    other = sandbox / "stage-op.jsonl"
    bl.Ledger.open(other, caps).spend("research", compiles=3)
    replay = bl.Ledger.open(other, caps).spend("research", compiles=3)
    assert replay["already_charged"] is True
    assert bl.Ledger.open(other, caps).spent == {"compiles": 3}

    # A first report of nothing is not "already charged": nothing was charged either way, and the two
    # are only distinguishable if the flag means what it says.
    nothing = bl.Ledger.open(other, caps).spend("stage-y", compiles=0)
    assert nothing["charged"] == {} and nothing["already_charged"] is False


# --------------------------------------------------------------------------- #
# INTERRUPTION MIDWAY THROUGH A STAGE
# --------------------------------------------------------------------------- #

def test_a_stage_interrupted_after_reserve_and_before_spend_resumes_without_a_second_charge(sandbox):
    """The reservation is on disk, so the resumed stage neither loses it nor holds capacity twice."""
    path = sandbox / "budget.jsonl"
    caps = bl.Caps(compiles=10, model_calls=4)
    ledger = bl.Ledger.open(path, caps)
    ledger.reserve("stage-a", compiles=6, model_calls=2)
    ledger.reserve("stage-b", compiles=4)
    # --- the process dies here, before stage-a reports anything -------------------
    resumed = bl.Ledger.open(path, caps)
    assert resumed.spent == {}
    assert resumed.reservations["stage-a"] == {"compiles": 6, "model_calls": 2}
    assert resumed.available()["compiles"] == 0
    assert resumed.available()["model_calls"] == 2

    assert resumed.reserve("stage-a", compiles=6, model_calls=2)["reused"] is True
    assert resumed.reserve("stage-a", compiles=999)["reused"] is True     # still a lookup, not a grant
    assert len(resumed.events) == 2                                      # no new reservation event

    spent = resumed.spend("stage-a", compiles=6, model_calls=2)
    assert spent["charged"] == {"compiles": 6, "model_calls": 2}
    assert resumed.spent == {"compiles": 6, "model_calls": 2}
    # stage-a's claim is gone (spent), so only stage-b's 4 compiles are still held and nothing else
    # can be reserved against the compiles cap; the model_calls cap was never claimed beyond stage-a.
    assert resumed.remaining()["reserved"] == {"compiles": 4}
    assert resumed.available()["compiles"] == 0
    assert resumed.available()["model_calls"] == 2
    with pytest.raises(bl.BudgetExceeded):
        resumed.reserve("stage-c", compiles=1)


def test_a_stage_interrupted_after_reporting_a_spend_is_not_charged_twice_on_resume(sandbox):
    """A partial report survives the restart and the resumed stage continues from it, once."""
    path = sandbox / "budget.jsonl"
    caps = bl.Caps(compiles=10)
    ledger = bl.Ledger.open(path, caps)
    ledger.reserve("stage-a", compiles=8)
    partial = ledger.spend("stage-a", compiles=3)
    assert partial["charged"] == {"compiles": 3}
    # --- the process dies here, after a real partial spend was reported -----------
    resumed = bl.Ledger.open(path, caps)
    assert resumed.spent == {"compiles": 3}                  # the reported spend is not lost
    assert resumed.reserve("stage-a", compiles=8)["reused"] is True

    final = resumed.spend("stage-a", compiles=8)             # the stage's true total, reported once
    assert final["charged"] == {"compiles": 5}               # exactly the part not yet charged
    assert final["already_charged"] is False
    assert resumed.spent == {"compiles": 8}                  # 8, never 11

    # ... and re-reporting that same total after another restart changes nothing.
    again = bl.Ledger.open(path, caps)
    assert again.spent == {"compiles": 8}
    assert again.spend("stage-a", compiles=8)["already_charged"] is True
    assert again.spent == {"compiles": 8}

    # The remaining capacity is the difference, and it can still be reserved and spent exactly once.
    assert again.remaining()["available"]["compiles"] == 2
    again.reserve("stage-b", compiles=2)
    assert again.spend("stage-b", compiles=2)["charged"] == {"compiles": 2}
    assert again.spent == {"compiles": 10}
    with pytest.raises(bl.BudgetExceeded):
        again.spend("stage-b", compiles=3)                   # the running total would pass the cap


# --------------------------------------------------------------------------- #
# EVALUATION COUNTERS ARE THEIR OWN ACCOUNT
# --------------------------------------------------------------------------- #

def test_evaluation_counters_are_charged_separately_from_research_counters(sandbox):
    """`reserve_evaluation` had no matching spend path: the panel's compiles hit the research cap."""
    path = sandbox / "budget.jsonl"
    caps = bl.Caps(model_calls=4, compiles=10, evaluation_calls=12, evaluation_compiles=20)
    ledger = bl.Ledger.open(path, caps)
    ledger.reserve("research-round", model_calls=2, compiles=4)

    reservation = ledger.reserve_evaluation("evaluate", calls=6, compiles=10)
    assert reservation["amounts"] == {"evaluation_calls": 6, "evaluation_compiles": 10}
    assert ledger.remaining()["available"]["evaluation_compiles"] == 10
    assert ledger.remaining()["available"]["compiles"] == 6  # research capacity untouched

    # `paired_transfer` asks again in research kinds for the same stage; that is the same reservation.
    assert ledger.reserve("evaluate", compiles=10)["reused"] is True
    # ... and its `<stage>-setup` companion is held against the counter its spend will be charged to.
    assert ledger.reserve("evaluate-setup", compiles=4)["amounts"] == {"evaluation_compiles": 4}

    setup = ledger.spend("evaluate-setup", compiles=4)
    assert setup["account"] == "evaluation"
    assert setup["routed"] == {"compiles": "evaluation_compiles"}
    assert setup["charged"] == {"evaluation_compiles": 4}
    arms = ledger.spend("evaluate", compiles=6)
    assert arms["charged"] == {"evaluation_compiles": 6}

    assert ledger.spent == {"evaluation_compiles": 10}       # not one research compile
    assert ledger.remaining()["compiles"] == 10
    assert ledger.remaining()["evaluation_compiles"] == 10
    assert ledger.operations["evaluate"] == {"evaluation_compiles": 6}

    # The explicit entry point exists for callers that do not reserve first.
    explicit = ledger.spend_evaluation("panel", calls=2, compiles=1)
    assert explicit["account"] == "evaluation" and explicit["charged"] == {"evaluation_calls": 2,
                                                                          "evaluation_compiles": 1}
    assert ledger.spent == {"evaluation_compiles": 11, "evaluation_calls": 2}
    # An evaluation charge is refused against the EVALUATION cap, even with research capacity free.
    with pytest.raises(bl.BudgetExceeded) as excinfo:
        ledger.spend_evaluation("panel", compiles=100)
    assert "evaluation_compiles" in str(excinfo.value)
    assert ledger.spent == {"evaluation_compiles": 11, "evaluation_calls": 2}

    # A reservation for evaluation capacity that cannot fit is refused like any other.
    with pytest.raises(bl.BudgetExceeded):
        ledger.reserve_evaluation("second-panel", compiles=10)   # 15 spent-or-held of 20 already

    replayed = bl.Ledger.open(path, caps)
    assert replayed.spent == {"evaluation_compiles": 11, "evaluation_calls": 2}
    assert replayed.evaluation_stages == {"evaluate", "evaluate-setup", "panel"}
    # Routing survives replay, and the running-total rule does too: `evaluate` already holds 6, so a
    # report of 7 charges the one compile that is new.
    resumed_spend = replayed.spend("evaluate", compiles=7)
    assert resumed_spend["charged"] == {"evaluation_compiles": 1}
    assert resumed_spend["account"] == "evaluation"
    assert resumed_spend["routed"] == {"compiles": "evaluation_compiles"}
    assert "compiles" not in replayed.spent


# --------------------------------------------------------------------------- #
# A LEDGER WRITTEN BY THE OLD CODE
# --------------------------------------------------------------------------- #

def test_a_legacy_ledger_written_by_the_old_code_shows_its_overrun_on_replay(sandbox):
    """The historical 16-spend file is not rewritten, and it cannot be read as a clean ledger."""
    path = sandbox / "legacy.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in [
        {"type": "reserve", "stage": "one", "amounts": {"compiles": 8}, "at": 1000.0, "schema_version": 1},
        {"type": "reserve", "stage": "two", "amounts": {"compiles": 8}, "at": 1001.0, "schema_version": 1},
        {"type": "spend", "stage": "one", "amounts": {"compiles": 8}, "at": 1002.0, "schema_version": 1},
        {"type": "spend", "stage": "two", "amounts": {"compiles": 8}, "at": 1003.0, "schema_version": 1},
    ]), encoding="utf-8")

    ledger = bl.Ledger.open(path, bl.Caps(compiles=10))
    assert ledger.spent == {"compiles": 16}                  # the recorded numbers are kept
    assert ledger.reservations == {"one": {"compiles": 8}, "two": {"compiles": 8}}
    snapshot = ledger.snapshot()
    assert snapshot["remaining"]["compiles"] == -6
    assert snapshot["overrun"] == {"compiles": 6}
    assert len(snapshot["overruns"]) == 1
    assert snapshot["overruns"][0]["detected_on_replay"] is True
    assert "overrun events existed" in snapshot["overruns"][0]["reason"]

    # The resumed run cannot add to that quietly either: the next charge is refused.
    with pytest.raises(bl.BudgetExceeded):
        ledger.spend("three", compiles=1)
    # ... and re-reporting an operation that file already charged is a repeat, not a second charge.
    assert ledger.spend("two", compiles=8)["already_charged"] is True
    assert ledger.spent == {"compiles": 16}


def test_the_real_experiment_ledger_still_replays_with_research_and_evaluation_split(sandbox):
    """The checked-in narrow-RSI ledger keeps its numbers, and its evaluation reserve stays unspent.

    That is the measured end-to-end state of the defect: `evaluation_calls`/`evaluation_compiles` were
    reserved (60 each) and never spent, while the panel's 24 real compiles were charged to the research
    counter. This test pins the HISTORY and the repair path: replay is unchanged, and the next spend of
    that same evaluation stage goes to the evaluation counter.

    The ledger is COPIED into the sandbox first. It is an append-only historical receipt under
    `eval/results/`, and a test that spends into it would rewrite the record it is checking.
    """
    source = Path(__file__).resolve().parents[1] / "eval/results/narrow-rsi-20260921/budget.jsonl"
    path = sandbox / "budget.jsonl"
    shutil.copyfile(source, path)
    caps = bl.Caps(compiles=72)
    ledger = bl.Ledger.open(path, caps)
    assert ledger.spent == {"compiles": 24}
    assert ledger.reservations["evaluate"] == {"evaluation_calls": 60, "evaluation_compiles": 60}
    assert ledger.snapshot()["overruns"] == []               # 24 of 72 is inside the cap

    assert ledger.spend("evaluate", compiles=5)["charged"] == {"evaluation_compiles": 5}
    assert ledger.spent == {"compiles": 24, "evaluation_compiles": 5}
    assert ledger.remaining()["compiles"] == 48
    assert ledger.remaining()["evaluation_compiles"] == caps.evaluation_compiles - 5
    assert source.read_text("utf-8").count("\n") == 4        # the historical receipt is untouched
