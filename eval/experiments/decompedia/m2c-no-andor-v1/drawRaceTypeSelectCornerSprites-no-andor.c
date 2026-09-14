#include "common.h"
#include "game/menu/race_type_select/race_type_select_ui.h"
#include "game/engine/relocatable_heap.h"
#include "game/menu/renderer/menu_renderer.h"
#include "PR/libaudio.h"
#include "game/engine/asset_manager.h"
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef, pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD to set/unset explicitly.
*/
void drawRaceTypeSelectCornerSprites(RaceTypeSelectWidgetActor *arg0) {
    drawMenuSprite(arg0->x, arg0->y, getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), 3U, 0x20U, 0x20U, 0U, 0U);
    drawMenuSprite((s16) (arg0->x + 0x40), arg0->y, getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), 4U, 0x20U, 0x20U, 0U, 0U);
    drawMenuSprite(arg0->x, (s16) (arg0->y + 0x40), getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), 5U, 0x20U, 0x20U, 0U, 0U);
    drawMenuSprite((s16) (arg0->x + 0x40), (s16) (arg0->y + 0x40), getRelocatableHeapBlockBase((s32) gAssetHandles[0x21]), 6U, 0x20U, 0x20U, 0U, 0U);
}
