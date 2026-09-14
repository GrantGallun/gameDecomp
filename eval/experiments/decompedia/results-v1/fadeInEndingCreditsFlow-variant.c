#include "common.h"
#include "game/ending/ending_credits_flow.h"
#include "game/menu/renderer/menu_renderer.h"
#include "game/engine/game_task_scheduler.h"
#include "game/engine/callback_task_scheduler.h"
#include "game/audio/sound_manager.h"
#include "PR/libaudio.h"
#include "game/ending/ending_credits_jam.h"
#include "game/ending/ending_credits_linda.h"
#include "game/ending/ending_credits_nancy.h"
#include "game/ending/ending_credits_slash.h"
#include "game/ending/ending_credits_tommy.h"
#include "game/ending/ending_credits_ui.h"
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef, pass -D_MACRO_INC_GUARD/-U_MACRO_INC_GUARD to set/unset explicitly.
*/
                  /* extern */
                /* extern */
                /* extern */
        /* extern */
                /* extern */
                /* extern */

void fadeInEndingCreditsFlow() {
    s32 temp_v1;
    s32 temp_v1_2;

    temp_v1 = gCurrentGameTask->fadeStep;
    if (temp_v1 != 0) {
        gCurrentGameTask->fadeStep = temp_v1 - 1;
    } else {
        temp_v1_2 = gCurrentGameTask->fade;
        if (temp_v1_2 != 0) {
            gCurrentGameTask->fade = stepMenuFadeAlpha((s32) (s16) temp_v1_2, 0x10, 0U);
        } else {
            setCurrentGameTaskCallback(updateEndingCreditsFlow, 0);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsPageTextActor, 0, 0x64);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsSlash, 0, 0x64);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsNancy, 0, 0x64);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsTommy, 0, 0x64);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsJam, 0, 0x64);
            createCallbackTask((CallbackTaskCallback)initEndingCreditsLinda, 0, 0x64);
            requestMusicSequenceBank(0xA);
        }
    }
    updateCallbackTasks();
}
