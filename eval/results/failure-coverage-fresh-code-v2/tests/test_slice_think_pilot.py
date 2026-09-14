from eval import slice_think_pilot as pilot


def _slice(arm, seconds, tokens, *, empty=False, truncated=False,
           valid_fences=True, fell_back=False):
    return {
        "arm": arm,
        "generation_seconds": seconds,
        "generation_tokens": tokens,
        "refused": False,
        "empty": empty,
        "truncated": truncated,
        "valid_fences": valid_fences,
        "fell_back_to_thinking": fell_back,
    }


def _result(arm, score=50.0):
    return {"arm": arm, "compiled": True, "exact": False, "score": score}


def test_arm_order_counterbalances_server_drift():
    assert pilot.arm_order(0) == ("think_low", "think_false")
    assert pilot.arm_order(1) == ("think_false", "think_low")


def test_assessment_accepts_large_speedup_without_quality_loss():
    calls = [
        _slice("think_low", 10.0, 100),
        _slice("think_low", 10.0, 100),
        _slice("think_false", 5.0, 40),
        _slice("think_false", 5.0, 40),
    ]
    result = pilot.assess(
        calls, [_result("think_low"), _result("think_false")])
    assert result["status"] == \
        "s1_supported_speed_gate_met_without_observed_regression"
    assert result["wall_clock_reduction_percent"] == 50.0
    assert result["generated_token_reduction_percent"] == 60.0


def test_assessment_kills_speedup_when_empty_rate_rises():
    calls = [
        _slice("think_low", 10.0, 100),
        _slice("think_false", 2.0, 20, empty=True),
    ]
    result = pilot.assess(
        calls, [_result("think_low"), _result("think_false")])
    assert result["status"] == \
        "quality_regression_empty_or_truncated_keep_thinking"


def test_capability_gate_stops_when_false_returns_thinking(monkeypatch):
    responses = iter([
        ("OK", {"eval_count": 2, "done_reason": "stop"}),
        ("```decls\n```\n```stmts\nS1_OK;\n```",
         {"eval_count": 10, "done_reason": "stop"}),
        ("The user wants two blocks.",
         {"eval_count": 20, "done_reason": "stop",
          "_fell_back_to_thinking": True}),
    ])
    monkeypatch.setattr(pilot.llm, "generate", lambda *a, **k: next(responses))
    result = pilot.capability_gate(
        "http://unused", "fake", timeout=1, num_thread=1)
    assert result["proceed"] is False
    assert result["status"] == \
        "lever_not_honoured_response_empty_thinking_returned"
