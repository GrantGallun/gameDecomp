"""Capture real (source, oracle diff, verified line records) for the swap-family fire tests.

    python3 capture_fixtures.py   -> tests/fixtures/site_edits_operators.json
"""
import json

import run
from localize import build_attr
from solver import source_attribution

WANT = {"arith_op:initRaceIntroBillboard": "operator",
        "arg_swap:updateRaceUiTrickPrizePayoutWaitForConfirm": "argswap",
        "stmt_swap:initRaceSplitscreenSelectCornerSprites": "stmtswap",
        "drop_stmt:initControllerPakRaceRecordSaveExitMessage": "missing_store",
        "drop_stmt:updateRaceItemProjectileTrailEffect": "missing_store",
        "temp_return:osSpTaskYield": "next_use_temp",
        "decl_width:alLoadNew": "widening_hint",
        "commute:drawEndingCreditsCharacterLoopingSparkle": "commutative",
        "if_invert:updateRacePlayerRecoverySparkle": "empty_arm"}

out = {}
for c in map(json.loads, open(run.E / "cases.jsonl")):
    if c["id"] not in WANT:
        continue
    code = c["head"] + c["perturbed_def"] + c["tail"]
    b = build_attr(c["function"], "ec_fixture", code)
    assert b["compiled"] and b["attr"]["status"] == "verified", c["id"]
    out[c["id"]] = {"function": c["function"], "family": WANT[c["id"]], "source": code, "diff": b["diff"],
                    "attribution": source_attribution.pack_instructions(b["attr"]),
                    "answer": c["head"] + c["original_def"] + c["tail"],
                    "provenance": "planted single-line edit on a binary-context exact "
                                  "(eval/results/edit-capability-20261002); answer certified by masked dump"}
assert set(out) == set(WANT), set(WANT) - set(out)
(run.ROOT / "tests/fixtures/site_edits_operators.json").write_text(json.dumps(out, indent=1))
print("wrote", len(out), "fixtures")
