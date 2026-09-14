#include "common.h"
#include "game/menu/race_type_select/race_type_select_ui.h"
#include "game/engine/callback_task_scheduler.h"
#include "game/engine/render_callback.h"
#include "PR/libaudio.h"
#include "game/race/player/race_player_input.h"
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef, pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD to set/unset explicitly.
*/
void updateRaceTypeSelectCursor(RaceTypeSelectWidgetActor *arg0) {
    u8 var_v0;
    u8 var_v1;

    var_v0 = arg0->transition.bytes.state;
    var_v1 = var_v0;
    if (var_v0 != (u8) gRaceTypeSelectCursorTarget.state) {
        arg0->transition.bytes.state = (u8) gRaceTypeSelectCursorTarget.state;
        var_v0 = (u8) gRaceTypeSelectCursorTarget.state & 0xFF;
        var_v1 = var_v0;
        arg0->sprite.spriteIndex = gRaceTypeSelectCursorTarget.alpha;
    }
    switch (var_v1) {
    case 4:
        break;
    case 0:
        arg0->sprite.spriteIndex += 0x26;
        if (arg0->sprite.spriteIndex >= 0x100) {
            arg0->sprite.spriteIndex = 0x100;
            arg0->transition.bytes.state = 1;
        }
        var_v0 = arg0->transition.bytes.state;
        break;
    case 1:
        if ((s32) arg0->transition.bytes.timer < 0x10) {
            arg0->sprite.spriteIndex -= 9;
        } else {
            arg0->sprite.spriteIndex += 9;
        }
        var_v0 = arg0->transition.bytes.state;
        arg0->transition.bytes.timer = (arg0->transition.bytes.timer + 1) & 0x1F;
        break;
    case 2:
        if (gRacePlayers->menuState == 1) {
            arg0->transition.bytes.state = 3;
            var_v0 = 3 & 0xFF;
        }
        break;
    case 3:
        arg0->x -= 0x20;
        if (arg0->x < -0xEF) {
            arg0->transition.bytes.state = 4;
        }
        var_v0 = arg0->transition.bytes.state;
        break;
    }
    gRaceTypeSelectCursorAnimState = var_v0;
    if (arg0->transition.bytes.state == 4) {
        removeCallbackTask(arg0);
        return;
    }
    addRenderCallback(&gMenuRenderCallbackList, (void (*)(void *)) drawRaceTypeSelectCursor, arg0);
}
/* Warning: struct RaceUiRankTrigger is not defined (only forward-declared) */
