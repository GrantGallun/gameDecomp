"""Branch points: parse/validate against the parent, apply, keep flat variants, plan combinations."""
from __future__ import annotations

import json

import pytest

from solver import branch_points as bp

SOURCE = (
    '#include "common.h"\n'                       # L1 (preprocessor: no slot)
    "\n"                                          # L2
    "void f(s16 *arr, s32 n) {\n"                 # L3
    "    s32 i;\n"                                # L4
    "    s32 sum = 0;\n"                          # L5
    "    for (i = 0; i < n; i++) {\n"             # L6
    "        sum = sum + *(arr + i);\n"           # L7
    "    }\n"                                     # L8
    "    arr[0] = sum;\n"                         # L9
    "}\n"                                         # L10
)


def response(*points) -> str:
    return json.dumps({"branch_points": list(points)})


def test_prompt_carries_evidence_and_the_slot_table() -> None:
    prompt = bp.build_prompt("glabel f", SOURCE, 88.5, "-addu v0,v0,a0", "MISMATCH SITES: L7")
    assert "glabel f" in prompt and "-addu v0,v0,a0" in prompt and "MISMATCH SITES: L7" in prompt
    assert "EDITABLE SOURCE SLOTS" in prompt and "L7:" in prompt
    assert "the compiler will choose" in prompt


def test_generator_fires_on_a_valid_multi_point_response() -> None:
    parsed = bp.parse(response(
        {"slot": "L7", "why": "indexing vs pointer arithmetic",
         "alternatives": ["        sum = sum + arr[i];", "        sum += arr[i];"]},
        {"slot": "L6", "through": "L8", "why": "loop form",
         "alternatives": ["    i = 0;\n    while (i < n) {\n        sum = sum + *(arr + i);\n"
                          "        i++;\n    }"]}), SOURCE)
    assert [p.slot for p in parsed.points] == ["L7", "L6"] and parsed.rejects == []
    loop = parsed.points[1]
    assert loop.through == "L8" and loop.current.startswith("    for (") and loop.current.endswith("}")
    rewritten = bp.apply(SOURCE, [(loop, loop.alternatives[0])])
    assert "while (i < n)" in rewritten and "for (" not in rewritten
    assert rewritten.startswith('#include "common.h"') and rewritten.endswith("}\n")


def test_each_rejection_fires_with_its_reason() -> None:
    parsed = bp.parse(response(
        {"slot": "L99", "why": "", "alternatives": ["x;"]},                 # unknown slot
        {"slot": "L1", "why": "", "alternatives": ["#include \"x.h\""]},     # preprocessor: no slot
        {"slot": "L9", "through": "L4", "why": "", "alternatives": ["y;"]},  # backwards span
        {"slot": "L9", "why": "", "alternatives": ["    arr[0] = sum;", "    arr[0]=sum;"]},
    ), SOURCE)
    reasons = [r["reason"] for r in parsed.rejects]
    assert reasons.count("unknown-slot") == 2 and "bad-span" in reasons
    assert reasons.count("empty-identical-or-duplicate") == 2, \
        "whitespace-only restatements of the current line are not alternatives"
    assert parsed.points == []
    assert bp.parse("not json", SOURCE).rejects[0]["reason"] == "unparseable"
    assert bp.parse('{"branch_points": 3}', SOURCE).rejects[0]["reason"] == "unparseable"


def test_span_limit() -> None:
    lines = "".join(f"    x{i} = {i};\n" for i in range(20))
    src = "void g(void) {\n" + lines + "}\n"
    ok = bp.parse(response({"slot": "L2", "through": f"L{1 + bp.MAX_SPAN_LINES}", "why": "",
                            "alternatives": ["    y = 1;"]}), src)
    too_long = bp.parse(response({"slot": "L2", "through": f"L{2 + bp.MAX_SPAN_LINES}", "why": "",
                                  "alternatives": ["    y = 1;"]}), src)
    assert len(ok.points) == 1 and too_long.rejects[0]["reason"] == "bad-span"


def test_merge_pools_alternatives_for_the_same_span() -> None:
    a = bp.parse(response({"slot": "L7", "why": "a", "alternatives": ["        sum += arr[i];"]}),
                 SOURCE)
    b = bp.parse(response({"slot": "L7", "why": "b", "alternatives": ["        sum += arr[i];",
                                                                       "        sum += i[arr];"]}),
                 SOURCE)
    merged = bp.merge([a, b])
    assert len(merged) == 1 and merged[0].alternatives == ("        sum += arr[i];",
                                                           "        sum += i[arr];")


def test_apply_refuses_overlapping_choices() -> None:
    parsed = bp.parse(response(
        {"slot": "L7", "why": "", "alternatives": ["        sum += arr[i];"]},
        {"slot": "L6", "through": "L8", "why": "", "alternatives": ["    /* loop */"]}), SOURCE)
    inner, loop = parsed.points
    with pytest.raises(ValueError):
        bp.apply(SOURCE, [(inner, inner.alternatives[0]), (loop, loop.alternatives[0])])


def _outcome(point, alt, compiled=True, score=80.0, exact=False):
    return bp.Outcome(point, alt, compiled, score, exact)


