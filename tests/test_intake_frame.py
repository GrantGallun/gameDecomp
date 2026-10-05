"""The measurement harness's own frame construction, tested on a synthetic KB.

WHY THIS FILE EXISTS. Every negative result in this line of work so far has been traceable to the harness
rather than to the mechanism under test: a selection predicate that did not select the failure, a pool
that mutated between runs, and -- in this session -- a `--want` cap that silently truncated a 40-function
frozen frame to 12 rows. The frame builder is the part of the harness that decides what gets measured, so
it is the part that needs tests asserting it FIRES correctly, not just that it declines politely.

The tests use a hand-built SQLite file, so they need no repo, no compiler and no network.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.intake_probe import bucket_frame, tier_of, unsolved_by_tier    # noqa: E402


def make_kb(tmp_path: Path, rows: list[tuple[str, int, str]]) -> Path:
    """(name, size, stderr) -> a KB with one `functions` row and one `attempts` row per function."""
    path = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("create table functions (addr integer primary key, name text, size integer, "
                     "state text, attempts integer, best_score real)")
        conn.execute("create table attempts (id integer primary key autoincrement, func_addr integer, "
                     "compiled integer, compiler_stderr text, exact integer)")
        for index, (name, size, stderr) in enumerate(rows, start=1):
            conn.execute("insert into functions (addr, name, size, state) values (?, ?, ?, 'pending')",
                         (index, name, size))
            conn.execute("insert into attempts (func_addr, compiled, compiler_stderr, exact) "
                         "values (?, 0, ?, 0)", (index, stderr))
        conn.commit()
    finally:
        conn.close()
    return path


def test_tier_boundaries_are_the_projects_existing_strata():
    assert tier_of(0) == "tiny" and tier_of(19) == "tiny"
    assert tier_of(20) == "small" and tier_of(59) == "small"
    assert tier_of(60) == "medium" and tier_of(149) == "medium"
    assert tier_of(150) == "large" and tier_of(299) == "large"
    assert tier_of(300) == "huge" and tier_of(10 ** 7) == "huge"
    assert tier_of(None) == "tiny", "an unknown size must land somewhere, not raise"


def test_the_frame_is_drawn_from_every_tier_not_from_the_smallest(tmp_path):
    """THE BIAS THIS FRAME EXISTS TO REMOVE. The old pool was `order by size asc limit N`, so a small
    limit could only ever return small functions. The bucketed frame must reach every tier."""
    kb = make_kb(tmp_path, [
        ("tiny_a", 10, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("small_a", 40, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("medium_a", 100, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("large_a", 200, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("huge_a", 5000, "cfe: Error: candidate.c, line 5: Syntax Error"),
    ])
    entries, allocation = bucket_frame(kb, 5, exclude=set(), failure_class="syntax-error")
    assert [e["tier"] for e in entries] == ["tiny", "small", "medium", "large", "huge"]
    assert allocation["by_tier"] == {"tiny": 1, "small": 1, "medium": 1, "large": 1, "huge": 1}
    assert allocation["shortfall"] == 0
    assert {e["function"] for e in entries} == {"tiny_a", "small_a", "medium_a", "large_a", "huge_a"}


def test_only_the_requested_failure_class_is_drawn(tmp_path):
    """The frame's whole claim is that it measures ONE measured failure class. An undeclared-symbol draft
    that leaks in is a different question with a different owner."""
    kb = make_kb(tmp_path, [
        ("syntax", 100, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("undeclared", 100, "cfe: Error: candidate.c, line 4: 'gFoo' undefined"),
        ("selector", 100, "cfe: Error: candidate.c, line 9: Selector requires struct/union pointer"),
        ("clean", 100, ""),
    ])
    entries, _ = bucket_frame(kb, 10, exclude=set(), failure_class="syntax-error")
    assert [e["function"] for e in entries] == ["syntax"]
    pool = unsolved_by_tier(kb, exclude=set())
    classes = {e["function"]: e["failure_class"] for e in pool["medium"]}
    assert classes == {"syntax": "syntax-error", "undeclared": "undeclared-symbol",
                       "selector": "selector", "clean": "compiles"}


def test_a_thin_tier_is_reported_as_a_shortfall_or_filled_by_another(tmp_path):
    """A tier that cannot fill its quota must not silently shrink the frame. Either another tier takes the
    slot -- and the composition says so -- or the shortfall is stated."""
    kb = make_kb(tmp_path, [
        ("huge_1", 900, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("huge_2", 800, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("huge_3", 700, "cfe: Error: candidate.c, line 5: Syntax Error"),
    ])
    entries, allocation = bucket_frame(kb, 3, exclude=set(), failure_class="syntax-error")
    assert len(entries) == 3, "the frame must still reach the requested size when another tier can fill it"
    assert allocation["by_tier"]["huge"] == 3
    assert allocation["shortfall"] == 0
    assert set(allocation["tiers_exhausted"]) == {"tiny", "small", "medium", "large"}

    entries, allocation = bucket_frame(kb, 6, exclude=set(), failure_class="syntax-error")
    assert len(entries) == 3
    assert allocation["shortfall"] == 3, "an unfillable frame must SAY it is short, not just be short"


def test_per_tier_quota_is_honoured_and_round_robin_advances(tmp_path):
    """`--per-tier` is the knob the size-bucket run uses. With a quota of 2 and five tiers, the first ten
    members must be two per tier; round-robin order keeps a partially filled frame balanced too."""
    rows = []
    for tier, size in (("tiny", 10), ("small", 40), ("medium", 100), ("large", 200), ("huge", 900)):
        for index in range(3):
            rows.append((f"{tier}_{index}", size + index,
                         "cfe: Error: candidate.c, line 5: Syntax Error"))
    kb = make_kb(tmp_path, rows)
    entries, allocation = bucket_frame(kb, 10, exclude=set(), failure_class="syntax-error",
                                       per_tier=2)
    assert allocation["by_tier"] == {"tiny": 2, "small": 2, "medium": 2, "large": 2, "huge": 2}
    assert [e["tier"] for e in entries[:5]] == ["tiny", "small", "medium", "large", "huge"]

    entries, allocation = bucket_frame(kb, 12, exclude=set(), failure_class="syntax-error",
                                       per_tier=2)
    assert len(entries) == 12, "once the quota is met, the remaining slots come from any tier with stock"


def test_held_out_functions_never_enter_the_frame(tmp_path):
    """An evaluation frame that contains held-out functions is not an evaluation."""
    kb = make_kb(tmp_path, [
        ("heldout", 100, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("trainable", 100, "cfe: Error: candidate.c, line 5: Syntax Error"),
    ])
    entries, allocation = bucket_frame(kb, 5, exclude={"heldout"}, failure_class="syntax-error")
    assert [e["function"] for e in entries] == ["trainable"]
    assert allocation["pool_by_tier"]["medium"] == 1


def test_a_function_with_an_exact_attempt_is_not_unsolved(tmp_path):
    """The pool predicate is `no exact attempt`. If it stops selecting the failure, every rate computed
    on the frame is a rate over the wrong population."""
    kb = make_kb(tmp_path, [("solved", 100, "cfe: Error: candidate.c: Syntax Error")])
    conn = sqlite3.connect(str(kb))
    try:
        conn.execute("update attempts set exact = 1")
        conn.commit()
    finally:
        conn.close()
    pool = unsolved_by_tier(kb, exclude=set())
    assert all(not entries for entries in pool.values())

    entries, allocation = bucket_frame(kb, 1, exclude=set(), failure_class="syntax-error")
    assert entries == [] and allocation["shortfall"] == 1


def test_the_allocation_reports_the_stock_it_drew_from_not_the_leftovers(tmp_path):
    """`matching_by_tier` is consumed by the selection loop, so reading its length afterwards reports what
    was LEFT. The 200-state frame printed `{small: 0, medium: 0, large: 0, huge: 130}` beside a
    `by_tier` of `{small: 2, medium: 34, large: 57, huge: 107}` -- a composition report claiming the
    frame drew from tiers that were empty while it was drawing 34 from one of them."""
    kb = make_kb(tmp_path, [(f"m{i}", 100 + i, "cfe: Error: candidate.c, line 5: Syntax Error")
                            for i in range(5)]
                 + [(f"h{i}", 900 + i, "cfe: Error: candidate.c, line 5: Syntax Error")
                    for i in range(3)])
    _entries, allocation = bucket_frame(kb, 4, exclude=set(), failure_class="syntax-error")
    assert allocation["matching_by_tier"]["medium"] == 5, "the stock before selection"
    assert allocation["matching_by_tier"]["huge"] == 3
    assert allocation["by_tier"]["medium"] + allocation["remaining_by_tier"]["medium"] == 5
    assert allocation["by_tier"]["huge"] + allocation["remaining_by_tier"]["huge"] == 3
    assert allocation["remaining_by_tier"]["medium"] == 5 - allocation["by_tier"]["medium"]


def test_biggest_first_inside_a_tier_is_deterministic(tmp_path):
    """A frame whose membership changes between two runs of the same command is not a frame -- the pool
    was observed to move by 2 of 40 under a live campaign, so the tie-breaking rule is pinned here."""
    kb = make_kb(tmp_path, [
        ("mid", 100, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("big", 140, "cfe: Error: candidate.c, line 5: Syntax Error"),
        ("small", 80, "cfe: Error: candidate.c, line 5: Syntax Error"),
    ])
    first, _ = bucket_frame(kb, 2, exclude=set(), failure_class="syntax-error")
    second, _ = bucket_frame(kb, 2, exclude=set(), failure_class="syntax-error")
    assert [e["function"] for e in first] == [e["function"] for e in second] == ["big", "mid"]


@pytest.mark.parametrize("want", [0, -1])
def test_a_nonpositive_frame_size_is_an_empty_frame_not_a_crash(tmp_path, want):
    kb = make_kb(tmp_path, [("a", 100, "cfe: Error: candidate.c, line 5: Syntax Error")])
    entries, allocation = bucket_frame(kb, want, exclude=set(), failure_class="syntax-error")
    assert entries == [] and allocation["taken"] == 0


# --- the frame's own sensitivity: an instrument that cannot state its resolution is a sample ---------

def test_resolution_scales_with_the_frame_and_never_claims_sub_state_precision():
    """A percentage-only floor is meaningless at small n: 4% of a 12-state frame is half a state. The
    floor is therefore a state count with a minimum, and it must never exceed the frame."""
    from eval.intake_probe import MIN_RESOLVABLE_STATES, resolution

    small = resolution(12)
    assert small["across_frames"]["resolvable_states"] == MIN_RESOLVABLE_STATES
    assert small["across_frames"]["resolvable_states"] <= 12

    wide = resolution(200)
    assert wide["across_frames"]["resolvable_states"] > small["across_frames"]["resolvable_states"], \
        "a bigger frame must resolve a bigger absolute effect, or it was not worth building"

    empty = resolution(0)
    assert empty["across_frames"] is None


def test_resolution_separates_the_two_kinds_of_comparison():
    """THE DEFECT IN THE FIRST VERSION OF THIS METRIC. It reported a single floor of 20 states (10%) for a
    200-state frame. That is true of a FRESH BUILD, whose membership is re-derived from a live KB, and
    false of two ARMS on one frozen frame -- those run on identical states and share every one of them.
    Conflating the two would tell a reader that a 5-state arm difference is noise when it is not."""
    from eval.intake_probe import resolution

    report = resolution(200)
    assert report["across_frames"]["resolvable_states"] == 20
    assert report["arms_on_one_frozen_frame"]["resolvable_states"] == 2
    assert report["arms_on_one_frozen_frame"]["resolvable_states"] < \
        report["across_frames"]["resolvable_states"]
    assert "share every state" in report["note"]


def test_resolution_states_the_drift_it_assumes_and_where_it_came_from():
    """The number is only usable next to its basis. The default is the turnover OBSERVED between two
    consecutive builds of one command (4 of 40), not a round figure someone liked."""
    from eval.intake_probe import resolution

    report = resolution(200)
    assert report["assumed_membership_drift"] > 0
    assert "4 of 40" in report["basis"]
    assert report["drift_states_at_this_size"] == 200 * report["assumed_membership_drift"]


def test_resolution_is_bounded_by_the_frame():
    """A floor larger than the frame would claim the frame cannot measure anything, which is a different
    statement from 'this frame is small'."""
    from eval.intake_probe import resolution

    tiny = resolution(3)
    assert tiny["across_frames"]["resolvable_states"] <= 3

