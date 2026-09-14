"""Tests for compact, fallibility-labelled Ghidra prompt context."""

import json

import pytest

from solver import ghidra_context


def evidence(c_text="int FUN_80001000(void) { return DAT_80002000; }"):
    return {
        "schema_version": 1,
        "program": {"language": "MIPS:BE:32:default"},
        "function": {
            "entry": "80001000", "end": "8000102f",
            "ghidra_name": "FUN_80001000",
            "instruction_count": 12, "basic_block_count": 3,
        },
        "decompiler": {"completed": True, "error": "", "c": c_text},
        "basic_blocks": [
            {"start": "80001000", "end": "80001007", "destinations": [
                {"address": "80001010", "flow": "COMPUTED_JUMP"},
                {"address": "80001020", "flow": "COMPUTED_JUMP"},
            ]},
            {"start": "80001010", "end": "80001017", "destinations": []},
            {"start": "80001020", "end": "8000102f", "destinations": []},
        ],
        "instructions": [{"text": "secret duplicated assembly"}],
        "calls": [],
        "data_refs": [
            {"target": "80002000"}, {"target": "80002000"},
        ],
    }


def test_render_is_compact_and_labels_pseudocode_as_fallible():
    text = ghidra_context.render(evidence(), "wantedName")
    assert "not an oracle" in text
    assert "2 destinations" in text
    assert "wantedName(void)" in text
    assert "80002000 (2 refs)" in text
    assert "secret duplicated assembly" not in text


def test_warning_is_promoted_out_of_pseudocode():
    text = ghidra_context.render(evidence(
        "/* WARNING: Removing unreachable block (ram,0x80001018) */\n"
        "void FUN_80001000(void) {}"), "wantedName")
    assert "DECOMPILER WARNING: Removing unreachable block" in text
    assert "/* WARNING" not in text


def test_decompiled_text_is_bounded():
    text = ghidra_context.render(evidence("x" * 500), "wantedName",
                                 max_decompiled_chars=100)
    assert "GHIDRA CONTEXT TRUNCATED" in text
    assert len(text) < 1200


def test_schema_two_high_ir_is_summarized_and_flags_dropped_blocks():
    value = evidence()
    value.update({
        "schema_version": 2,
        "parameters": [
            {"name": "param_1", "type": "int", "storage": "r4"},
        ],
        "locals": [
            {"name": "local_8", "type": "int", "storage": "Stack[-0x8]"},
        ],
        "callers": [
            {"site": "80000020", "entry": "80000000", "ghidra_name": "FUN_80000000"},
        ],
        "normalized_ir": {
            "completed": True,
            "error": "",
            "operation_count": 4,
            "pcode_truncated": False,
            "operations": [
                {"opcode": "LOAD", "inputs": ["ram", "v1"]},
                {"opcode": "CBRANCH", "inputs": ["v2"]},
                {"opcode": "MULTIEQUAL", "inputs": ["v3", "v4"]},
                {"opcode": "RETURN", "inputs": ["v5"]},
            ],
            "basic_blocks": [
                {"index": 0, "out": [1], "in": []},
                {"index": 1, "out": [], "in": [0]},
            ],
        },
        "bsim_signature": {
            "completed": True,
            "error": "",
            "settings": 77,
            "feature_count": 3,
            "features": ["11111111", "22222222", "22222222"],
        },
    })
    value["function"].update({
        "signature": "int FUN_80001000(int param_1)",
        "calling_convention": "__stdcall",
    })

    text = ghidra_context.render(value, "wantedName")

    assert "4 operations, 2 semantic CFG blocks" in text
    assert "multiequal=1" in text
    assert "semantic CFG has 1 fewer block" in text
    assert "int param_1@r4" in text
    assert "Recovered callers: 80000000" in text
    assert "BSim semantic fingerprint: 3 features" in text
    assert "11111111" not in text
    assert "v3" not in text  # Full normalized IR stays out of the prompt.


def test_load_rejects_wrong_language(tmp_path):
    path = tmp_path / "evidence.json"
    wrong = evidence()
    wrong["program"]["language"] = "x86:LE:64:default"
    path.write_text(json.dumps(wrong), encoding="utf-8")
    with pytest.raises(ValueError, match="expected MIPS"):
        ghidra_context.load(path)


def test_load_accepts_schema_two_and_rejects_unknown_schema(tmp_path):
    path = tmp_path / "evidence.json"
    value = evidence()
    value["schema_version"] = 2
    path.write_text(json.dumps(value), encoding="utf-8")
    assert ghidra_context.load(path)["schema_version"] == 2

    value["schema_version"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        ghidra_context.load(path)
