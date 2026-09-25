"""solver.branch_shape: fires on its motivating residuals, declines without the residual signature."""
from solver import branch_shape

# Diffs of target (-) against candidate (+): only the balance of opcodes matters to the gates.
MORE_B = "--- t\n+++ c\n@@ -1,3 +1,2 @@\n-b    3e4\n li    s3,0x60\n"
MORE_SLT = "--- t\n+++ c\n@@ -1,3 +1,3 @@\n-slti    at,s1,0x40\n-bnez    at,a4\n+bne    s0,s3,a8\n"
NONE = "--- t\n+++ c\n@@ -1,2 +1,2 @@\n-lw    t6,0(a0)\n+lw    t7,0(a0)\n"

# drawTrainingCourseLessonEndMenu best node (restart round 3), reduced to the select and the split loop webs.
DRAW = """void drawTrainingCourseLessonEndMenu(struct A *arg0) {
    s32 var_s0_3;
    s32 var_s1;
    s32 var_s1_2;
    s32 var_s3;

    var_s1 = 0;
    do {
        draw(var_s1);
        var_s1 += 0x10;
    } while (var_s1 < 0x40);
    var_s1_2 = 0;
    do {
        draw(var_s1_2);
        var_s1_2 += 0x10;
    } while (var_s1_2 < 0x30);
    var_s0_3 = 0;
    do {
        var_s3 = 0x60;
        if (var_s0_3 == arg0->unk24) {
            var_s3 = 0x100;
        }
        draw(var_s3);
        var_s0_3 += 1;
    } while (var_s0_3 != 3);
}
"""

AI = """extern s32 AI_STATUS_REG;

s32 __osAiDeviceBusy(void) {
    if (AI_STATUS_REG & 0x80000000) {
        return 1;
    }
    return 0;
}
"""

CART = """extern OSPiHandle CartRomHandle;

OSPiHandle *osCartRomInit(void) {
    u32 sp1C;

    sp1C = 0;
    if (CartRomHandle.baseAddress == 0xB0000000) {

    } else {
        CartRomHandle.type = 0;
        osPiRawReadIo(0, &sp1C);
    }
    return &CartRomHandle;
}
"""

O1 = {"settings": {"C_OPT": "-O1"}}
O2 = {"settings": {"C_OPT": "-O2"}}
MORE_B_FRAME = MORE_B + "-addiu    sp,sp,-0x8\n-addiu    sp,sp,8\n"


def test_select_else_fires_on_draw_training_select():
    out = list(branch_shape.select_else(DRAW, "drawTrainingCourseLessonEndMenu", MORE_B))
    assert len(out) == 1
    label, cand = out[0]
    assert label.startswith("select_else:var_s3")
    assert ("        if (var_s0_3 == arg0->unk24) {\n            var_s3 = 0x100;\n        } else {\n"
            "            var_s3 = 0x60;\n        }\n") in cand


def test_select_else_declines_without_a_missing_branch_or_when_the_condition_reads_the_variable():
    assert not list(branch_shape.select_else(DRAW, "drawTrainingCourseLessonEndMenu", NONE))
    reads = DRAW.replace("if (var_s0_3 == arg0->unk24)", "if (var_s3 == arg0->unk24)")
    assert not list(branch_shape.select_else(reads, "drawTrainingCourseLessonEndMenu", MORE_B))


def test_split_merge_fires_on_kept_slt_and_merges_the_register_group():
    out = dict(branch_shape.split_merge(DRAW, "drawTrainingCourseLessonEndMenu", MORE_SLT))
    cand = out["split_merge:s1:all"]
    assert "var_s1_2" not in cand and cand.count("s32 var_s1;") == 1
    assert "} while (var_s1 < 0x30);" in cand
    assert not list(branch_shape.split_merge(DRAW, "drawTrainingCourseLessonEndMenu", MORE_B))


def test_o1_register_local_fires_on_os_ai_device_busy_only_at_o1():
    out = list(branch_shape.o1_register_local(AI, "__osAiDeviceBusy", MORE_B_FRAME, O1))
    assert [label for label, _ in out] == ["o1_register_local:AI_STATUS_REG"]
    assert "    register s32 reg_value = AI_STATUS_REG;\n    if (reg_value & 0x80000000) {" in out[0][1]
    assert not list(branch_shape.o1_register_local(AI, "__osAiDeviceBusy", MORE_B_FRAME, O2))
    assert not list(branch_shape.o1_register_local(AI, "__osAiDeviceBusy", NONE, O1))


def test_o1_register_local_skips_aggregate_reads():
    assert not list(branch_shape.o1_register_local(CART, "osCartRomInit", MORE_B_FRAME, O1))


