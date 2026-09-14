"""typedecl must FIRE on the residual it was written for.

The motivating case is verbatim from nonmatchings/Fdrumsoff/base.c, whose only
defect is that PlayerCommandState is undeclared. Tests that only check
declining are the easy half; every one of the four silent-decline bugs in
CLAUDE.md passed that half.
"""

from solver import typedecl

FDRUMSOFF = '''#include "common.h"

s32 Fdrumsoff(PlayerCommandState *arg0, s32 arg1) {
    arg0->pdrums = NULL;
    return arg1;
}
'''

ALLINK = '''#include "common.h"

void alLink(ALLink *ln, ALLink *to) {
    ALLink *temp_v0;

    ln->prev = to;
    ln->next = to->next;
    temp_v0 = to->next;
    to->next = ln;
}
'''


def test_fires_on_fdrumsoff():
    layout = {"param0": [(112, 4, "s32")]}
    plans = typedecl.plan(FDRUMSOFF, "Fdrumsoff", layout, {"s32"})
    assert len(plans) == 1
    text = plans[0]["text"]
    assert "char pad00[0x70];" in text          # 112 == 0x70, kept as padding
    assert "s32 pdrums;" in text
    assert text.endswith("} PlayerCommandState;")


def test_applied_code_declares_before_use():
    out = typedecl.apply(FDRUMSOFF, typedecl.plan(
        FDRUMSOFF, "Fdrumsoff", {"param0": [(112, 4, "s32")]}, {"s32"}))
    assert out.index("PlayerCommandState;") < out.index("s32 Fdrumsoff(")
    assert '#include "common.h"' in out


def test_two_parameters_of_one_type_merge():
    """alLink takes ALLink twice; evidence for both params is one struct."""
    layout = {"param0": [(0, 4, "s32"), (4, 4, "s32")],
              "param1": [(0, 4, "s32")]}
    plans = typedecl.plan(ALLINK, "alLink", layout, set())
    assert len(plans) == 1
    assert plans[0]["params"] == [0, 1]
    assert plans[0]["offsets"] == [0, 4]
    assert "s32 prev;" in plans[0]["text"]      # first use order
    assert "s32 next;" in plans[0]["text"]


def test_gaps_become_padding_not_closed_up():
    layout = {"param0": [(0, 4, "s32"), (32, 4, "s32")]}
    code = 'void f(T *a) {\n    a->x = 0;\n    a->y = 1;\n}\n'
    text = typedecl.plan(code, "f", layout, set())[0]["text"]
    assert "char pad04[0x1c];" in text


def test_declines_when_more_members_than_offsets():
    """Unknown is the default: never invent a field to satisfy a name."""
    code = 'void f(T *a) {\n    a->x = a->y + a->z;\n}\n'
    assert typedecl.plan(code, "f", {"param0": [(0, 4, "s32")]}, set()) == []


def test_declines_without_evidence():
    assert typedecl.plan(FDRUMSOFF, "Fdrumsoff", {}, {"s32"}) == []


def test_declines_for_already_declared_type():
    assert typedecl.plan(FDRUMSOFF, "Fdrumsoff", {"param0": [(112, 4, "s32")]},
                         {"s32", "PlayerCommandState"}) == []


def test_declines_when_the_draft_declares_it_itself():
    code = 'typedef struct { int a; } T;\nvoid f(T *a) {\n    a->x = 0;\n}\n'
    assert typedecl.plan(code, "f", {"param0": [(0, 4, "s32")]}, set()) == []


def test_apply_is_identity_without_plans():
    assert typedecl.apply(FDRUMSOFF, []) == FDRUMSOFF


MUSASK = '''#include "common.h"

s32 MusAsk(s32 arg0) {
    PlayerCommandState *var_v1;
    s32 var_a1;

    var_v1 = getState(arg0);
    var_a1 = var_v1->unkC2;
    return var_a1;
}
'''


def test_fires_on_an_undeclared_local_type():
    """66 of 143 leaves stopped here: typedecl only read the parameter list."""
    assert typedecl.pointer_locals(MUSASK, "MusAsk") == [
        ("PlayerCommandState", "var_v1")]


def test_a_return_statement_is_not_a_declaration():
    """`return var_a1;` parses as type `return`, name `var_a1`. A throwaway
    scan reported `return` as an undeclared type on six functions."""
    code = 'void f(void) {\n    s32 x;\n    return x;\n}\n'
    assert typedecl.pointer_locals(code, "f") == []
    assert "return" in typedecl.C_KEYWORDS


def test_local_only_types_are_declared_from_the_pool():
    """A local has no evidence key -- the tier keys on param0/param1 -- so the
    layout must come from the cross-function pool."""
    pool = {"PlayerCommandState": [(0xC2, 2, "s16"), (0xC4, 2, "s16")]}
    plans = typedecl.plan(MUSASK, "MusAsk", {}, {"s32"}, pool)
    assert len(plans) == 1
    assert plans[0]["source"] == "pooled"
    assert "s16 unkC2;" in plans[0]["text"]


def test_local_declines_without_a_pool_entry():
    assert typedecl.plan(MUSASK, "MusAsk", {}, {"s32"}, {}) == []


def test_build_type_locals_produce_no_plan():
    """pointer_locals is a raw scanner. `u8` is a BUILD type, not a C keyword,
    so it is filtered in plan(), which is where known_types is available."""
    code = 'void f(void) {\n    u8 *p;\n    p = 0;\n}\n'
    assert typedecl.pointer_locals(code, "f") == [("u8", "p")]
    assert typedecl.plan(code, "f", {}, {"u8"}, {"u8": [(0, 1, "u8")]}) == []


def test_keyword_pointer_locals_are_ignored_by_the_scanner():
    code = 'void f(void) {\n    void *p;\n    p = 0;\n}\n'
    assert typedecl.pointer_locals(code, "f") == []
