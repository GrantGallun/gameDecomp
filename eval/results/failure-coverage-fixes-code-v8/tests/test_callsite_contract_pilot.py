"""Coverage accounting keeps deterministic extraction failures visible."""

from eval.callsite_contract_pilot import aggregate


def _row(name: str, calls: int, exact: int, resolved: int, total: int,
         consumed: int, info: int) -> dict:
    return {
        "function": name,
        "metadata": {
            "instruction_count": 40,
            "historical_best_score": 80.0,
        },
        "information_score": info,
        "contracts": {"coverage": {
            "binary_calls": calls,
            "evidence_calls": calls,
            "aligned_calls": calls,
            "resolved_targets": calls,
            "exact_callee_contracts": exact,
            "resolved_exact_arguments": resolved,
            "total_exact_arguments": total,
            "exact_calls_with_consumed_return": consumed,
        }},
    }


def test_aggregate_ranks_consumed_returns_and_does_not_hide_errors():
    result = aggregate([
        _row("argumentsOnly", 2, 1, 2, 2, 0, 4),
        _row("returnConsumer", 1, 1, 1, 1, 1, 3),
        {"function": "failed", "error": "bootstrap"},
    ])

    assert result["functions_requested"] == 3
    assert result["functions_measured"] == 2
    assert result["functions_failed"] == 1
    assert result["argument_resolution_rate"] == 1.0
    assert result["functions_with_call_count_mismatch"] == []
    assert result["high_information_candidates"][0]["function"] == \
        "returnConsumer"
