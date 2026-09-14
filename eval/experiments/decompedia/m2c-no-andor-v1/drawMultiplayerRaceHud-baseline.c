#include "common.h"
#include "game/race/ui/race_hud.h"
#include "game/engine/relocatable_heap.h"
#include "game/menu/renderer/menu_render_utils.h"
#include "PR/libaudio.h"
#include "game/engine/asset_manager.h"
#include "game/race/player/race_player_input.h"
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef, pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD to set/unset explicitly.
*/
extern u8 gCurrentViewportIndex;
extern s16 gRaceHudSpinnerFrame;
extern ? gRaceTimerOnesDigitTileIds;
extern ? gRaceTimerTensDigitTileOffsets;

void drawMultiplayerRaceHud(void *arg0) {
    RacePlayer *temp_v1;
    RacePlayer *temp_v1_2;

    drawScaledAssetTableSprite(0x38, 0x24, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1C]), (((s16) gRaceHudSpinnerFrame >> 1) + 4) & 0xFFFF, 1U);
    temp_v1 = &gRacePlayers[gCurrentViewportIndex];
    drawScaledAssetTableSprite(-0x18, -0x38, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1F]), ((temp_v1->itemEffectCount + *(&gRaceTimerTensDigitTileOffsets + temp_v1->itemEffectType)) - 1) & 0xFFFF, (u16) (temp_v1->itemEffectPalette + 1));
    temp_v1_2 = &gRacePlayers[gCurrentViewportIndex];
    drawScaledAssetTableSprite(-8, -0x38, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1F]), (u16) *(&gRaceTimerOnesDigitTileIds + temp_v1_2->actionEffectType), (u16) (temp_v1_2->actionEffectPalette + 1));
    drawScaledAssetTableSprite(-0x4C, 0x18, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1F]), *(&gRacePlayers->rankIndex + (gCurrentViewportIndex * 0x60C)) & 0xFFFF, 1U);
    if ((s32) gCurrentViewportIndex < 2) {
        drawAssetTableSprite(-0x44, -0x30, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1F]), 0x1AU);
        return;
    }
    drawAssetTableSprite(0x14, -0x30, getRelocatableHeapBlockBase((s32) gAssetHandles[0x1F]), 0x1AU);
}
/* Warning: struct AssetTable is not defined (only forward-declared) */
/* Warning: struct RaceUiRankTrigger is not defined (only forward-declared) */
