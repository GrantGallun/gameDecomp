"""memberaccess must FIRE on Fcutoff, the residual it was written for."""

from solver import memberaccess, typedecl

FCUTOFF = '''#include "common.h"

s32 Fcutoff(PlayerCommandState *arg0, u8 *arg1) {
    arg0->unkC2 = (arg1->unk0 << 8) | arg1->unk1;
    arg0->unkC4 = 0;
    return (s32) (arg1 + 2);
}
'''


def test_fires_on_fcutoff():
    out, plans = memberaccess.rewrite(FCUTOFF, "Fcutoff")
    assert len(plans) == 1 and plans[0]["variable"] == "arg1"
    assert "arg1[0]" in out and "arg1[1]" in out
    assert "arg1->" not in out


def test_pointer_arithmetic_is_left_alone():
    """`arg1 + 2` walks two BYTES; a synthesised struct would rescale it."""
    out, _ = memberaccess.rewrite(FCUTOFF, "Fcutoff")
    assert "return (s32) (arg1 + 2);" in out


def test_the_struct_parameter_is_untouched():
    """arg0 is a real struct pointer; only the byte pointer is rewritten."""
    out, _ = memberaccess.rewrite(FCUTOFF, "Fcutoff")
    assert "arg0->unkC2" in out and "arg0->unkC4" in out


def test_hex_offsets_decode():
    code = ('void f(u8 *p) {\n    p->unkC2 = p->unk0;\n}\n')
    out, plans = memberaccess.rewrite(code, "f")
    assert plans[0]["mapping"] == {"unkC2": 0xC2, "unk0": 0}
    assert "p[194]" in out and "p[0]" in out


def test_declines_on_a_real_member_name():
    """A name that is not an offset cannot be turned into an index."""
    code = 'void f(u8 *p) {\n    p->finePitch = 0;\n}\n'
    assert memberaccess.plan(code, "f") == []


def test_declines_on_wider_element_types():
    """offset == index only holds for one-byte elements; anything else would
    be a wrong constant rather than a compile error."""
    code = 'void f(s32 *p) {\n    p->unk4 = 0;\n}\n'
    assert memberaccess.plan(code, "f") == []


def test_declines_when_there_are_no_member_accesses():
    code = 'void f(u8 *p) {\n    *p = 0;\n}\n'
    assert memberaccess.plan(code, "f") == []


def test_apply_is_identity_without_plans():
    assert memberaccess.apply(FCUTOFF, []) == FCUTOFF


def test_void_is_never_treated_as_an_undeclared_struct():
    """`void` is a keyword, so buildtypes never lists it; typedecl emitted
    `typedef struct { ... } void;` for 8 of 35 plans before this."""
    code = 'void guMtxF2L(void *arg0) {\n    arg0->unk0 = 1;\n}\n'
    assert typedecl.plan(code, "guMtxF2L",
                         {"param0": [(0, 4, "s32")]}, set()) == []
