"""Mutation generators fire on the shapes their signatures point to."""
from solver import regalloc_mutations as rm

SOURCE = """#include "common.h"
typedef struct { s16 x; } Vec;
void f(Vec *out, s32 a, s32 b) {
    s32 c;
    s32 d;
    c = a * b;
    if (a + 1) {
        out->x = (s16)(c * (b + a) >> 12);
    }
    d = (a | b) + (u8)c;
}
"""


def texts(generator):
    return [variant for _label, _kind, variant in generator]


def test_commutative_swaps_cover_every_operator_and_group_the_moved_operand():
    variants = texts(rm.commutative_swaps(SOURCE, "f"))
    assert any("c = b * a;" in v for v in variants)
    assert any("if (1 + a)" in v for v in variants)
    assert any("(s16)((b + a) * c >> 12)" in v for v in variants)
    assert any("(s16)(c * (a + b) >> 12)" in v for v in variants)
    assert any("d = (u8)c + (a | b);" in v for v in variants)
    assert any("(b | a)" in v for v in variants)
    # Declarations `Vec *out` are not products.
    assert not any("out * Vec" in v for v in variants)


def test_left_assoc_operand_is_grouped_when_moved_right():
    source = "void g(s32 a, s32 b, s32 c) {\n    a = a + b + c;\n}\n"
    variants = texts(rm.commutative_swaps(source, "g"))
    assert "    a = c + (a + b);\n" in "".join(variants)
    assert "    a = b + a + c;\n" in "".join(variants)


def test_declaration_swaps_only_adjacent_plain_declarations():
    variants = texts(rm.declaration_swaps(SOURCE, "f"))
    assert len(variants) == 1 and "s32 d;\n    s32 c;" in variants[0]


def test_local_types_fire_on_the_makeFixedRotationXY_s16_local():
    source = ("void makeFixedRotationXY(Mat3x3 arg0, s16 arg1, s16 arg2) {\n    s32 sp2C;\n    s16 temp_v0;\n"
              "    Vec *p;\n    temp_v0 = fixedCosine(arg2);\n    arg0[5] = (s16) ((s32) (sp2C * temp_v0) / 4096);\n}\n")
    variants = texts(rm.local_types(source, "makeFixedRotationXY"))
    assert any("    s32 temp_v0;" in v for v in variants)       # the change that matched on 2026-09-13
    assert len(variants) == 10                                # 5 alternatives for each of 2 integer locals
    assert not any("Vec" not in v for v in variants)


B49C = """void func_8005B49C(struct RaceUiCounterActor *arg0) {
    int tmp;
    (*(s16 *)((u8 *)(arg0) + 0x1A)) = (s16) ((*(s16 *)((u8 *)(arg0) + 0x1A)) - 1);
    tmp = 2;
    (*(s16 *)((u8 *)(arg0) + 0x1C)) = (s16)tmp;

    if ((*(s16 *)((u8 *)(arg0) + 0x1A)) == 0) {
        enqueueSoundEffect(0x1A, 0x32);
    }
}
"""


def test_constant_inline_then_statement_move_reach_the_func_8005B49C_match():
    inlined = texts(rm.constant_local_inlines(B49C, "func_8005B49C"))
    assert len(inlined) == 1
    assert "int tmp" not in inlined[0] and "(s16)2;" in inlined[0]
    moved = texts(rm.statement_moves(inlined[0], "func_8005B49C"))
    store_first = ("    (*(s16 *)((u8 *)(arg0) + 0x1C)) = (s16)2;\n"
                   "    (*(s16 *)((u8 *)(arg0) + 0x1A)) = (s16) ((*(s16 *)((u8 *)(arg0) + 0x1A)) - 1);\n")
    assert any(store_first in v for v in moved)               # the two-step change that matched
    # Moves never cross a blank line, a control statement or a block.
    assert not any("enqueueSoundEffect(0x1A, 0x32);\n    (*(s16" in v for v in moved)


