"""Completed callees propagate callsite constraints, not source-body noise."""

from __future__ import annotations

import sqlite3

from solver import callsite_contracts, dataflow


ASM = """\
glabel parent
    move $s0, $a0
    jal exactAccessor
    lw $a1, 0x14($s0)
    move $s1, $v0
    lw $t0, 0x8($s1)
    beqz $t0, .Ldone
    nop
.Ldone:
    move $v0, $s1
    jr $ra
    nop
"""


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions (
            addr integer primary key, name text, size integer,
            insn_count integer, is_leaf integer
        );
        create table evidence (
            id integer primary key, kind text, addr integer, func_addr integer,
            target_addr integer, op text
        );
        insert into functions values (0x1000, 'parent', 48, 12, 0);
        insert into functions values (0x2000, 'exactAccessor', 16, 4, 1);
        insert into evidence values
            (1, 'call', 0x1008, 0x1000, 0x2000, 'jal');
    """)
    return conn


def _library() -> dict:
    return {
        "digest": "frozen",
        "nodes": {
            "exactAccessor": {
                "trust": {
                    "exact": True, "attempt_id": 9, "strategy": "system",
                },
                "machine": {
                    "memory_shapes": ["param0@+0x4:4:int:signed:read"],
                    "direct_calls": [], "indirect_call_count": 0,
                },
                "source_profile": {"call_sequence": [], "control": {}},
                "exact_source": (
                    "void *exactAccessor(Thing *thing, s32 index) {\n"
                    "    return thing->items[index];\n"
                    "}"),
            },
        },
    }


def test_dataflow_captures_delay_slot_arguments_and_call_result_identity():
    result = dataflow.analyse(ASM)
    call = next(iter(result.callsites.values()))

    assert call.target == "exactAccessor"
    assert call.arguments[0].describe() == "param0"
    assert call.arguments[1].describe() == "load(param0+0x14)"
    assert call.delay_slot == call.instruction + 1
    load = next(access for access in result.accesses.values()
                if access.address and "ret(exactAccessor" in access.address.describe())
    assert load.address.describe().endswith("+0x8")


def test_contract_binds_exact_abi_effects_and_return_uses_to_parent():
    bundle = callsite_contracts.build(_db(), "parent", ASM, _library())
    row = bundle["callsites"][0]
    contract = row["exact_callee_contract"]

    assert row["pc"] == "0x00001008"
    assert row["arguments"][0]["value"] == "param0"
    assert row["arguments"][1]["value"] == \
        "load_32(byte_address(param0+0x14))"
    assert row["arguments"][1]["binary_value"] == {
        "kind": "memory_load",
        "width": 4,
        "signed": None,
        "post_load_byte_offset": 0,
        "expression": "load_32(byte_address(param0+0x14))",
        "base": "param0",
        "byte_offset": 0x14,
        "base_kind": "caller_identity",
        "compatible_element_index": 5,
    }
    assert contract["return"]["class"] == "pointer"
    assert contract["parameters"][0]["stable_id"] == "param0"
    assert row["bound_effects"] == ["param0@+0x4:4:int:signed:read"]
    kinds = {use["kind"] for use in row["return_uses"]}
    assert "return_used_as_memory_address" in kinds
    assert "return_forwarded_by_parent" in kinds
    assert bundle["coverage"]["exact_callee_contracts"] == 1


def test_render_is_compact_and_never_contains_exact_body():
    bundle = callsite_contracts.build(_db(), "parent", ASM, _library())
    rendered = callsite_contracts.render(bundle)

    assert "param1 (s32) <- raw 32-bit load from caller_identity param0" \
        in rendered
    assert "byte offset 0x14; compatible 4-byte element index 0x5" \
        in rendered
    assert "return_used_as_memory_address" in rendered
    assert "read from ret(exactAccessor" in rendered
    assert "thing->items[index]" not in rendered


def test_recovered_exact_node_is_not_propagated_as_autonomous_knowledge():
    library = _library()
    library["nodes"]["exactAccessor"]["trust"]["strategy"] = \
        "authorized-target-history-recovery"

    bundle = callsite_contracts.build(_db(), "parent", ASM, library)

    assert bundle["callsites"][0]["exact_callee_contract"] is None
    assert callsite_contracts.render(bundle) == ""


def test_exact_void_contract_discards_stale_v0_return_artifacts():
    library = _library()
    library["nodes"]["exactAccessor"]["exact_source"] = (
        "void exactAccessor(Thing *thing, s32 index) {\n"
        "    thing->index = index;\n"
        "}")

    bundle = callsite_contracts.build(_db(), "parent", ASM, library)
    row = bundle["callsites"][0]

    assert row["exact_callee_contract"]["return"]["class"] == "void"
    assert row["return_uses"] == []
    assert row["return_consumed"] is False
    assert row["discarded_void_result_traces"] > 0


def test_candidate_validator_enforces_leaf_argument_binary_facts():
    bundle = callsite_contracts.build(_db(), "parent", ASM, _library())

    good = callsite_contracts.validate_candidate_asm(bundle, ASM)
    bad = callsite_contracts.validate_candidate_asm(
        bundle, ASM.replace("lw $a1, 0x14($s0)", "lw $a1, 0x18($s0)"))

    assert good["passed"] is True
    assert good["argument_checks"] == 2
    assert bad["passed"] is False
    mismatch = next(row for row in bad["mismatches"]
                    if row["kind"] == "argument" and row["parameter"] == 1)
    assert mismatch["expected"]["byte_offset"] == 0x14
    assert mismatch["observed"]["byte_offset"] == 0x18


def test_candidate_validator_enforces_leaf_return_consumers():
    bundle = callsite_contracts.build(_db(), "parent", ASM, _library())
    candidate = ASM.replace("move $s1, $v0", "move $s1, $zero")

    result = callsite_contracts.validate_candidate_asm(bundle, candidate)

    assert result["argument_checks"] == 2
    assert result["return_use_checks"] > 0
    assert result["passed"] is False
    assert any(row["kind"] == "return_use" for row in result["mismatches"])


def test_return_use_normalization_handles_disassembler_spelling_aliases():
    target_store = {"kind": "return_value_stored",
                    "instruction": "sw $v0, 0x4($a2)"}
    candidate_store = {"kind": "return_value_stored",
                       "instruction": "sw $v0, 4($t0)"}
    target_copy = {"kind": "return_transformed_or_copied",
                   "instruction": "or $a2, $v0, $zero"}
    candidate_copy = {"kind": "return_transformed_or_copied",
                      "instruction": "move $a2, $v0"}

    assert callsite_contracts._return_use_fact(target_store) == \
        callsite_contracts._return_use_fact(candidate_store)
    assert callsite_contracts._return_use_fact(target_copy) == \
        callsite_contracts._return_use_fact(candidate_copy)


def test_candidate_validator_resolves_numeric_objdump_branch_targets():
    bundle = callsite_contracts.build(_db(), "parent", ASM, _library())
    normalized_objdump = ASM.replace("beqz $t0, .Ldone", "beqz $t0, 1c") \
        .replace(".Ldone:\n", "")

    result = callsite_contracts.validate_candidate_asm(
        bundle, normalized_objdump)

    assert result["passed"] is True
    assert result["argument_checks"] == 2
