"""stack_layout fires on its 2026-09-14 90+ census residuals and declines without sp-relative evidence."""
from solver import stack_layout as sl

OS_VI_BLACK = """void osViBlack(u8 arg0) {
    volatile u8 framePad[0x8];

    register u32 temp_s0;

    temp_s0 = __osDisableInt();
    if (arg0 != 0) {
        __osViNext->state |= 0x20;
    } else {
        __osViNext->state &= ~0x20;
    }
    __osRestoreInt(temp_s0);
}
"""
OS_VI_BLACK_DIFF = """--- target_object_dump_normalized.s
+++ candidate_object_dump_normalized.s
@@ -1,6 +1,6 @@
-addiu    sp,sp,-0x28
+addiu    sp,sp,-0x30
 sw    ra,0x14(sp)
-sw    a0,0x28(sp)
+sw    a0,0x30(sp)
 jal    __osDisableInt
 sw    s0,0x18(sp)
-lbu    t6,0x2b(sp)
+lbu    t6,0x33(sp)
@@ -24,3 +24,3 @@
-addiu    sp,sp,0x28
+addiu    sp,sp,0x30
 jr    ra
"""

BADGE = """void drawCourseSelectExtraCourseBadge(CourseSelectWidgetActor *arg0) {
    u16 sp34;
    s32 var_v0;
    u16 var_a3;

    if (gCourseSelectModeSelection == 1) {
        var_a3 = 1;
        var_v0 = 0x27;
    } else {
        var_a3 = 0;
        var_v0 = 0x21;
    }
    sp34 = var_a3;
    drawMenuSpriteWithAlpha(arg0->coordinates[0], arg0->coordinates[1], var_v0, var_a3, sp34);
}
"""
BADGE_DIFF = """@@ -17,1 +17,1 @@
-sh    a3,0x34(sp)
+sh    a3,0x36(sp)
@@ -27,1 +27,1 @@
-lhu    a3,0x34(sp)
+lhu    a3,0x36(sp)
"""

ROTATION = """void makeFixedRotationXZ(s16 *arg0, s16 arg1, s16 arg2) {
    volatile u8 framePad[0x38];

    s16 sp38;
    s16 sp18;

    makeFixedRotationX(&sp38, arg1);
    makeFixedRotationZ(&sp18, arg2);
    multiplyFixedMatrix3s(&sp38, &sp18, arg0);
}
"""
ROTATION_DIFF = """@@ -8,6 +8,6 @@
 jal    makeFixedRotationX
-addiu    a0,sp,0x38
+addiu    a0,sp,0x1e
 lh    a1,0x62(sp)
 jal    makeFixedRotationZ
-addiu    a0,sp,0x18
+addiu    a0,sp,0x1c
"""


def labels(source, function, diff):
    return {label: text for label, _kind, text in sl.variants(source, function, diff)}


def test_deltas_are_read_from_the_diff():
    assert sl.stack_deltas(OS_VI_BLACK_DIFF) == {"frame": 8, "slots": [8]}
    assert sl.stack_deltas(BADGE_DIFF) == {"frame": None, "slots": [2]}
    assert sl.stack_deltas(ROTATION_DIFF) == {"frame": None, "slots": [-26, 4]}


def test_fires_invented_frame_pad_drop_on_osViBlack():
    found = labels(OS_VI_BLACK, "osViBlack", OS_VI_BLACK_DIFF)
    dropped = found["stack_drop_unused:framePad"]
    assert "framePad" not in dropped and "register u32 temp_s0;" in dropped
    # `temp_s0` is used, so it is never dropped.
    assert not any(label.startswith("stack_drop_unused:temp_s0") for label in found)


def test_fires_aligned_pad_before_local_on_course_badge():
    found = labels(BADGE, "drawCourseSelectExtraCourseBadge", BADGE_DIFF)
    # The exact fix measured by the probe: an s16 pad before the third declaration.
    fixed = found["stack_insert_pad:s16 pad;@2"]
    assert fixed.index("s32 var_v0;") < fixed.index("s16 pad;") < fixed.index("u16 var_a3;")
    # Three declarations have five other orders; each is proposed once (swaps duplicating hoists are dropped).
    orders = [label for label in found if label.startswith(("stack_swap", "stack_hoist", "stack_sink", "stack_reverse"))]
    assert len(orders) == 5 and len({found[label] for label in orders}) == 5


def test_fires_named_layout_on_rotation_matrices():
    found = labels(ROTATION, "makeFixedRotationXZ", ROTATION_DIFF)
    layout = found["stack_named_layout"]
    assert "framePad" not in layout
    assert "s16 sp38[16];" in layout and "s16 sp18[16];" in layout
    # Arrays decay: same address, and the frontend policy rejects `&array` for a pointer parameter.
    assert "makeFixedRotationX(sp38, arg1);" in layout and "multiplyFixedMatrix3s(sp38, sp18, arg0);" in layout


def test_declines_without_stack_differences():
    register_only = "@@ -1,1 +1,1 @@\n-addu    v0,a0,a1\n+addu    v1,a0,a1\n"
    assert not list(sl.variants(OS_VI_BLACK, "osViBlack", register_only))
    assert not list(sl.variants(OS_VI_BLACK, "osViBlack", ""))


def test_never_reorders_or_drops_call_initializers():
    source = """void f(void) {
    s32 a = g();
    s32 b;

    use(&b);
}
"""
    diff = "@@ -1,1 +1,1 @@\n-addiu    a0,sp,0x1c\n+addiu    a0,sp,0x18\n"
    found = labels(source, "f", diff)
    assert found and not any(label.startswith(("stack_swap_decl", "stack_hoist_decl", "stack_sink_decl",
                                               "stack_reverse", "stack_drop_unused:a")) for label in found)


def test_signal_requires_few_faults_with_stack_shape():
    assert sl.signals({"faults": {"immediate": 2}, "first_difference": ["-addiu    sp,sp,-0x28"]})
    assert not sl.signals({"faults": {"register_allocation": 3}, "first_difference": ["-addu v0,a0,a1"]})
    assert not sl.signals({"faults": {"immediate": 50}})
