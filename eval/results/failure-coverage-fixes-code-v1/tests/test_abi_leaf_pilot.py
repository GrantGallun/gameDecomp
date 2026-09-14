import json
from pathlib import Path

from eval import abi_leaf_pilot as pilot


ROOT = Path(__file__).resolve().parents[1]


def test_heldout_names_unions_every_frozen_set(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"heldout": [{"function": "secretA"}]}))
    b.write_text(json.dumps({"heldout": [{"function": "secretB"}]}))
    assert pilot.heldout_names([a, b]) == {"secretA", "secretB"}


def test_leaf_dev_pool_is_disjoint_from_every_frozen_heldout_set():
    leaf_set = json.loads(
        (ROOT / "eval/sets/leaves_v1.json").read_text(encoding="utf-8"))
    dev = {row["function"] for row in leaf_set["dev"]}
    heldout = pilot.heldout_names([
        ROOT / "eval/sets/sbk1_v1.json",
        ROOT / "eval/sets/sbk1_v2.json",
        ROOT / "eval/sets/sbk1_v3.json",
    ])
    assert dev.isdisjoint(heldout)


def test_abi_signals_detect_narrow_parameter_and_return_load():
    asm = """\
glabel narrowLeaf
sw $a0, 0($sp)
andi $t0, $a0, 0xffff
jr $ra
lbu $v0, 3($t0)
"""
    signals = pilot.abi_signals(asm)
    assert signals["narrow_parameters"] == {"a0": 2}
    assert signals["narrow_return_load"] == {
        "opcode": "lbu", "width": 1, "signed": False}
    score, reasons = pilot.leverage_score("getNarrow", signals, 5, 4)
    assert score > 10
    assert "narrow load reaches v0 near return" in reasons


def test_abi_signals_detect_floating_return_register():
    signals = pilot.abi_signals("jr $ra\nmov.s $f0, $f2\n")
    assert signals["float_return_signal"] is True
    score, _ = pilot.leverage_score("compute", signals, 1, 2)
    assert score >= 12


def test_direct_parameter_layout_is_binary_only_and_conservative():
    asm = """\
lbu $t6, 0x518($a0)
sw $t0, 0x20($a1)
lh $t1, 0x20($a1)
lw $t2, -4($a0)
"""
    # 0x20 has conflicting widths and negative offsets are not struct fields.
    assert pilot.direct_parameter_layout(asm) == {0x518: 1}


def test_non_abi_leaf_is_not_ranked_from_name_alone():
    signals = {
        "narrow_parameters": {}, "float_return_signal": False,
        "narrow_return_load": None, "narrow_return_normalization": None,
        "writes_v0_near_return": True,
    }
    assert pilot.leverage_score("getOrdinaryInt", signals, 20, 2) == (0.0, [])


def test_inferred_prototype_is_old_style_and_return_only():
    source = '#include "common.h"\nvoid parent(void) { randomNext(); }\n'
    candidate, action = pilot._apply_inferred_prototype(
        source, "randomNext", "u8")
    assert action == "insert"
    assert "u8 randomNext();" in candidate


def test_inferred_prototype_replaces_wrong_return_declaration():
    source = "extern void randomNext();\nvoid p(void) { randomNext(); }\n"
    candidate, action = pilot._apply_inferred_prototype(
        source, "randomNext", "u8")
    assert action == "replace"
    assert candidate.startswith("u8 randomNext();")


def test_subsuming_mask_marks_return_contract_edge_inactive():
    asm = """jal randomNextObject
     sw $a0, 0x30($sp)
    lw $a0, 0x30($sp)
    andi $t7, $v0, 0xF
"""

    activity = pilot.return_contract_activity(asm, "randomNextObject", 1)

    assert activity == [{
        "call_line": 1, "consumer": "explicit_mask", "mask": 0xF,
        "compiler_active": False,
    }]


def test_assessment_separates_leaf_yield_from_transfer():
    result = pilot.assessment(
        [{"function": "leaf"}],
        [{"function": "leaf", "exact": True, "model_calls": 1,
          "charged_generation_tokens": 100, "wall_seconds": 2}],
        [{"leaf": "leaf", "callers": [{
            "parent": "p", "variants": [{"assembly_changed": True,
                                           "score_delta": 1.0,
                                           "exact": False}]}]}])
    assert result["status"] == "confirmed_parent_codegen_delta_no_exact_parent"
    assert result["exact_leaves"] == 1
    assert result["parent_assembly_deltas"] == 1