def test_keepers_keep_only_improvements() -> None:
    parsed = bp.parse(response(
        {"slot": "L5", "why": "", "alternatives": ["    s32 sum;", "    register s32 sum = 0;"]},
        {"slot": "L7", "why": "", "alternatives": ["        sum += arr[i];", "        sum += i[arr];"]},
        {"slot": "L9", "why": "", "alternatives": ["    *arr = sum;"]}), SOURCE)
    decl, body, store = parsed.points
    outcomes = [
        _outcome(decl, decl.alternatives[0], compiled=False, score=0.0),   # not compiled
        _outcome(decl, decl.alternatives[1], score=80.0),                  # neutral flat: dropped
        _outcome(body, body.alternatives[0], score=83.0),                  # up
        _outcome(body, body.alternatives[1], score=85.0),                  # better up: wins
        _outcome(store, store.alternatives[0], score=79.0),                # down: dropped
    ]
    kept = bp.keepers(outcomes, parent_score=80.0)
    assert [(o.point.slot, o.score) for o in kept] == [("L7", 85.0)], \
        "neutral flats reached a match 0.06% of the time on the campaign: not kept"
    assert [bp.classify(o, 80.0) for o in outcomes] == ["not-compiled", "flat", "up", "up", "down"]


def test_noop_spellings_are_not_keepers() -> None:
    """A flat variant whose object is identical to the parent's is a no-op, not a basin probe."""
    parsed = bp.parse(response(
        {"slot": "L7", "why": "", "alternatives": ["        sum = sum + arr[i];",
                                                   "        sum += i[arr];"]}), SOURCE)
    point = parsed.points[0]
    noop = bp.Outcome(point, point.alternatives[0], True, 80.0, False, same_object=True)
    neutral = bp.Outcome(point, point.alternatives[1], True, 80.0, False, same_object=False)
    assert bp.classify(noop, 80.0) == "noop" and bp.classify(neutral, 80.0) == "flat"
    assert bp.keepers([noop, neutral], 80.0) == []


def test_tried_block_reports_what_the_compiler_ignored() -> None:
    block = bp.tried_block([("L7", "        sum += arr[i];", "noop"),
                            ("L9", "    *arr = sum;", "down"),
                            ("L5", "    s32 sum;", "up"),              # improvements are not "tried"
                            ("L4", "    int i;", "not-compiled")])
    assert "L7: sum += arr[i];" in block and "IDENTICAL object" in block
    assert "L9:" in block and "L4:" in block and "L5:" not in block
    assert bp.tried_block([("L5", "x", "up")]) == ""
    prompt = bp.build_prompt("glabel f", SOURCE, 80.0, "-x", "", tried=block)
    assert prompt.endswith(block)


def test_object_key_ignores_the_timestamped_diff_header() -> None:
    """The bug that booked 96,120 no-ops as 'neutral' steps: headers made every diff unique."""
    body = "@@ -1,2 +1,2 @@\n addiu sp,sp,-0x18\n-li a1,1\n+li a1,2\n"
    one = "--- target.s\t2026-09-27 13:17:11.87 -0500\n+++ f_base.s\t2026-09-27 13:17:11.88 -0500\n" + body
    two = "--- target.s\t2026-09-27 13:19:45.26 -0500\n+++ f_bp_1.s\t2026-09-27 13:19:45.27 -0500\n" + body
    assert one != two and bp.diff_body(one) == bp.diff_body(two)
    assert bp.object_key(True, False, one) == bp.object_key(True, False, two)
    assert bp.object_key(True, False, one) != bp.object_key(True, False, one.replace("a1,2", "a1,3"))
    assert bp.object_key(False, False, one) == "not-compiled"
    assert bp.object_key(True, True, one) == bp.object_key(True, True, "") == "exact"


def test_combination_plan_is_greedy_then_pairs_and_never_overlaps() -> None:
    parsed = bp.parse(response(
        {"slot": "L4", "why": "", "alternatives": ["    int i;"]},
        {"slot": "L5", "why": "", "alternatives": ["    s32 sum;"]},
        {"slot": "L7", "why": "", "alternatives": ["        sum += arr[i];"]},
        {"slot": "L6", "through": "L8", "why": "", "alternatives": ["    /* loop */"]}), SOURCE)
    i_decl, sum_decl, body, loop = parsed.points
    kept = [_outcome(body, body.alternatives[0], score=90.0),
            _outcome(loop, loop.alternatives[0], score=89.0),     # overlaps body: never with it
            _outcome(sum_decl, sum_decl.alternatives[0], score=85.0),
            _outcome(i_decl, i_decl.alternatives[0], score=80.0)]
    plans = bp.combination_plan(kept)
    as_slots = [[o.point.slot for o in plan] for plan in plans]
    assert as_slots[0] == ["L7", "L5"] and as_slots[1] == ["L7", "L5", "L4"]
    assert all(not ({"L7", "L6"} <= set(p)) for p in as_slots), "overlapping points combined"
    assert ["L6", "L5"] in as_slots, "pairs among the top keepers cover the non-greedy combination"
    for plan in plans:   # every plan must actually apply
        bp.apply(SOURCE, [(o.point, o.alternative) for o in plan])