def test_constant_inline_declines_locals_written_twice():
    source = "void g(void) {\n    int t;\n    t = 1;\n    t += 2;\n    use(t);\n}\n"
    assert texts(rm.constant_local_inlines(source, "g")) == []


SLASH = """void updateEndingSlashSlideToCenter(EndingCreditsSlash *arg0) {
    s32 var_a1;

    loopMainMenuSceneModelAnimation(0);
    var_a1 = arg0->unk18 + 0x24000;
    arg0->unk18 = var_a1;
    if (var_a1 >= 0x500000) {
        arg0->unk18 = 0x500000;
        setCallbackTaskCallback(arg0, (void (*)(void *)) updateEndingSlashSlowRotationWipe);
        var_a1 = arg0->unk18;
    }
    setMainMenuSceneModelPosition(0, var_a1, arg0->unk1C, arg0->unk20);
    addMainMenuSceneModelDrawCallback(0);
}
"""

SLASH_MATCHED = """void updateEndingSlashSlideToCenter(EndingCreditsSlash *arg0) {

    loopMainMenuSceneModelAnimation(0);
    arg0->unk18 += 0x24000;
    if (arg0->unk18 >= 0x500000) {
        arg0->unk18 = 0x500000;
        setCallbackTaskCallback(arg0, (void (*)(void *)) updateEndingSlashSlowRotationWipe);
    }
    setMainMenuSceneModelPosition(0, arg0->unk18, arg0->unk1C, arg0->unk20);
    addMainMenuSceneModelDrawCallback(0);
}
"""


def test_field_local_elimination_fires_on_the_ending_credits_cluster():
    variants = texts(rm.field_local_eliminations(SLASH, "updateEndingSlashSlideToCenter"))
    assert SLASH_MATCHED in variants          # the shape that compiled object-exact on 2026-09-13
    # compound and plain, each with and without the (now unused) declaration kept;
    # unit-step spellings need a step of 1.
    assert len(variants) == 4


def test_field_local_elimination_declines_other_writes_and_two_fields():
    other = SLASH.replace("        var_a1 = arg0->unk18;\n", "        var_a1 = 3;\n")
    assert texts(rm.field_local_eliminations(other, "updateEndingSlashSlideToCenter")) == []
    two = SLASH.replace("        var_a1 = arg0->unk18;\n", "        var_a1 = arg0->unk1C + 1;\n        arg0->unk1C = var_a1;\n")
    assert texts(rm.field_local_eliminations(two, "updateEndingSlashSlideToCenter")) == []


SPARKLE = """void updateRaceUiSparkle(RaceItemEffectActor *arg0) {
    u16 temp_t7;

    temp_t7 = arg0->unk1C + 1;
    arg0->unk1C = temp_t7;
    if ((temp_t7 & 0xFFFF) >= 0x10) {
        removeCallbackTask(arg0);
        return;
    }
}
"""

RANDOM = """u8 randomNextObject(RandomStateObject *arg0) {
    u8 temp_inc;
    u8 temp_idx;

    temp_inc = (*(u8 *)((u8 *)(arg0) + 0x518)) + 1;
    (*(u8 *)((u8 *)(arg0) + 0x518)) = temp_inc;
    temp_idx = temp_inc & 0xFF;
    return *(&gRandomTable + temp_idx);
}
"""


def test_unit_step_spellings_reach_the_matched_sparkle_and_random_sources():
    sparkle = texts(rm.field_local_eliminations(SPARKLE, "updateRaceUiSparkle"))
    assert any("    if (++arg0->unk1C >= 0x10) {" in v and "temp_t7" not in v for v in sparkle)
    postfix = [v for label, _k, v in rm.field_local_eliminations(RANDOM, "randomNextObject") if label.endswith(":postfix")]
    assert len(postfix) == 1 and "(*(u8 *)((u8 *)(arg0) + 0x518))++;" in postfix[0]
    inlined = texts(rm.single_use_local_inlines(postfix[0], "randomNextObject"))
    assert any("return *(&gRandomTable + ((*(u8 *)((u8 *)(arg0) + 0x518))));" in v for v in inlined)


