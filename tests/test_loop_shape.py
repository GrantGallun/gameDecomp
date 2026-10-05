"""Statement trees (solver.c_stmt), m2c loop normalisation (solver.loop_shape) and the branch skeleton (solver.skeleton)."""
from solver import c_stmt as cs
from solver import loop_shape, skeleton

# Assembly-only m2c drafts, verbatim bodies (2026-09-30).
FIND = """s32 __MusIntFindChannel(s32 arg0, s32 arg1) {
    s32 var_v1;
    void *var_v0;

    var_v0 = mus_channels;
    var_v1 = 0;
    if (max_channels > 0) {
loop_1:
        if (var_v0->unk58 == 0) {
            return var_v1;
        }
        var_v1 += 1;
        var_v0 += 0x11C;
        if (var_v1 >= max_channels) {
            /* Duplicate return node #4. Try simplifying control flow for better match */
            return -1;
        }
        goto loop_1;
    }
    return -1;
}
"""
SUSPEND = """void suspendGameTask(s32 arg0) {
    void *var_v0;

    var_v0 = gActiveGameTaskList.unk4;
    if (var_v0 != NULL) {
loop_1:
        if (arg0 == var_v0->unk15) {
            var_v0->unk16 = 1;
            return;
        }
        var_v0 = var_v0->unk4;
        if (var_v0 == NULL) {

        } else {
            goto loop_1;
        }
    }
}
"""
PENDING = """s32 hasPendingRaceReplayCourseGridEntry(void) {
    s16 *var_v0;
    s16 temp_v1;

    var_v0 = *(&D_800DC490 + (gRaceCourseIndex * 4));
loop_1:
    temp_v1 = *var_v0;
    if (temp_v1 != -2) {
        if (temp_v1 != -1) {
            return 1;
        }
        var_v0 += 0x10;
        goto loop_1;
    }
    return 0;
}
"""


def _variants(src, fn):
    return dict(loop_shape.variants(src, fn))


def _flat(text):
    return " ".join(text.split())


def test_parse_render_round_trip_keeps_every_statement():
    from solver import rewrite_library
    b, e = rewrite_library._body(FIND, "__MusIntFindChannel")
    root = cs.parse_body(FIND, b, e)
    text = cs.render(root.body)
    for line in ("var_v0 = mus_channels;", "loop_1:", "goto loop_1;", "return -1;", "if (var_v0->unk58 == 0) {"):
        assert line in text


def test_goto_loop_with_duplicated_return_becomes_a_counted_for():
    v = _variants(FIND, "__MusIntFindChannel")
    assert all("goto" not in s for s in v.values())
    canon = next(s for k, s in v.items() if k.startswith("loops:canonical"))
    assert "do {" in canon and "} while (var_v1 < max_channels);" in canon and canon.count("return -1;") == 1
    assert any("for (var_v1 = 0; var_v1 < max_channels; var_v1 += 1, var_v0 += 0x11C) {" in s for s in v.values())
    assert any(_flat("var_v1 = 0; while (var_v1 < max_channels) {") in _flat(s) for s in v.values())


def test_empty_then_with_goto_else_becomes_a_while_and_a_for():
    v = _variants(SUSPEND, "suspendGameTask")
    assert any("} while (var_v0 != NULL);" in s for s in v.values())
    assert any("for (var_v0 = gActiveGameTaskList.unk4; var_v0 != NULL; var_v0 = var_v0->unk4) {" in s
               for s in v.values())


def test_label_at_top_becomes_break_loop_and_assignment_in_test():
    v = _variants(PENDING, "hasPendingRaceReplayCourseGridEntry")
    canon = next(s for k, s in v.items() if k.startswith("loops:canonical"))
    assert "while (1) {" in canon and _flat("if (temp_v1 == -2) { break; }") in _flat(canon)
    assert any("while ((temp_v1 = *var_v0) != -2) {" in s for s in v.values())


def test_goto_that_a_loop_would_capture_declines():
    src = """void f(s32 a) {
    while (a) {
loop_1:
        a -= 1;
        if (a == 3) { break; }
        goto loop_1;
    }
}
"""
    assert all("goto" in s for s in dict(loop_shape.variants(src, "f")).values()) or not loop_shape.variants(src, "f")


def test_negate_spells_the_opposite_comparison():
    assert cs.negate("var_v1 >= max_channels") == "var_v1 < max_channels"
    assert cs.negate("!x") == "x"
    assert cs.negate("a && b") == "!(a && b)"
    assert cs.negate("p->x") == "!p->x"


def test_skeleton_reads_branch_direction_from_hunk_offsets():
    # strchr's target (line 1 = offset 0): bnezl forward to 0x28, bne backward to 0x14
    diff = "\n".join(["--- t", "+++ c", "@@ -1,12 +1,12 @@",
                      " lbu    v0,0(a0)", " andi    a2,a1,0xff", " move    v1,a0", " beql    v0,a2,40",
                      " move    v0,a0", "-bnezl    v0,28", "+beqz    v0,28", " lbu    v0,1(v1)", " jr    ra",
                      " move    v0,zero", " lbu    v0,1(v1)", " addiu    v1,v1,1", " bne    v0,a2,14"])
    t, c = skeleton.skeletons(diff)
    assert t == ["beqlv", "bnezlv", "jr", "bne^"] and c == ["beqlv", "beqzv", "jr", "bne^"]
    assert skeleton.distance(diff) == 2
