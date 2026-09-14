from solver import principles, residual, workspace


def _packet(**overrides):
    values = {
        "compiled": True,
        "exact": False,
        "weighted_progress_score": 99.0,
        "compiler_error_signature": "",
        "target_instructions": 3,
        "candidate_instructions": 3,
        "instruction_delta": 0,
        "target_text_bytes": 16,
        "candidate_text_bytes": 16,
        "text_length_delta": 0,
        "positional_byte_distance": 3,
        "positional_equal_bytes": 13,
        "changed_diff_lines": 4,
        "faults": {
            "structural": 0, "layout": 0, "offset": 0, "width": 0,
            "relocation": 0, "register_allocation": 2, "immediate": 0,
        },
        "first_difference": (),
    }
    values.update(overrides)
    return residual.ResidualPacket(**values)


def test_isolated_register_principle_is_confirmed_by_exact_transition():
    attempt = workspace.Attempt(
        True, 99.0, False, "-lbu t6,0(a0)\n+lbu v1,0(a0)", "", "")

    hidden = principles.retrieve("glabel f\n", attempt, _packet())
    enabled = principles.retrieve(
        "glabel f\n", attempt, _packet(), include_hypotheses=True)

    match = next(match for match in hidden
                 if match.pattern_id == "isolated-register-web-source-shape")
    assert match.status == "CONFIRMED"
    assert "variable names" in match.guidance
    assert enabled == hidden


def test_relocation_principle_is_confirmed():
    attempt = workspace.Attempt(True, 99.0, False, "", "", "")
    packet = _packet(faults={
        "structural": 0, "layout": 0, "offset": 0, "width": 0,
        "relocation": 1, "register_allocation": 0, "immediate": 0,
    })

    matches = principles.retrieve("glabel f\n", attempt, packet)

    assert matches[0].pattern_id == "relocation-mismatch"
    assert matches[0].status == "CONFIRMED"