def test_empty_then_return_fires_on_cart_rom_init():
    out = list(branch_shape.empty_then_return(CART, "osCartRomInit", MORE_B))
    assert len(out) == 1
    assert ("    if (CartRomHandle.baseAddress == 0xB0000000) {\n        return &CartRomHandle;\n    } else {\n"
            in out[0][1])
    assert not list(branch_shape.empty_then_return(CART, "osCartRomInit", NONE))


def test_variants_reads_the_recipe_from_the_verdict():
    kinds = {k for _l, k, _c in branch_shape.variants(AI, "__osAiDeviceBusy", MORE_B_FRAME,
                                                       {"compiler_recipe": O1})}
    assert kinds == {"o1_register_local"}


def test_search_stream_includes_branch_shape_families():
    from solver import regalloc_mutations
    kinds = {kind for _label, kind, _c in regalloc_mutations.variants(DRAW, "drawTrainingCourseLessonEndMenu",
                                                                      MORE_B + MORE_SLT[len("--- t\n+++ c\n"):])}
    assert {"select_else", "split_merge"} <= kinds


# copyGfxCommandBlockToScratch best node (restart round 3): m2c's pseudo-call left as a call.
COPY = """s32 allocMenuRenderScratch( s32);                      /* extern */

s32 copyGfxCommandBlockToScratch(s32 arg0) {
    s32 temp_v0;

    temp_v0 = allocMenuRenderScratch(0x40);
    if (temp_v0 == 0) {
        return 0;
    }
    M2C_MEMCPY_ALIGNED(temp_v0, arg0, 0x40);
    return temp_v0;
}
"""


def test_m2c_struct_copy_fires_on_copy_gfx_command_block():
    out = list(branch_shape.m2c_struct_copy(COPY, "copyGfxCommandBlockToScratch"))
    assert [label for label, _ in out] == ["m2c_struct_copy:0x40"]
    cand = out[0][1]
    assert "typedef struct { s32 w[16]; } M2cCopy40;\n\ns32 copyGfxCommandBlockToScratch(s32 arg0) {" in cand
    assert "    *(M2cCopy40 *)(temp_v0) = *(M2cCopy40 *)(arg0);" in cand
    assert "M2C_MEMCPY_ALIGNED" not in cand
    assert not list(branch_shape.m2c_struct_copy(COPY.replace("0x40);", "0x42);"), "copyGfxCommandBlockToScratch"))


# __MusIntFindChannel best node (restart round 3).
MUS = """s32 __MusIntFindChannel(s32 arg0, s32 arg1) {
    PlayerCommandState *var_v0;
    s32 var_v1;

    var_v0 = mus_channels;
    var_v1 = 0;
    if (max_channels > 0) {
loop_1:
        if (var_v0->pdata == NULL) {
            return var_v1;
        }
        var_v1 += 1;
var_v0 = (PlayerCommandState *)((unsigned char *)var_v0 + 0x11C);
        if (var_v1 >= max_channels) {
            /* Duplicate return node #4. Try simplifying control flow for better match */
            return -1;
        }
        goto loop_1;
    }
    return -1;
}
"""


def test_dup_return_merge_fires_on_mus_int_find_channel():
    out = list(branch_shape.dup_return_merge(MUS, "__MusIntFindChannel"))
    assert len(out) == 1
    cand = out[0][1]
    assert "        if (!(var_v1 >= max_channels)) {\n            goto loop_1;\n        }\n    }\n    return -1;\n" in cand
    assert "Duplicate return node" not in cand


def test_dup_return_merge_declines_when_the_surviving_return_differs():
    other = MUS.replace("    }\n    return -1;\n}\n", "    }\n    return -2;\n}\n")
    assert not list(branch_shape.dup_return_merge(other, "__MusIntFindChannel"))


def test_select_else_offers_all_sites_first_and_split_merge_prefers_the_target_slt_register():
    two = DRAW.replace("        draw(var_s3);\n", "        draw(var_s3);\n        var_s1 = 0x60;\n"
                       "        if (var_s0_3 == 2) {\n            var_s1 = 0x100;\n        }\n")
    labels = [label for label, _ in branch_shape.select_else(two, "drawTrainingCourseLessonEndMenu", MORE_B)]
    assert labels[0] == "select_else:all" and len(labels) == 3
    both = DRAW.replace("    s32 var_s3;\n", "    s32 var_s3;\n    s32 var_s0;\n    s32 var_s0_2;\n")
    first = next(branch_shape.split_merge(both, "drawTrainingCourseLessonEndMenu", MORE_SLT))[0]
    assert first == "split_merge:s1:all"


