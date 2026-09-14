from eval import two_lane_wavefront as pilot


def test_matchability_accepts_one_straight_line_return():
    signals = pilot.matchability_signals(
        "lw $v0, 0($a0)\naddiu $v0, $v0, 1\njr $ra\nnop\n")
    assert signals["straight_line"] is True
    assert signals["return_count"] == 1
    assert signals["memory_instructions"] == 1


def test_matchability_rejects_branch_and_call():
    branch = pilot.matchability_signals(
        "beqz $a0, 8\nnop\njr $ra\nnop\n")
    call = pilot.matchability_signals(
        "jal helper\nnop\njr $ra\nnop\n")
    assert branch["straight_line"] is False
    assert call["straight_line"] is False


def test_assessment_keeps_leaf_and_parent_outcomes_separate():
    result = pilot.assessment(
        [{"function": "a"}],
        [{"function": "a", "exact": True, "model_calls": 1,
          "charged_generation_tokens": 100,
          "wall_seconds": 2, "attempts": [{"compiled": True}]}],
        [],
        {"function": "p", "baseline_source": "new_generation",
         "compiled": True, "exact": False, "best_score": 50,
         "model_calls": 1, "charged_generation_tokens": 200},
        [])
    assert result["lane_a"]["exact_leaves"] == 1
    assert result["lane_b"]["baseline_compiled"] is True
    assert result["new_exact_functions_total"] == 1
