"""The callsite-contract generation comparison keeps exactness primary."""

from pathlib import Path

from eval.callsite_contract_generation_pilot import (
    _arm_order,
    _candidate_artifact,
    assessment,
)


def test_arm_order_is_counterbalanced_across_two_functions():
    positions = {arm: [] for arm in ("baseline", "contracts")}
    for function in range(2):
        for draw in (1, 2):
            order = _arm_order(function, draw)
            for arm in order:
                positions[arm].append(order.index(arm))

    assert sorted(positions["baseline"]) == [0, 0, 1, 1]
    assert sorted(positions["contracts"]) == [0, 0, 1, 1]


def test_assessment_reports_exact_gain_before_score_gain():
    rows = [
        {"function": "a", "arm": "baseline", "compiled": True,
         "score": 90.0, "exact": False},
        {"function": "a", "arm": "contracts", "compiled": True,
         "score": 100.0, "exact": True},
        {"function": "b", "arm": "baseline", "compiled": True,
         "score": 80.0, "exact": False},
        {"function": "b", "arm": "contracts", "compiled": True,
         "score": 70.0, "exact": False},
    ]

    result = assessment(rows)

    assert result["status"] == "contract_exact_gain_observed_needs_replication"
    assert result["arms"]["contracts"]["exact_functions"] == ["a"]
    assert result["per_function"]["a"]["best_score_delta"] == 10.0


def test_candidate_artifact_tracks_winning_c89_repair(tmp_path: Path):
    repaired = tmp_path / "run-7_contracts_1_c89_object_dump_normalized.s"
    repaired.write_text("repaired")

    result = {
        "compiled": True,
        "score": 80.0,
        "exact": False,
        "raw": {"score": 0.0, "exact": False},
        "c89_repair_changed_source": True,
        "candidate_artifact_stem": "run-7_contracts_1_c89",
    }

    assert _candidate_artifact(tmp_path, result, "contracts", 1) == repaired
