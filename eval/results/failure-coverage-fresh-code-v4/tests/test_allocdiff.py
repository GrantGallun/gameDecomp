"""Tests for allocation-residual diagnosis.

The prescription half of this module is REFUTED (50-58% against chance on an
identical 802-pair set), so the tests that matter most assert it stays gated.
The model-free half -- which web is mis-coloured and where it is referenced --
is read from the diff and is exact.
"""

from solver import allocdiff


def _d(pairs):
    out = ["--- target", "+++ candidate", "@@ -1,9 +1,9 @@"]
    for a, b in pairs:
        out.append("-" + a)
        out.append("+" + b)
    return "\n".join(out)


ALLOC = _d([
    ("move a3,a0", "move a2,a0"),
    ("lbu t8,0x6a(a3)", "lbu t8,0x6a(a2)"),
    ("sw v0,0x64(a3)", "sw v0,0x64(a2)"),
])


def test_anchor_names_the_earliest_miscoloured_web():
    a = allocdiff.anchor(ALLOC)
    assert a is not None
    assert a.target_reg == "a3" and a.cand_reg == "a2"


def test_direction_reads_the_colour_pool_order():
    """Target holding a HIGHER-indexed colour means it ranked LATER there."""
    a = allocdiff.anchor(ALLOC)
    assert a.direction == "later"


def test_prescription_options_are_withheld_by_default():
    """A refuted model does not get to steer; CLAUDE.md invariant."""
    p = allocdiff.prescribe(ALLOC)
    assert p["applicable"]
    assert p["options"] == []
    assert p["options_withheld"] is True


def test_prescription_options_require_explicit_opt_in():
    p = allocdiff.prescribe(ALLOC, unvalidated_ok=True)
    assert p["options_withheld"] is False


def test_not_applicable_when_streams_differ_in_length():
    bad = "--- target\n+++ candidate\n-move a3,a0\n-nop\n+move a2,a0"
    assert not allocdiff.applicable(bad)


def test_web_instructions_are_read_from_the_diff_not_the_model():
    a = allocdiff.anchor(ALLOC)
    rows = allocdiff.web_instructions(ALLOC, a.line)
    assert rows and all(t != c for t, c in rows)


def test_nocs_matches_the_quoted_formula():
    assert [allocdiff.nocs_for(n) for n in (1, 2, 5, 6, 10)] == [1, 2, 2, 3, 4]


def test_web_count_is_far_below_the_instruction_fault_count():
    """One mis-coloured web renames a value at every reference, so counting
    instructions ranks by blast radius instead of by progress."""
    from solver import signals
    many = _d([("move a3,a0", "move a2,a0")]
              + [(f"lw t{i},0x{0x10+4*i:x}(a3)", f"lw t{i},0x{0x10+4*i:x}(a2)")
                 for i in range(6)])
    sig = signals.analyse(many, 90.0)
    assert sig.regalloc >= 6
    assert len(allocdiff.mismatches(many)) < sig.regalloc
