"""`m2c_or_address` must fire on the residuals, and leave everything else byte-identical.

The firing cases are taken verbatim from the drafts on the frozen frame. The declines matter as much: a
`|` in a bitmask (`flags & ~3 | 1`) is not an address, and casting it would be a fabrication -- so the
tests pin that the rewrite only touches a DEREFERENCE whose operand contains a top-level `|`.

Three regex attempts failed before this on real input and each failure was SILENT, which is why the
delineation is a balanced scan and why the tests include the two shapes that defeated the regexes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import m2c_or_address as oa                                   # noqa: E402


def test_fires_on_the_osEPiRawReadIo_residual():
    """Verbatim from the draft, with the `(s32)` cast inside the chain.

    The expected output casts `arg0->unkC` and `arg1` -- the pointer operands -- and leaves the `(s32)` cast
    alone, because it is already integer-valued."""
    src = "    *arg2 = *(arg0->unkC | arg1 | (s32) &D_A0000000);\n"
    out, changes = oa.rewrite(src)
    assert len(changes) == 1
    assert out == ("    *arg2 = *(s32 *)((u32)arg0->unkC | (u32)arg1 | (s32) &D_A0000000);\n")


def test_fires_on_the_write_form():
    """The store spelling, from `osEPiRawWriteIo`: `*(p | n) = v` at the start of a line."""
    src = "    *(arg0->unkC | arg1) = arg2;\n"
    out, changes = oa.rewrite(src)
    assert len(changes) == 1
    assert out == "    *(s32 *)((u32)arg0->unkC | (u32)arg1) = arg2;\n"


def test_survives_a_macro_guard_comment_with_parentheses():
    """THE SHAPE THAT DEFEATED THE FIRST REGEX. m2c writes a `/* Note: ... ( ... ) ... */` block near the
    top of many drafts, and a `[^()]*` pattern cannot cross it, so the rewrite silently matched nothing."""
    src = ("/*\nNote: pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD (unset) explicitly.\n*/\n"
           "void f(void) {\n    *p = *(a->b | c);\n}\n")
    out, changes = oa.rewrite(src)
    assert len(changes) == 1, "the comment's parentheses blocked the scan"
    assert "*(s32 *)((u32)a->b | (u32)c)" in out
    assert "Note: pass" in out, "the comment must survive byte for byte"


def test_leaves_a_bitmask_alone():
    """A `|` that is NOT a dereferenced address must not be touched. Casting it would be a fabrication."""
    src = ("void f(void) {\n    x = (flags & ~3) | 1;\n    y = a | b | c;\n"
           "    if (mode | 0x80) { z = 1; }\n}\n")
    out, changes = oa.rewrite(src)
    assert out == src and changes == []


def test_a_dereference_without_an_or_is_untouched():
    src = "void f(void) {\n    *p = *(q);\n    *r = *(s + 4);\n}\n"
    assert oa.rewrite(src) == (src, [])


def test_stops_at_an_unbalanced_dereference():
    """A half-written expression must not be rewritten into something that looks deliberate."""
    src = "void f(void) {\n    *p = *(a | b;\n}\n"
    assert oa.rewrite(src) == (src, [])


def test_keeps_a_grouped_operand_intact():
    """`(a | b)` inside a larger chain is ONE operand: the split has to be depth-aware or it tears it."""
    src = "void f(void) {\n    *p = *((a | b) | c);\n}\n"
    _out, changes = oa.rewrite(src)
    assert len(changes) == 1
    assert changes[0]["after"].count("(u32)") == 2, changes[0]


def test_casts_every_operand_so_the_or_is_well_typed():
    """The whole point: `pointer | int` has no operator, so both sides must become integers."""
    src = "void f(void) {\n    *p = *(ptr | 4);\n}\n"
    _out, changes = oa.rewrite(src)
    assert "(u32)ptr" in changes[0]["after"]
    assert "| 4" in changes[0]["after"], "a literal needs no cast"


def test_reports_a_line_number_for_every_change():
    src = "void f(void) {\n    *p = 1;\n\n    *p = *(a | b);\n}\n"
    _out, changes = oa.rewrite(src)
    assert changes and changes[0]["line"] == 4


def test_is_idempotent():
    """Running it twice must not wrap its own cast in another cast."""
    src = "void f(void) {\n    *p = *(a->b | c);\n}\n"
    once, _ = oa.rewrite(src)
    twice, changes = oa.rewrite(once)
    assert twice == once and changes == []


def test_a_clean_draft_costs_nothing():
    src = "s32 f(s32 a) {\n    return a + 1;\n}\n"
    assert oa.rewrite(src) == (src, [])