def test_single_use_inline_declines_calls_and_multiple_reads():
    source = "void g(void) {\n    int t;\n    t = f();\n    use(t);\n}\n"
    assert texts(rm.single_use_local_inlines(source, "g")) == []
    twice = "void g(s32 a) {\n    int t;\n    t = a & 0xFF;\n    use(t, t);\n}\n"
    assert texts(rm.single_use_local_inlines(twice, "g")) == []


def test_variants_are_unique_and_never_the_original():
    rows = list(rm.variants(SOURCE, "f"))
    sources = [v for _l, _k, v in rows]
    assert len(sources) == len(set(sources)) and SOURCE not in sources


TRIPLE = """#include "common.h"
extern u8 D_800D6220[];
void initRaceCourseTripleParticle(struct RaceUiTripleParticleActor *arg0) {
    void *temp_t9;

    (*(s16 *)((u8 *)(arg0) + 0x30)) = 0;
    temp_t9 = ((*(u16 *)((u8 *)(arg0) + 0x10)) * 0xC) + D_800D6220;
    (*(s32 *)((u8 *)(arg0) + 0x18)) = (s32) (*(s32 *)((u8 *)(temp_t9) + 0));
    (*(s32 *)((u8 *)(arg0) + 0x1C)) = (s32) (*(s32 *)((u8 *)(temp_t9) + 4));
    (*(s32 *)((u8 *)(arg0) + 0x20)) = (s32) (*(s32 *)((u8 *)(temp_t9) + 8));
    setCallbackTaskCallback(arg0, (void (*)(void *)) updateRaceCourseTripleParticle);
}
"""


def test_struct_copy_merge_fires_on_the_course_particle_cluster():
    rows = list(rm.struct_copy_merges(TRIPLE, "initRaceCourseTripleParticle"))
    assert [label for label, _k, _v in rows] == ["struct_copy:3@5:pointer", "struct_copy:3@5:inline"]
    inline = rows[1][2]
    # The shape that compiled object-exact for all three siblings on 2026-09-13.
    assert "typedef struct { s32 w0; s32 w1; s32 w2; } RegallocWords3;\nvoid initRaceCourseTripleParticle(" in inline
    assert ("    *(RegallocWords3 *)((u8 *)(arg0) + 0x18) = *(RegallocWords3 *)((u8 *)(((*(u16 *)((u8 *)(arg0) + 0x10)) "
            "* 0xC) + D_800D6220));\n") in inline
    assert "temp_t9" not in inline


def test_struct_copy_merge_needs_contiguous_matching_offsets():
    gap = TRIPLE.replace("(arg0) + 0x1C)) = (s32) (*(s32 *)((u8 *)(temp_t9) + 4))",
                         "(arg0) + 0x24)) = (s32) (*(s32 *)((u8 *)(temp_t9) + 4))")
    assert all(":3@" not in label for label, _k, _v in rm.struct_copy_merges(gap, "initRaceCourseTripleParticle"))


ICONS = """void initRaceTypeSelectOptionIcons(RaceTypeSelectRowActor *arg0) {
    arg0->iconX[0] = -0x104;
    arg0->iconX[1] = -0x104;
    arg0->iconX[2] = -0x104;
    arg0->iconX[3] = -0x104;
    arg0->iconY = -0x58;
}
"""

QUEUE = """s32 reserveSoundEffectQueueReadIndex(void) {
    s32 temp_v1;

    temp_v1 = gSoundQueueReadIndex;
    if (temp_v1 == gSoundQueueWriteIndex) {
        return -1;
    }
    gSoundQueueReadIndex = (temp_v1 + 1) & 0x3F;
    return temp_v1;
}
"""


