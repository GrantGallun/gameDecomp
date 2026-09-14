"""Token-budgeted wavefront planning spends only after deterministic gates."""

from pathlib import Path
import json

from eval.wavefront_budget import audit_generation_receipts, build_plan


def _coverage(name: str, *, exact: int = 1, resolved: int = 1,
              total: int = 1, consumed: int = 1, info: int = 10,
              calls: int = 2, evidence: int = 2, score: float = 80.0,
              instructions: int = 50) -> dict:
    return {
        "function": name,
        "information_score": info,
        "metadata": {"historical_best_score": score,
                     "instruction_count": instructions},
        "contracts": {"coverage": {
            "binary_calls": calls,
            "evidence_calls": evidence,
            "exact_callee_contracts": exact,
            "resolved_exact_arguments": resolved,
            "total_exact_arguments": total,
            "exact_calls_with_consumed_return": consumed,
        }},
    }


def test_audit_accounts_tokens_and_keeps_missing_telemetry_visible(
        tmp_path: Path):
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps({
        "kind": "pilot", "model": "local",
        "results": [
            {"arm": "base", "generation_tokens": 100,
             "prompt_chars": 400, "generation_seconds": 2,
             "compiled": True, "exact": False},
            {"arm": "treatment", "generation_tokens": 150,
             "prompt_chars": 800, "generation_seconds": 3,
             "compiled": False, "exact": False},
        ],
    }))

    audit = audit_generation_receipts([path])

    assert audit["recorded_model_draws"] == 2
    assert audit["unrecorded_warmup_calls"] == 1
    assert audit["generated_tokens"] == 250
    assert audit["estimated_prompt_tokens"] == 300
    assert audit["exact_draws"] == 0


def test_plan_prioritizes_consumed_returns_and_obeys_hard_budget():
    receipt = {"rows": [
        _coverage("near", consumed=1, info=8, score=98.0),
        _coverage("informative", consumed=1, info=14, score=60.0),
        _coverage("argsOnly", consumed=0, info=20, score=99.0),
        _coverage("unaligned", calls=2, evidence=3),
        _coverage("noExact", exact=0, resolved=0, total=0),
    ]}

    plan = build_plan(receipt, generation_token_budget=1200,
                      repair_token_cap=1200, max_targets=2)

    assert [row["function"] for row in plan["frontier"]] == ["informative"]
    assert plan["budget"]["reserved"] == 1200
    assert plan["budget_deferred"][0]["function"] == "near"
    assert plan["excluded_reason_counts"] == {
        "call_count_mismatch": 1,
        "no_exact_leaf_contract": 1,
    }


def test_plan_rejects_partially_resolved_exact_arguments():
    receipt = {"rows": [_coverage("partial", resolved=1, total=2)]}

    plan = build_plan(receipt, generation_token_budget=2400,
                      repair_token_cap=1200, max_targets=2)

    assert plan["frontier"] == []
    assert plan["excluded_reason_counts"] == {
        "unresolved_exact_leaf_arguments": 1,
    }
