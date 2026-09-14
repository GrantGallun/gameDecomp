#include "common.h"
#include "game/menu/race_setup/race_setup_ui.h"
#include "game/engine/relocatable_heap.h"
#include "game/menu/renderer/menu_renderer.h"
#include "PR/libaudio.h"
#include "game/engine/asset_manager.h"
#include "game/engine/callback_task_scheduler.h"
#include "game/menu/main_menu/controller_main_menu_flow.h"
#include "game/menu/race_setup/race_setup_menu.h"
#include "game/race/player/race_player_input.h"
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef, pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD to set/unset explicitly.
*/
extern ? gRaceSetupSaveChoicePromptBottomSprites;
extern ? gRaceSetupSaveChoicePromptTopSprites;
extern ? gRaceSetupSaveStatusTransitionStates;

void drawRaceSetupSaveChoicePrompts(TitleMenuTransitionActor *arg0) {
    RaceSetupMenuSubState *var_s6;
    TitleMenuTransitionActor *var_s1;
    s16 *temp_s4;
    s16 *var_s2;
    s16 temp_v0_2;
    s16 temp_v0_3;
    s32 var_s0;
    s32 var_s0_2;
    s32 var_s5;
    s32 var_s7;
    u16 temp_v0;

    if (gActiveMenuTask->unk1E == 8) {
        var_s5 = 0;
        var_s7 = 0;
        if ((s32) gPlayerCount > 0) {
            var_s2 = gMenuChoicePromptState;
            var_s6 = &gRaceSetupMenuSubState;
            var_s1 = arg0;
            do {
                temp_v0 = var_s6->pendingStatusCodes[0];
                if (temp_v0 != var_s1->selection[0]) {
                    var_s1->selection[0] = temp_v0;
                }
                temp_v0_2 = *var_s2;
                if (temp_v0_2 != 0) {
                    temp_s4 = var_s7 + gControllerPakStatusCodes;
                    if (!(temp_v0_2 & 1)) {
                        var_s0 = 0x100;
                    } else {
                        var_s0 = 0x60;
                    }
                    drawMenuSpriteWithAlpha(var_s1->x[0], var_s1->topY[0], getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), (u16) *(&gRaceSetupSaveChoicePromptTopSprites + (*temp_s4 * 2)), 0x20U, 0x20U, 0U, (u16) var_s0, 0U);
                    var_s0_2 = 0x100;
                    if (var_s0 == 0x100) {
                        var_s0_2 = 0x60;
                    }
                    drawMenuSpriteWithAlpha(var_s1->x[0], var_s1->y[0], getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), (u16) *(&gRaceSetupSaveChoicePromptBottomSprites + (*temp_s4 * 2)), 0x20U, 0x20U, 0U, (u16) var_s0_2, 0U);
                    temp_v0_3 = *var_s2;
                    if (temp_v0_3 != 3) {
                        if (temp_v0_3 == 4) {
                            goto block_14;
                        }
                    } else {
block_14:
                        drawMenuSpriteWithAlpha(var_s1->x[0], (s16) (((*var_s2 * 0x10) + var_s1->y[0]) - 0x30), getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), 0x12U, 0x20U, 0x20U, 0U, (u16) (s32) var_s1->alpha[0], (u8) (var_s5 + 7));
                    }
                }
                var_s7 += 2;
                if (*var_s2 >= 5) {
                    if (var_s1->slideOffset[0] == 0) {
                        var_s6->nextStatusCodes[0] = var_s1->selection[0];
                        *(&gRaceSetupSaveStatusTransitionStates + var_s5) = 2;
                        *var_s2 = 0;
                    }
                }
                var_s5 += 1;
                var_s6 += 2;
                var_s2 += 2;
                var_s1 += 2;
            } while (var_s5 < (s32) gPlayerCount);
        }
    }
}
