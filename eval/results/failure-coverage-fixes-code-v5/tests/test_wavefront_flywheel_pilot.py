"""The combined pilot freezes evidence and keeps its four arms identifiable."""

from __future__ import annotations

import sqlite3

from eval import wavefront_flywheel_pilot as pilot


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions (
            addr integer primary key, name text, size integer,
            insn_count integer, is_leaf integer
        );
        create table evidence (
            id integer primary key, kind text, func_addr integer,
            target_addr integer, base text, offset integer, width integer,
            signed integer, class text, is_load integer, op text
        );
        create table attempts (
            id integer primary key, func_addr integer, source_code text,
            strategy text, exact integer
        );
        insert into functions values (1, 'parent', 100, 25, 0);
        insert into functions values (2, 'frozenLeaf', 20, 5, 1);
        insert into functions values (3, 'liveOnlyLeaf', 16, 4, 1);
        insert into evidence values
            (1, 'call', 1, 2, null, null, null, null, null, null, 'jal'),
            (2, 'call', 1, 3, null, null, null, null, null, null, 'jal'),
            (3, 'mem_access', 2, null, 'param0', 4, 4, 1, 'data', 1, 'lw');
        insert into attempts values
            (10, 2, 's32 frozenLeaf(s32 x) { return x; }', 'system', 1),
            (11, 3, 's32 liveOnlyLeaf(void) { return 3; }', 'system', 1);
    """)
    return conn


def _library() -> dict:
    return {
        "nodes": {
            "frozenLeaf": {
                "trust": {"exact": True, "attempt_id": 7,
                          "strategy": "frozen-system"},
                "exact_source": (
                    "s32 frozenLeaf(s32 value) {\n"
                    "    return value + 0;\n"
                    "}"),
            },
        },
    }


def test_packets_use_frozen_membership_source_and_receipt():
    packets = pilot._freeze_packets(
        _db(), "parent", _library(), max_callees=2)

    assert [packet["name"] for packet in packets] == ["frozenLeaf"]
    packet = packets[0]
    assert packet["exact_attempt_id"] == 7
    assert packet["exact_strategy"] == "frozen-system"
    assert "value + 0" in packet["exact_source"]
    assert packet["prototype"] == "s32 frozenLeaf(s32 value);"
    assert packet["compatible_return_expressions"] == ["value + 0"]


def test_context_is_injected_before_the_final_generation_request():
    prompt = "facts\nProduce the corrected C file now.\n"
    rendered = pilot._inject(prompt, "NEW CONTEXT")

    assert rendered.index("NEW CONTEXT") < rendered.index("Produce the corrected")
    assert rendered.count("Produce the corrected") == 1


def test_arm_order_counterbalances_two_functions_and_draws():
    orders = [pilot._arm_order(function, draw)
              for function in range(2) for draw in (1, 2)]

    assert orders.count(pilot.ARMS) == 2
    assert orders.count(tuple(reversed(pilot.ARMS))) == 2


def test_assessment_reports_combined_marginal_and_interaction():
    scores = {
        "control": 10.0,
        "flywheel": 20.0,
        "wavefront": 30.0,
        "combined": 50.0,
    }
    rows = [{
        "function": "parent", "arm": arm, "score": score,
        "compiled": True, "exact": arm == "combined",
    } for arm, score in scores.items()]

    result = pilot._assessment(rows)

    assert result["status"] == "combined_exact_gain_observed_needs_replication"
    assert result["combined_vs_flywheel_mean_best_delta"] == 30.0
    assert result["additive_interaction_mean_best"] == 10.0