# MusStop best node (round 4), reduced: m2c's var_at, assigned on two paths, one right after a label.
MUSSTOP = """void MusStop(s32 arg0, s32 arg1) {
    PlayerCommandState *var_a0;
    s32 var_at;
    s32 var_v1;

    var_v1 = 0;
    var_a0 = mus_channels;
    if (max_channels > 0) {
        do {
            var_v1 += 1;
            if (var_a0->soundId == 0) {
                var_at = var_v1 < max_channels;
                if (arg0 & 2) {
                    goto block_8;
                }
            } else {
block_8:
                var_a0->fadeTarget = arg1;
block_9:
                var_at = var_v1 < max_channels;
            }
var_a0 = (PlayerCommandState *)((unsigned char *)var_a0 + 0x11C);
        } while (var_at != 0);
    }
}
"""


def test_at_inline_fires_on_mus_stop_and_keeps_labels_valid():
    out = list(branch_shape.at_inline(MUSSTOP, "MusStop"))
    assert [label for label, _ in out] == ["at_inline:var_at"]
    cand = out[0][1]
    assert "var_at" not in cand
    assert "        } while ((var_v1 < max_channels));\n" in cand
    assert "block_9:;\n            }\n" in cand


def test_at_inline_accepts_a_constant_propagated_path_and_declines_unrelated_forms():
    clamp = """void f(Actor *arg0, s16 arg1) {
    s16 var_v0;
    s32 var_at;

    var_v0 = arg1 - arg0->timer;
    var_at = var_v0 < -4;
    if (var_v0 >= 5) {
        var_v0 = 4;
        var_at = 4 < -4;
    }
    if (var_at != 0) {
        arg0->timer = 0;
    }
}
"""
    out = list(branch_shape.at_inline(clamp, "f"))
    assert out and "    if ((var_v0 < -4)) {\n" in out[0][1] and "var_at" not in out[0][1]
    other = clamp.replace("var_at = 4 < -4;", "var_at = arg1 < 9;")
    assert not list(branch_shape.at_inline(other, "f"))


# __osSetGlobalIntMask best node (restart round 3, 75.833) and its diff's gate lines: the target saves s0 and keeps
# the __osDisableInt result there; the candidate stores it to the stack.
SET_MASK = """extern s32 __OSGlobalIntMask;

void __osSetGlobalIntMask(u32 mask) {
    volatile u8 framePad[0x8];

    u32 temp_s0;

    temp_s0 = __osDisableInt();
    __OSGlobalIntMask |= mask;
    __osRestoreInt(temp_s0);
}
"""
SAVES_S0 = ("--- t\n+++ c\n@@ -1,6 +1,5 @@\n addiu    sp,sp,-0x28\n-sw    ra,0x1c(sp)\n+sw    ra,0x14(sp)\n"
            " jal    __osDisableInt\n-sw    s0,0x18(sp)\n+sw    a0,0x28(sp)\n-move    s0,v0\n+sw    v0,0x1c(sp)\n")


def test_o1_register_saved_fires_on_set_global_int_mask():
    out = dict(branch_shape.o1_register_saved(SET_MASK, "__osSetGlobalIntMask", SAVES_S0, O1))
    assert list(out) == ["o1_register_saved:temp_s0", "o1_register_saved:temp_s0:nopad"]
    assert "    register u32 temp_s0;\n" in out["o1_register_saved:temp_s0"]
    assert "framePad" in out["o1_register_saved:temp_s0"]
    assert "framePad" not in out["o1_register_saved:temp_s0:nopad"]
    assert "void __osSetGlobalIntMask(u32 mask)" in out["o1_register_saved:temp_s0"]  # parameters untouched (P6b)


def test_o1_register_saved_declines_at_o2_and_without_a_missing_saved_register():
    # H7: `register` is inert at -O2; and no target-only `sw sN` means no saved register to recover.
    assert not list(branch_shape.o1_register_saved(SET_MASK, "__osSetGlobalIntMask", SAVES_S0, O2))
    assert not list(branch_shape.o1_register_saved(SET_MASK, "__osSetGlobalIntMask", NONE, O1))
    both = SAVES_S0 + "+sw    s0,0x10(sp)\n"
    assert not list(branch_shape.o1_register_saved(SET_MASK, "__osSetGlobalIntMask", both, O1))


def test_o1_register_saved_prefers_locals_named_after_the_wanted_register():
    src = SET_MASK.replace("    u32 temp_s0;\n", "    s32 sp1C;\n    u32 temp_s0;\n")
    labels = [label for label, _ in branch_shape.o1_register_saved(src, "__osSetGlobalIntMask", SAVES_S0, O1)]
    assert labels[0] == "o1_register_saved:temp_s0" and "o1_register_saved:sp1C" in labels
