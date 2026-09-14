from solver import logic, workspace


def _attempt(*, compiled=True, exact=False):
    return workspace.Attempt(compiled, 90.0, exact, "", "", "")


TARGET = """
glabel f
lw t0, 0(a0)
beqz t0, done
nop
jal helper
sw t0, 4(a0)
done:
jr ra
nop
"""


def test_register_only_changes_remain_structural_candidate():
    candidate = TARGET.replace("t0", "v1")

    result = logic.compare(TARGET, candidate, attempt=_attempt())

    assert result.stage == "structural_candidate"
    assert result.semantic_status == "not_tested"
    assert all(result.gates.values())
    assert result.metrics["memory_effects"] == 1.0


def test_changed_call_prevents_logic_shape_promotion():
    candidate = TARGET.replace("jal helper", "jal other_helper")

    result = logic.compare(TARGET, candidate, attempt=_attempt())

    assert result.stage == "compiling_candidate"
    assert not result.gates["calls_agree"]
    assert result.semantic_status == "not_tested"


def test_nonstack_memory_effects_distinguish_offsets_but_ignore_stack():
    changed_object = TARGET.replace("sw t0, 4(a0)", "sw t0, 8(a0)")
    stack_target = TARGET.replace("sw t0, 4(a0)", "sw t0, 4(sp)")
    stack_candidate = stack_target.replace("sw t0, 4(sp)", "sw t0, 12(sp)")

    changed = logic.compare(TARGET, changed_object, attempt=_attempt())
    stack = logic.compare(stack_target, stack_candidate, attempt=_attempt())

    assert changed.metrics["memory_effects"] < 1.0
    assert stack.metrics["memory_effects"] == 1.0


def test_exact_object_is_terminal_stage_and_noncompile_is_explicit():
    exact = logic.compare(TARGET, TARGET, attempt=_attempt(exact=True))
    failed = logic.compare(TARGET, "", attempt=_attempt(compiled=False))

    assert exact.stage == "byte_exact"
    assert exact.semantic_status == "implied_by_byte_exact_target_object"
    assert failed.stage == "not_compiling"
    assert failed.target is None


def test_logic_quality_rank_ignores_weighted_score_and_bytes():
    compiling = logic.compare(
        TARGET, TARGET.replace("jal helper", "jal other"),
        attempt=workspace.Attempt(True, 99.9, False, "", "", ""))
    structural = logic.compare(
        TARGET, TARGET.replace("t0", "v1"),
        attempt=workspace.Attempt(True, 50.0, False, "", "", ""))

    assert logic.quality_key(structural) > logic.quality_key(compiling)
