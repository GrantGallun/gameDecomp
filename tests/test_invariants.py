"""Invariant reading, level order, and lexicographic ranking on object truth."""
from __future__ import annotations

from solver import invariants as inv

TARGET = """
addiu    sp,sp,-0x20
sw    ra,0x1c(sp)
sw    s0,0x18(sp)
lh    t0,0x1c(a0)
beqz    t0,.L1
move    s0,a0
jal    drawMenuGlyphScript
li    a1,1
lw    ra,0x1c(sp)
lw    s0,0x18(sp)
addiu    sp,sp,0x20
jr    ra
"""


def swap(text, a, b):
    return text.replace(a, "\x00").replace(b, a).replace("\x00", b)


def dist_of(candidate: str) -> tuple:
    return inv.distance(inv.parse(TARGET), inv.parse(candidate))


def test_invariants_are_read_from_the_dump() -> None:
    got = inv.Invariants.of(inv.parse(TARGET))
    assert got.frame == 0x20 and got.saved == frozenset({"ra", "s0"})
    assert got.calls == ("drawMenuGlyphScript",) and got.branches == 2   # beqz + jr
    assert got.instructions == 12


def test_identical_listing_has_no_level() -> None:
    assert dist_of(TARGET) == (0, 0, 0, 0, 0, 0) and inv.level(dist_of(TARGET)) is None


def test_each_level_fires_on_its_own_case() -> None:
    assert inv.level(dist_of(TARGET.replace("drawMenuGlyphScript", "otherCall"))) == "calls"
    assert inv.level(dist_of(TARGET.replace("beqz    t0,.L1\n", ""))) == "control-flow"
    assert inv.level(dist_of(TARGET.replace("-0x20", "-0x28"))) == "frame"
    assert inv.level(dist_of(TARGET.replace("li    a1,1", "addiu    a1,zero,1"))) == "expressions"
    assert inv.level(dist_of(TARGET.replace("lh    t0,0x1c(a0)", "lh    t0,0x1e(a0)"))) == "operands"
    assert inv.level(dist_of(TARGET.replace("lh    t0,0x1c(a0)", "lh    t1,0x1c(a0)"))) == "registers"


def test_a_higher_level_dominates_the_score() -> None:
    """Object truth first: repairing the call sequence beats a higher similarity score."""
    wrong_call_high_score = inv.rank_key(dist_of(TARGET.replace("drawMenuGlyphScript", "x")),
                                         exact=False, score=99.0)
    right_call_low_score = inv.rank_key(dist_of(TARGET.replace("lh    t0", "lh    t3")),
                                        exact=False, score=80.0)
    assert right_call_low_score < wrong_call_high_score
    assert inv.rank_key((0,) * 6, exact=True, score=0.0) < right_call_low_score
    same = dist_of(TARGET.replace("lh    t0", "lh    t3"))
    assert inv.rank_key(same, False, 90.0) < inv.rank_key(same, False, 85.0), "score breaks ties"


def test_register_masking_does_not_eat_symbols_or_offsets() -> None:
    a, b = inv.Insn("lui", "t6,%hi(gSaveData)"), inv.Insn("lui", "t7,%hi(gSaveData)")
    assert a.masked == b.masked == "R,%hi(gSaveData)"
    assert inv.Insn("lw", "s0,0x18(sp)").masked == "R,0x18(R)"


def unified(target: str, candidate: str, context: int = 1) -> str:
    import difflib
    return "\n".join(difflib.unified_diff(target.strip().splitlines(),
                                          candidate.strip().splitlines(),
                                          "target_object_dump_normalized.s", "cand.s",
                                          lineterm="", n=context))


def test_distance_from_a_short_context_diff_equals_the_full_listing_distance() -> None:
    """Omitted regions are identical on both sides, so per-hunk distances sum to the whole."""
    cases = [TARGET.replace("drawMenuGlyphScript", "otherCall"),
             TARGET.replace("beqz    t0,.L1\n", ""),
             TARGET.replace("-0x20", "-0x28").replace("0x20\njr", "0x28\njr"),
             TARGET.replace("li    a1,1", "addiu    a1,zero,1"),
             TARGET.replace("lh    t0,0x1c(a0)", "lh    t1,0x1c(a0)")
                   .replace("lw    s0,0x18(sp)", "lw    s0,0x14(sp)")]
    for candidate in cases:
        full = inv.distance(inv.parse(TARGET), inv.parse(candidate))
        assert inv.distance_from_diff(unified(TARGET, candidate)) == full, candidate


def test_branch_delta_is_signed_across_hunks() -> None:
    """A branch removed in one hunk and added in another is not a control-flow difference."""
    moved = TARGET.replace("beqz    t0,.L1\n", "").replace("li    a1,1", "li    a1,1\nbnez    a1,.L2")
    full = inv.distance(inv.parse(TARGET), inv.parse(moved))
    assert full[1] == 0
    assert inv.distance_from_diff(unified(TARGET, moved))[1] == 0


def test_guide_names_the_level_and_the_target_values() -> None:
    candidate = inv.parse(TARGET.replace("-0x20", "-0x28"))
    text = inv.guide("frame", inv.parse(TARGET), candidate)
    assert "FRAME" in text and "0x20" in text and "0x28" in text and "s0" in text
    assert inv.guide(None, inv.parse(TARGET), candidate) == ""
