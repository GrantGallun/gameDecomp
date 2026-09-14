"""Completed callees become bounded, provenance-labelled parent context."""

import sqlite3

import pytest

from eval import callee_context_pilot
from solver import callee_context


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions (
            addr integer primary key,
            name text not null,
            size integer,
            insn_count integer
        );
        create table evidence (
            id integer primary key,
            kind text not null,
            func_addr integer,
            target_addr integer,
            base text,
            offset integer,
            width integer,
            is_load integer,
            op text
        );
        create table attempts (
            id integer primary key,
            func_addr integer,
            source_code text,
            strategy text,
            exact integer
        );

        insert into functions values (0x1000, 'parentFunction', 160, 40);
        insert into functions values (0x2000, 'lookupBlockBase', 32, 8);
        insert into functions values (0x3000, 'notYetExact', 24, 6);
        insert into functions values (0x4000, 'recoveredLeaf', 20, 5);
        insert into functions values (0x5000, 'helper', 12, 3);

        insert into evidence values
            (1, 'call', 0x1000, 0x2000, null, null, null, null, 'jal'),
            (2, 'call', 0x1000, 0x2000, null, null, null, null, 'jal'),
            (3, 'call', 0x1000, 0x3000, null, null, null, null, 'jal'),
            (4, 'call', 0x1000, 0x4000, null, null, null, null, 'jal'),
            (5, 'mem_access', 0x2000, null, 'param0', 4, 4, 1, 'lw'),
            (6, 'mem_access', 0x2000, null, 'global:0x80001000', 0, 4, 0, 'sw'),
            (7, 'call', 0x2000, 0x5000, null, null, null, null, 'jal'),
            (8, 'call', 0x2000, null, null, null, null, null, 'jalr');

        insert into attempts values (
            10, 0x2000,
            'void *lookupBlockBase(s32 handle) {\n return gBlocks[handle].base;\n}',
            'callgraph-leaf-first', 1
        );
        insert into attempts values (
            11, 0x3000,
            's32 notYetExact(s32 value) { return value; }',
            'sample', 0
        );
        insert into attempts values (
            12, 0x4000,
            's32 recoveredLeaf(void) { return 1; }',
            'authorized-target-history-recovery', 1
        );
    """)
    return conn


def test_packets_include_only_exact_direct_nonrecovered_callees_by_default():
    packets = callee_context.packets_for_parent(_db(), "parentFunction")

    assert [packet["name"] for packet in packets] == ["lookupBlockBase"]
    packet = packets[0]
    assert packet["address"] == "0x00002000"
    assert packet["parent_call_sites"] == 2
    assert packet["prototype"] == "void *lookupBlockBase(s32 handle);"
    assert "param0+0x4: 4-byte read x1" in packet["memory_effects"]
    assert "global:0x80001000+0x0: 4-byte write x1" in packet["memory_effects"]
    assert packet["direct_calls"] == ["helper"]
    assert packet["indirect_call_count"] == 1
    assert packet["compatible_return_expressions"] == [
        "gBlocks[handle].base"]


def test_recovered_callee_is_opt_in_and_visibly_labelled():
    packets = callee_context.packets_for_parent(
        _db(), "parentFunction", include_recovered=True)

    recovered = next(p for p in packets if p["name"] == "recoveredLeaf")
    assert recovered["recovered_from_target_source"] is True
    rendered = callee_context.render_packets([recovered])
    assert "target-source recovery (ceiling context)" in rendered


def test_semantic_annotation_keeps_only_stable_parameter_ids():
    annotation = callee_context.parse_annotation("""
        ```json
        {
          "summary": "Looks up a heap block base pointer.",
          "parameter_roles": {
            "param0": "heap handle",
            "inventedLocal": "must not survive"
          },
          "return_role": "block base address",
          "confidence": 0.82,
          "uncertainties": ["Element type remains ambiguous."]
        }
        ```
    """)

    assert annotation["parameter_roles"] == {"param0": "heap handle"}
    assert annotation["confidence"] == 0.82

    packet = callee_context.packets_for_parent(_db(), "parentFunction")[0]
    packet["semantic_annotation"] = annotation
    rendered = callee_context.render_packets(
        [packet], include_semantics=True)
    assert "SEMANTIC HYPOTHESIS" in rendered
    assert "param0=heap handle" in rendered
    assert "fallible hypotheses" in rendered


def test_annotation_rejects_unbounded_confidence():
    with pytest.raises(ValueError, match="between 0 and 1"):
        callee_context.parse_annotation(
            '{"summary":"guess","confidence":2,"parameter_roles":{}}')


def test_vacuous_semantic_abstention_is_not_given_to_parent():
    useful, reason = callee_context.annotation_is_useful({
        "summary": "Unable to determine behavior due to lack of provided code",
        "confidence": 0.0,
    })

    assert useful is False
    assert "abstained" in reason


def test_annotation_prompt_does_not_claim_semantics_are_facts():
    packet = callee_context.packets_for_parent(_db(), "parentFunction")[0]
    prompt = callee_context.annotation_prompt(packet)

    assert "Do not\nclaim" in prompt
    assert '"summary"' in prompt
    assert "0x00002000 lookupBlockBase" in prompt
    assert "COMPILER-COMPATIBLE BYTE-EXACT SOURCE" in prompt


def test_compact_parent_context_omits_source_but_keeps_constraints():
    packet = callee_context.packets_for_parent(_db(), "parentFunction")[0]
    rendered = callee_context.render_packets([packet], include_source=False)

    assert "compiler-compatible prototype" in rendered
    assert "param0+0x4: 4-byte read x1" in rendered
    assert "gBlocks[handle].base" in rendered
    assert "compiler-compatible exact body" not in rendered


def test_pilot_assessment_keeps_smoke_result_narrow():
    assessment = callee_context_pilot._assessment([
        {"arm": "baseline", "score": 80.0, "exact": False,
         "compiled": True},
        {"arm": "facts", "score": 82.5, "exact": False,
         "compiled": True},
        {"arm": "semantics", "score": 100.0, "exact": True,
         "compiled": True},
    ])

    assert assessment["facts_best_delta"] == 2.5
    assert assessment["semantics_exact_gain"] is True
    assert "not a population-level conclusion" in assessment["scope"]
