"""`p->unkN` on a scalar base becomes `p[N / sizeof(T)]`, and declines everywhere it does not own.

THE RESIDUAL THIS IS WRITTEN FOR. After the error-limit fix
(`eval/results/intake-20260921/ERROR-LIMIT.md`), `member-on-typed-pointer` is the ONLY remaining class
in 11 states of the frozen 200-state frame and appears in 74. Every fixture below is a line recovered
from one of those 11 final candidates, with the checker's own message beside it:

    drawMenuAsciiTextDefaultScale   var_a2 = var_s1->unk1;
                                    base type 'u8' (aka 'unsigned char')
    Fenvelope                       var_v0 = arg1->unk0;
    selectMenuRenderScratchBuffer   getRelocatableHeapBlockBase(gAssetHandles.unk2)
                                    base type 'AssetHandles' (aka 'short[64]')
    getRaceCourseTargetPositionAhead  (gRaceCourseSurfaces + sp34)->unk10   base type 's32'
                                      (gRaceCourseSurfaces + sp34)->unk12   -- 0x12 % 4 != 0, DECLINED
    multiplyFixedMatrix3s           var_a0->unk-2  -- negative, owned elsewhere

THE FIFTH RULE. Most of these assert it FIRES. The declines are here too, but they are the easy half.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import scalar_member_index as smi                                 # noqa: E402


def _diagnostic(line: int, column: int, declared: str, aka: str | None = None) -> str:
    spelled = f"'{declared}'" + (f" (aka '{aka}')" if aka else "")
    return (f"candidate.c:{line}:{column}: error: member reference base type {spelled} "
            f"is not a structure or union\n")


def test_it_fires_on_a_u8_pointer_access():
    """THE MOTIVATING CASE: drawMenuAsciiTextDefaultScale, its single remaining error."""
    source = "void f(u8 *var_s1) {\n    var_a2 = var_s1->unk1;\n}\n"
    column = source.splitlines()[1].index("->") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "u8", "unsigned char"))
    assert "var_a2 = var_s1[1];" in out, out
    applied = [c for c in changes if "after" in c]
    assert len(applied) == 1
    assert (applied[0]["offset"], applied[0]["width"], applied[0]["index"]) == (1, 1, 1)


def test_a_typedefd_array_is_resolved_through_the_aka():
    """`AssetHandles` names nothing; `short[64]` names the element width."""
    source = "void f(void) {\n    x = base(gAssetHandles.unk2);\n}\n"
    column = source.splitlines()[1].index(".unk2") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "AssetHandles", "short[64]"))
    assert "base(gAssetHandles[1])" in out, out
    assert [c["index"] for c in changes if "after" in c] == [1]


def test_a_hex_offset_on_a_word_base_divides_by_four():
    """`(gRaceCourseSurfaces + sp34)->unk10` -- the base is an EXPRESSION, so the site must come from
    the diagnostic's column rather than from parsing an identifier."""
    source = "void f(void) {\n    t = (gRaceCourseSurfaces + sp34)->unk10;\n}\n"
    column = source.splitlines()[1].index("->unk10") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "s32", "long"))
    assert "(gRaceCourseSurfaces + sp34)[4];" in out, out
    assert [c["index"] for c in changes if "after" in c] == [4]


def test_several_sites_on_one_line_are_all_rewritten():
    """Two diagnostics, two columns, and the right-to-left application must not shift either."""
    source = "void f(u8 *p) {\n    y = p->unk0 + p->unk4;\n}\n"
    line = source.splitlines()[1]
    first, second = line.index("->unk0") + 1, line.index("->unk4") + 1
    out, _ = smi.rewrite(source, _diagnostic(2, first, "u8") + _diagnostic(2, second, "u8"))
    assert "y = p[0] + p[4];" in out, out


def test_an_offset_that_is_not_a_multiple_of_the_width_is_declined_with_its_arithmetic():
    """`->unk12` on `s32 *`: 0x12 is 18 and 18 % 4 is 2. Choosing an index would choose a layout."""
    source = "void f(void) {\n    t = (gRaceCourseSurfaces + sp34)->unk12;\n}\n"
    column = source.splitlines()[1].index("->unk12") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "s32"))
    assert out == source, "the candidate is returned unmodified"
    declined = [c for c in changes if "declined" in c]
    assert len(declined) == 1
    assert "not a multiple" in declined[0]["declined"]
    assert declined[0]["offset"] == 0x12 and declined[0]["width"] == 4


def test_a_void_base_is_left_to_its_owner():
    source = "void f(void *p) {\n    y = p->unk4;\n}\n"
    column = source.splitlines()[1].index("->") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "void"))
    assert out == source
    assert "void_field_repair" in [c.get("declined") for c in changes][0]


def test_a_negative_offset_is_left_to_its_owner():
    """`multiplyFixedMatrix3s` carries `var_a0->unk-2`; two modules already own that spelling."""
    source = "void f(s16 *var_a0) {\n    var_a0->unk-2 = 0;\n}\n"
    column = source.splitlines()[1].index("->unk-2") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "s16", "short"))
    assert out == source
    assert "m2c_negative_offset" in [c.get("declined") for c in changes][0]


def test_an_unknown_base_type_abstains_rather_than_guessing_a_width():
    source = "void f(Actor *p) {\n    y = p->unk4;\n}\n"
    column = source.splitlines()[1].index("->") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "Actor"))
    assert out == source
    assert "no width is known" in [c.get("declined") for c in changes][0]


def test_a_column_that_is_not_a_member_access_is_never_edited():
    """A diagnostic that does not land on `unkN` must not move a byte."""
    source = "void f(u8 *p) {\n    y = p->field;\n}\n"
    column = source.splitlines()[1].index("->") + 1
    out, changes = smi.rewrite(source, _diagnostic(2, column, "u8"))
    assert out == source
    assert "not an `unkN` member access" in [c.get("declined") for c in changes][0]


def test_no_diagnostic_is_a_clean_no_op():
    source = "void f(u8 *p) {\n    y = p->unk1;\n}\n"
    assert smi.rewrite(source, "") == (source, [])
    assert smi.rewrite(source, "candidate.c:1:1: error: something else entirely\n") == (source, [])


def test_width_resolution_covers_the_spellings_the_checker_actually_emits():
    assert smi.width_of("u8") == 1 and smi.width_of("unsigned char") == 1
    assert smi.width_of("short[64]") == 2 and smi.width_of("s16") == 2
    assert smi.width_of("long") == 4 and smi.width_of("f32") == 4
    assert smi.width_of("Actor") is None and smi.width_of("struct S[4]") is None


def test_the_action_is_registered_and_names_its_missing_inputs():
    from eval import intake_runners
    from eval.tool_runners import NOT_APPLICABLE

    registry: dict = {}
    assert "eval.intake_runners.scalar_member_index" in intake_runners.register(registry)

    result = intake_runners.scalar_member_index({"candidate": "void f(void){}\n"}, {})
    assert result["status"] == NOT_APPLICABLE
    assert "repo" in result["reason"]

    without_target = intake_runners.scalar_member_index(
        {"candidate": "void f(void){}\n", "repo": "."}, {})
    assert without_target["status"] == NOT_APPLICABLE
    assert "target" in without_target["reason"], without_target["reason"]