def test_store_loop_fires_on_the_icon_stores_with_the_matched_while_form():
    rows = list(rm.store_loops(ICONS, "initRaceTypeSelectOptionIcons"))
    assert [label for label, _k, _v in rows] == ["store_loop:arg0->iconXx4:while", "store_loop:arg0->iconXx4:for"]
    assert ("    {\n        s32 i = 0;\n        while (i < 4) {\n            arg0->iconX[i] = -0x104;\n"
            "            i++;\n        }\n    }\n    arg0->iconY = -0x58;\n") in rows[0][2]


def test_guard_before_load_then_commutative_swap_reach_the_queue_match():
    moved = texts(rm.guard_before_load(QUEUE, "reserveSoundEffectQueueReadIndex"))
    assert len(moved) == 1
    assert ("    if (gSoundQueueReadIndex == gSoundQueueWriteIndex) {\n        return -1;\n    }\n"
            "    temp_v1 = gSoundQueueReadIndex;\n") in moved[0]
    swapped = texts(rm.commutative_swaps(moved[0], "reserveSoundEffectQueueReadIndex"))
    assert any("if (gSoundQueueWriteIndex == gSoundQueueReadIndex)" in v for v in swapped)


COURSE_RECORD = """void updateRaceUiCourseRecordRevealFinalMoney(struct RaceUiDualCounterActor *arg0) {
    s16 tmp = *(s16 *)((u8 *)(arg0) + 0x1E);
    tmp = tmp - 1;
    *(s16 *)((u8 *)(arg0) + 0x1E) = tmp;
    (*(s16 *)((u8 *)(arg0) + 0x20)) = 3;
    if ((*(s16 *)((u8 *)(arg0) + 0x1E)) == 0) {
        enqueueSoundEffect(0x1A, 0x32);
    }
}
"""


def test_load_modify_store_fires_on_course_record_and_parenthesises_postfix():
    rows = {label: v for label, _k, v in rm.load_modify_stores(COURSE_RECORD, "updateRaceUiCourseRecordRevealFinalMoney")}
    assert set(rows) == {"load_modify_store:tmp:plain", "load_modify_store:tmp:compound", "load_modify_store:tmp:postfix"}
    assert "    (*(s16 *)((u8 *)(arg0) + 0x1E))--;\n" in rows["load_modify_store:tmp:postfix"]
    assert "tmp" not in rows["load_modify_store:tmp:compound"]
    # Then the constant store moves first: the two-step change that matched on 2026-09-13.
    moved = texts(rm.statement_moves(rows["load_modify_store:tmp:compound"], "updateRaceUiCourseRecordRevealFinalMoney"))
    assert any("    (*(s16 *)((u8 *)(arg0) + 0x20)) = 3;\n    *(s16 *)((u8 *)(arg0) + 0x1E) -= 1;\n" in v for v in moved)


def test_field_local_keeps_declaration_and_drops_masks_as_separate_variants():
    labels = [label for label, _k, _v in rm.field_local_eliminations(SPARKLE, "updateRaceUiSparkle")]
    assert "field_local:temp_t7:postfix+unmasked" in labels and "field_local:temp_t7:postfix+keep_decl" in labels
    kept = [v for label, _k, v in rm.field_local_eliminations(SPARKLE, "updateRaceUiSparkle") if label.endswith("+keep_decl")]
    assert all("    u16 temp_t7;\n" in v for v in kept)


def test_load_modify_store_accepts_statements_sharing_a_line():
    source = ("void updateRaceUiTrickPrizePayoutRevealMakeBonus(struct RaceUiCourseStatsActor *arg0) {\n"
              "    s16 tmp = *(s16 *)((u8 *)(arg0) + 0x1A); tmp--; *(s16 *)((u8 *)(arg0) + 0x1A) = tmp;\n"
              "    (*(s16 *)((u8 *)(arg0) + 0x1C)) = 1;\n}\n")
    rows = {label: v for label, _k, v in rm.load_modify_stores(source, "updateRaceUiTrickPrizePayoutRevealMakeBonus")}
    assert "    (*(s16 *)((u8 *)(arg0) + 0x1A))--;\n    (*(s16 *)((u8 *)(arg0) + 0x1C)) = 1;\n" in rows["load_modify_store:tmp:postfix"]


