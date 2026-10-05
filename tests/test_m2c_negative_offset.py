"""`m2c_negative_offset` must FIRE on the construct it owns, and decline with a reason on the rest.

The residuals in the firing tests are copied verbatim from the drafts on the size-bucketed frame
(`eval/results/intake-20260921/post-fix-residual.json`), which is where this construct was measured as the
largest identified blocker among states that still will not compile.

The declines matter as much as the fires, and the project's own history says why: a pass that returns
nothing looks exactly like a pass with nothing to do, so every decline here names its reason and the tests
assert the reason rather than just `changed is False`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import m2c_negative_offset as neg                            # noqa: E402


def test_fires_on_the_save_pull_residual():
    """`temp_v0->unk-4` -- alSavePull, the state whose whole residual was this one line."""
    src = ("s32 *f(void *filter) {\n"
           "    void *temp_v0;\n"
           "    temp_v0 = filter;\n"
           "    temp_v0->unk-4 = (s32) 7;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert "->unk-" not in out
    assert "(*( s32 *)((unsigned char *)temp_v0 - 0x4))" in out
    accepted = [c for c in changes if "after" in c]
    assert len(accepted) == 1 and accepted[0]["byte_offset"] == -4
    # The rest of the line must survive byte for byte.
    assert " = (s32) 7;" in out


def test_fires_on_a_multi_digit_hex_offset():
    """`var_v0_5->unk-604` and `var_s1_2->unk-1828` -- offsets large enough that the hex is not one digit."""
    src = ("void f(void) {\n"
           "    void *var_v0_5;\n"
           "    void *var_s1_2;\n"
           "    var_v0_5->unk-604 = 9;\n"
           "    var_s1_2->unk-1828 = 3;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert "->unk-" not in out
    assert "0x604" in out and "0x1828" in out
    assert len([c for c in changes if "after" in c]) == 2


def test_splices_right_to_left_so_repeats_do_not_mis_target():
    """Two identical uses on one line: naive `index()` splicing rewrites the first twice."""
    src = ("void f(void) {\n"
           "    void *a;\n"
           "    a->unk-4 = a->unk-4;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert out.count("(*( s32 *)((unsigned char *)a - 0x4))") == 2
    assert len([c for c in changes if "after" in c]) == 2


def test_declines_when_the_base_is_already_a_pointer_to_a_known_scalar():
    """A byte view through a typed pointer would defeat the compiler's own diagnostic; say so instead."""
    src = ("void f(void) {\n"
           "    u8 *var_v1;\n"
           "    var_v1->unk-4 = 1;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert out == src
    assert changes and "already a pointer" in changes[0]["declined"]


def test_declines_when_the_base_is_a_named_type():
    """`Vec3s *temp_t1;` -- the compiler can resolve members of a declared type; a byte view would not."""
    src = ("void f(void) {\n"
           "    Vec3s *temp_t1;\n"
           "    temp_t1->unk-2 = 1;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert out == src
    assert changes and "declared type" in changes[0]["declined"]


def test_declines_when_the_base_has_no_declaration_here():
    """m2c names parameters `arg0` and they are not declared in the body: no type, no byte view."""
    src = ("void f(void) {\n"
           "    arg0->unk-4 = 1;\n"
           "}\n")
    out, changes = neg.rewrite(src)
    assert out == src
    assert changes and "no declaration" in changes[0]["declined"]


def test_declines_and_does_not_touch_a_draft_with_no_negative_offset():
    """The common case. A rewrite that touched this would corrupt every draft in the tree."""
    src = ("void f(void) {\n"
           "    void *temp_v0;\n"
           "    temp_v0->unk4 = 1;\n"
           "    temp_v0->unk0 = 2;\n"
           "}\n")
    assert neg.rewrite(src) == (src, [])


def test_every_change_carries_a_line_number():
    """The report is read by a human deciding whether the rewrite is sound; an unlocated change is not."""
    src = ("void f(void) {\n"
           "    void *a;\n"
           "\n"
           "    a->unk-8 = 1;\n"
           "}\n")
    _out, changes = neg.rewrite(src)
    accepted = [c for c in changes if "after" in c]
    assert accepted and accepted[0]["line"] == 4
    assert accepted[0]["before"] == "a->unk-8"


def test_the_rewrite_is_idempotent():
    """Running it twice must not rewrite its own output: the `->unk` it leaves is `->unk4`, not `->unk-4`."""
    src = ("void f(void) {\n"
           "    void *a;\n"
           "    a->unk-4 = 1;\n"
           "}\n")
    once, _ = neg.rewrite(src)
    twice, changes = neg.rewrite(once)
    assert twice == once and changes == []
