"""On-demand callee inspection reveals only valid, explicitly requested work."""

from __future__ import annotations

import pytest

from eval import on_demand_callee_pilot as pilot


def _packet() -> dict:
    return {
        "address": "0x80001000",
        "name": "completedLeaf",
        "parent_call_sites": 1,
        "prototype": "s32 completedLeaf(s32 value);",
        "memory_effects": ["param0+0x4: 4-byte read x1"],
        "exact_source": "SECRET EXACT BODY",
        "semantic_annotation": {
            "summary": "Returns a state-derived value.",
            "return_role": "derived state",
            "confidence": 0.8,
        },
    }


def test_catalog_is_compact_and_never_reveals_the_body():
    rendered = pilot._catalog([_packet()])

    assert "completedLeaf" in rendered
    assert "s32 completedLeaf(s32 value);" in rendered
    assert "Returns a state-derived value" in rendered
    assert "SECRET EXACT BODY" not in rendered


def test_inspection_prompt_has_call_local_evidence_but_no_body():
    prompt = pilot._inspection_prompt(
        "parent", "jal completedLeaf\nnop", "return completedLeaf(arg0);",
        [_packet()])

    assert "jal completedLeaf" in prompt
    assert "param0+0x4" in prompt
    assert "SECRET EXACT BODY" not in prompt


def test_selector_accepts_abstention_and_one_known_name():
    abstain = pilot._parse_selection(
        '{"inspect":[],"reason":"prototype sufficient",'
        '"expected_constraints":[],"confidence":0.8}', {"completedLeaf"})
    selected = pilot._parse_selection(
        '{"inspect":["completedLeaf"],"reason":"return shape matters",'
        '"expected_constraints":["return expression"],"confidence":0.7}',
        {"completedLeaf"})

    assert abstain["inspect"] == []
    assert selected["inspect"] == ["completedLeaf"]


def test_selector_rejects_unknown_or_excessive_requests():
    with pytest.raises(ValueError, match="unavailable"):
        pilot._parse_selection(
            '{"inspect":["invented"],"confidence":0.5}', {"completedLeaf"})
    with pytest.raises(ValueError, match="more than 1"):
        pilot._parse_selection(
            '{"inspect":["a","b"],"confidence":0.5}', {"a", "b"})


def test_three_draw_order_is_position_balanced():
    orders = [pilot._arm_order(0, draw) for draw in (1, 2, 3)]

    for arm in pilot.ARMS:
        assert sorted(order.index(arm) for order in orders) == [0, 1, 2]


def test_assessment_distinguishes_selector_abstention_from_null_treatment():
    rows = [{
        "function": "parent", "arm": arm, "compiled": False,
        "score": 0.0, "exact": False,
    } for arm in pilot.ARMS]

    assessment = pilot._assessment(rows, inspected=False)

    assert assessment["status"] == "selector_abstained_full_body_not_tested"
    assert assessment["selector_revealed_full_body"] is False