PVOICES = """void _collectPVoices(ALSynth *drvr) {
    ALLink *var_s0;

    var_s0 = drvr->pLameList.next;
    if (var_s0 != NULL) {
        for (;;) {
            alUnlink(var_s0);
            alLink(var_s0, &drvr->pFreeList);
            var_s0 = drvr->pLameList.next;
        
            if (!(var_s0 != NULL)) break;
        }
    }
}
"""


def test_rotated_loop_fires_on_collectPVoices():
    rows = texts(rm.rotated_loops(PVOICES, "_collectPVoices"))
    # The unrotated loop that matched object-exact on 2026-09-13.
    assert rows == ["""void _collectPVoices(ALSynth *drvr) {
    ALLink *var_s0;

    var_s0 = drvr->pLameList.next;
    while (var_s0 != NULL) {
        alUnlink(var_s0);
        alLink(var_s0, &drvr->pFreeList);
        var_s0 = drvr->pLameList.next;
    }
}
"""]


def test_rotated_loop_needs_the_same_condition():
    other = PVOICES.replace("if (!(var_s0 != NULL)) break;", "if (!(var_s0 != drvr)) break;")
    assert texts(rm.rotated_loops(other, "_collectPVoices")) == []


FDRUMS = """void *Fdrums(void *arg0, u8 *arg1) {
    void *temp_a2;

    temp_a2 = (*(void **)((u8 *)(arg0) + 0x54));
    (*(void **)((u8 *)(arg0) + 0x70)) = (void *)((char *)temp_a2 + (*(int **)((u8 *)temp_a2 + 0x14))[ *arg1 ]);
    return arg1 + 1;
}
"""


def test_readonly_field_local_then_store_value_local_reach_the_fdrums_match():
    inlined = texts(rm.readonly_field_local_inlines(FDRUMS, "Fdrums"))
    assert len(inlined) == 1 and "temp_a2" not in inlined[0]
    held = texts(rm.store_value_locals(inlined[0], "Fdrums"))
    F = "(*(void **)((u8 *)(arg0) + 0x54))"
    # The two-step change that matched object-exact on 2026-09-13.
    assert any(("    void * regalloc_value0;\n" in v or "    void *regalloc_value0;\n" in v)
               and f"    regalloc_value0 = (void *) ((char *){F} + (*(int **)((u8 *){F} + 0x14))[ *arg1 ]);\n" in v
               and "    (*(void **)((u8 *)(arg0) + 0x70)) = regalloc_value0;\n" in v for v in held)


def test_readonly_field_local_declines_when_the_field_is_written_later():
    written = FDRUMS.replace("    return arg1 + 1;", "    (*(void **)((u8 *)(arg0) + 0x54)) = 0;\n    return arg1 + 1;")
    assert texts(rm.readonly_field_local_inlines(written, "Fdrums")) == []


HEAP = """extern u16 gRelocatableHeapUsedBlockCount;
extern void *gRelocatableHeapFreeBlockStack;
void releaseRelocatableHeapBlockMetadata(void *arg0) {
    s16 temp_t7;

    temp_t7 = gRelocatableHeapUsedBlockCount - 1;
    gRelocatableHeapUsedBlockCount = temp_t7;
    *(&gRelocatableHeapFreeBlockStack + (temp_t7 & 0xFFFF)) = arg0;
    (*(s8 *)((unsigned char *)arg0 + 0x11)) = 0;
}
"""


def test_field_local_accepts_extern_globals_for_the_heap_counter():
    rows = {label: v for label, _k, v in rm.field_local_eliminations(HEAP, "releaseRelocatableHeapBlockMetadata")}
    postfix = rows["field_local:temp_t7:postfix+unmasked"]
    # The spelling that matched object-exact on 2026-09-13.
    assert "    gRelocatableHeapUsedBlockCount--;\n    *(&gRelocatableHeapFreeBlockStack + gRelocatableHeapUsedBlockCount) = arg0;\n" in postfix


