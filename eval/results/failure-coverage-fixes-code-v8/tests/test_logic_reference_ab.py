from __future__ import annotations

import hashlib
import json

import pytest

from eval import logic_reference_ab
from solver import logic, residual, workspace


def _logic_packet(function="target"):
    packet = {
        "schema_version": 1,
        "kind": "logic-first-module-packet",
        "function": function,
        "policies": {"target_reference_c_available": False},
        "binary_call_edges": [],
        "functions": [],
    }
    packet["packet_digest"] = hashlib.sha256(json.dumps(
        packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return packet


def _teacher(regime):
    return {
        "regime": regime,
        "policies": {"target_definition_emitted": False},
        "retrieval": {},
        "examples": [{
            "function": "sibling", "source": "void sibling(void) {}"}],
        "matched_source_blocks": [],
    }


def _residual():
    attempt = workspace.Attempt(True, 80.0, False, "diff", "", "")
    return residual.build(attempt, target_asm="glabel target\njr ra")


def test_logic_packet_digest_and_identity_are_checked(tmp_path):
    path = tmp_path / "packet.json"
    packet = _logic_packet()
    path.write_text(json.dumps(packet))
    assert logic_reference_ab.load_logic_packet(path, "target") == packet
    packet["function"] = "changed"
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="belongs|digest"):
        logic_reference_ab.load_logic_packet(path, "target")


def test_prompt_keeps_binary_control_free_of_teacher_source():
    prompt = logic_reference_ab.build_prompt(
        arm="binary_only", assembly="glabel target\njr ra",
        source="void target(void) {}", residual_packet=_residual(),
        logic_packet=_logic_packet(), teacher_packet=None)
    assert "REFERENCE TEACHER: disabled" in prompt
    assert "void sibling" not in prompt
    assert "mechanically preserves that known-compiling scaffold" in prompt
    assert "not stdint names" in prompt


def test_prompt_adds_only_matching_teacher_regime():
    teacher = _teacher("leave_one_tu_out")
    prompt = logic_reference_ab.build_prompt(
        arm="leave_one_tu_out", assembly="glabel target\njr ra",
        source="void target(void) {}", residual_packet=_residual(),
        logic_packet=_logic_packet(), teacher_packet=teacher)
    assert "void sibling" in prompt
    with pytest.raises(ValueError, match="regime"):
        logic_reference_ab.build_prompt(
            arm="leave_one_function_out", assembly="asm",
            source="void target(void) {}", residual_packet=_residual(),
            logic_packet=_logic_packet(), teacher_packet=teacher)


def test_prompt_carries_exact_previous_round_feedback():
    prompt = logic_reference_ab.build_prompt(
        arm="binary_only", assembly="asm", source="void target(void) {}",
        residual_packet=_residual(), logic_packet=_logic_packet(),
        teacher_packet=None, compiler_error="line 7: not an lvalue",
        instruction_diff="-lw t0,0(a0)\n+lw t1,0(a0)")
    assert "line 7: not an lvalue" in prompt
    assert "-lw t0,0(a0)" in prompt


def test_logic_rank_prefers_the_logic_stage():
    weak = logic.Assessment(
        stage="compiling_candidate", semantic_status="not_tested",
        exact=False, metrics={"call_sequence": 1.0, "memory_effects": 0.2,
                              "control_structure": 0.4,
                              "opcode_sequence": 0.5},
        gates={}, target=None, candidate=None, limitations=())
    strong = logic.Assessment(
        stage="logic_shape_candidate", semantic_status="not_tested",
        exact=False, metrics={"call_sequence": 1.0, "memory_effects": 0.8,
                              "control_structure": 0.8,
                              "opcode_sequence": 0.7},
        gates={}, target=None, candidate=None, limitations=())
    assert logic_reference_ab._is_better(strong, weak)


def test_generated_body_is_spliced_without_replacing_scaffold():
    source = """\
#include "common.h"
#define FIELD(p) (*(s32 *)(p))
extern void helper(void *);
void target(void *arg)
{
    FIELD(arg) = 0;
}
"""
    generated = """\
void target(void *wrong_name)
{
    FIELD(wrong_name) = 7;
    helper(wrong_name);
}
"""
    out = logic_reference_ab.splice_generated_body(
        source, "target", generated)
    assert out.startswith('#include "common.h"\n#define FIELD')
    assert "extern void helper" in out
    assert "void target(void *arg)" in out
    assert "FIELD(wrong_name) = 7" in out
    assert "FIELD(arg) = 0" not in out


def test_prefill_pins_exact_root_signature():
    source = "int target(void *arg)\n{\n    return 0;\n}\n"
    assert logic_reference_ab.function_prefill(source, "target") == (
        "```c\nint target(void *arg)\n{\n")


def test_aggregate_separates_context_arms():
    def arm(stage, improved):
        return {
            "calls_attempted": 1, "responses": 1, "charged_tokens": 10,
            "compiling_children": 1, "stage_improved": improved,
            "metric_delta": {name: 0.1 for name in (
                "call_sequence", "memory_effects", "control_structure",
                "opcode_sequence")},
            "root": {"residual": {"positional_byte_distance": 10}},
            "best": {"stage": stage, "exact": False,
                     "residual": {"positional_byte_distance": 8}},
        }
    rows = [{"status": "complete", "arms": {
        "binary_only": arm("compiling_candidate", False),
        "leave_one_function_out": arm("logic_shape_candidate", True),
        "leave_one_tu_out": arm("structural_candidate", True)}}]
    result = logic_reference_ab.aggregate(rows)
    assert result["arms"]["binary_only"]["logic_improvements"] == 0
    assert result["arms"]["leave_one_tu_out"]["logic_improvements"] == 1
    assert result["arms"]["leave_one_function_out"][
        "byte_distance_improvements"] == 1