SHOCK = """void updateRacePlayerShockEffect(RaceItemEffectActor *arg0) {
    RacePlayer *temp_v0;

    temp_v0 = &gRacePlayers[arg0->unk10];
    arg0->unk18 = (s32) (arg0->unk18 + temp_v0->collisionVolumes[0].point[0]);
    arg0->unk1C = (s32) (arg0->unk1C + temp_v0->collisionVolumes[0].point[1]);
}
"""

FOLLOW = """void updateRaceCameraFixedPositionFollow(void) {
    s32 temp_a0;
    s32 temp_v1;

    temp_v1 = (*(s32 *)((u8 *)(D_801124A0) + 0xC));
    (*(s32 *)((u8 *)(D_801124A0) + 0xC)) = (s32) (temp_v1 + ((s32) (gRacePlayers[0].projectedPos.x - temp_v1) >> 1));
    temp_a0 = (*(s32 *)((u8 *)(D_801124A0) + 0x10));
    (*(s32 *)((u8 *)(D_801124A0) + 0x10)) = (s32) (temp_a0 + ((s32) (gRacePlayers[0].projectedPos.y - temp_a0) >> 1));
    updateRaceCameraLookAtTransform();
}
"""


def test_compound_assignments_fire_on_shock_effect_all_at_once():
    rows = {label: v for label, _k, v in rm.compound_assignments(SHOCK, "updateRacePlayerShockEffect")}
    assert ("    arg0->unk18 += temp_v0->collisionVolumes[0].point[0];\n"
            "    arg0->unk1C += temp_v0->collisionVolumes[0].point[1];\n") in rows["compound_assign:all"]


def test_self_update_temps_fire_on_camera_follow_and_drop_the_temps():
    rows = {label: v for label, _k, v in rm.self_update_temps(FOLLOW, "updateRaceCameraFixedPositionFollow")}
    merged = rows["self_update:all"]
    assert "temp_v1" not in merged and "temp_a0" not in merged
    assert ("    (*(s32 *)((u8 *)(D_801124A0) + 0xC)) += ((s32) (gRacePlayers[0].projectedPos.x - "
            "(*(s32 *)((u8 *)(D_801124A0) + 0xC))) >> 1);\n") in merged


def test_typed_index_scales_fire_on_title_menu_sparkle():
    source = ("extern u8 titleMenuSparklePositions[];\nvoid initTitleMenuSparkle(MenuScreenEffectActor *arg0) {\n"
              "    void *temp_v0;\n\n    temp_v0 = ((*(u16 *)((u8 *)(arg0) + 0x10)) * 4) + titleMenuSparklePositions;\n}\n")
    rows = {label.split("@")[0]: v for label, _k, v in rm.typed_index_scales(source, "initTitleMenuSparkle")}
    assert set(rows) == {"typed_index:s8x4", "typed_index:u8x4", "typed_index:s16x2", "typed_index:u16x2",
                         "typed_index:s32x1", "typed_index:u32x1"}
    # The spelling that matched object-exact on 2026-09-13.
    assert ("    temp_v0 = (void *)&((s16 *)titleMenuSparklePositions)[(*(u16 *)((u8 *)(arg0) + 0x10)) * 2];\n"
            in rows["typed_index:s16x2"])


def test_typed_index_read_form_fires_on_prize_payout():
    source = ("void initRaceUiPrizePayout(struct RaceUiPrizePayoutActor *arg0) {\n"
              "    (*(s16 *)((u8 *)(arg0) + 0x1C)) = *((s16 *)((u8 *)&gRacePrizeAmountsByCourseAndRank + "
              "((gRacePlayers->rankIndex * 2) + (gRaceCourseIndex.signedValue * 8))));\n}\n")
    rows = [v for label, _k, v in rm.typed_index_scales(source, "initRaceUiPrizePayout") if label.startswith("typed_index_read")]
    # The spelling that matched object-exact on 2026-09-13.
    assert rows and ("((s16 *)&gRacePrizeAmountsByCourseAndRank)[gRacePlayers->rankIndex + "
                     "(gRaceCourseIndex.signedValue * 4)]") in rows[0]


def test_symbol_scale_fix_fires_through_deeply_nested_index_expressions():
    # The index nests parentheses three deep; a regex-based first version declined
    # silently on exactly this residual (initRaceCourseScrollingTexture, 2026-09-13).
    source = ("extern u16 D_800D5FF0;\nextern u16 D_800D5FF4;\n"
              "void initRaceCourseScrollingTexture(struct RaceCourseScrollingTextureActor *arg0) {\n    void *temp_v0;\n\n"
              "    use(*(&D_800D5FF4 + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 8)));\n"
              "    temp_v0 = ((*(u16 *)((u8 *)(arg0) + 0x10)) * 8) + &D_800D5FF0;\n}\n")
    rows = {label: v for label, _k, v in rm.symbol_scale_fixes(source, "initRaceCourseScrollingTexture")}
    assert set(rows) == {"symbol_scale:typed", "symbol_scale:bytes"}
    matched = rows["symbol_scale:bytes"]           # object-exact on 2026-09-13
    assert "use(*(u16 *)((u8 *)&D_800D5FF4 + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 8)));" in matched
    assert "temp_v0 = (void *)((u8 *)&D_800D5FF0 + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 8));" in matched


def test_symbol_scale_fix_accepts_hex_scales():
    source = ("extern s32 gCourseBillboardMarkerVertexResources;\nvoid initCourseBillboardMarker(void *arg0) {\n"
              "    use(*(&gCourseBillboardMarkerVertexResources + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 0x14)));\n}\n")
    rows = {label: v for label, _k, v in rm.symbol_scale_fixes(source, "initCourseBillboardMarker")}
    assert "use(*(s32 *)((u8 *)&gCourseBillboardMarkerVertexResources + ((*(u16 *)((u8 *)(arg0) + 0x10)) * 20)));" in rows["symbol_scale:bytes"]


def test_negative_scale_split_fires_on_camera_intro_pan():
    source = ("void updateRaceCameraIntroPan(void) {\n    s16 var_a0;\n\n"
              "    (*(s32 *)((u8 *)(D_801124A0) + 0xA8)) = (s32) ((fixedSine(var_a0) * -0xC00) + 0xC00000);\n}\n")
    rows = {label.split("@")[0]: v for label, _k, v in rm.negative_scale_splits(source, "updateRaceCameraIntroPan")}
    # The spelling that matched object-exact on 2026-09-13.
    assert "(s32) ((-fixedSine(var_a0) * 0xC00) + 0xC00000)" in rows["negative_scale:negate_operand"]
    assert "(s32) (-(fixedSine(var_a0) * 0xC00) + 0xC00000)" in rows["negative_scale:negate_product"]


def test_result_local_reuse_fires_on_doModFunc():
    source = ("f32 _doModFunc(void *arg0, s32 arg1) {\n    f32 var_f2;\n\n    var_f2 = 1;\n"
              "    return (*(f32 *)((unsigned char *)arg0 + 0x1C)) * (f32) ((f64) var_f2 - 1.0);\n}\n")
    rows = texts(rm.result_local_reuses(source, "_doModFunc"))
    # The shape that matched object-exact on 2026-09-13.
    assert rows == [source.replace("    return (*(f32 *)((unsigned char *)arg0 + 0x1C)) * (f32) ((f64) var_f2 - 1.0);\n",
                                   "    var_f2 = var_f2 - 1.0;\n    return (*(f32 *)((unsigned char *)arg0 + 0x1C)) * var_f2;\n")]
