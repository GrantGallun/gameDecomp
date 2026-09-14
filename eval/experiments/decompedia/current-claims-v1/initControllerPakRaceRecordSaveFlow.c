#include "common.h"
#include "game/engine/asset_manager.h"
#include "game/engine/callback_task_scheduler.h"
#include "game/engine/game_task_scheduler.h"
#include "game/engine/viewport_manager.h"
#include "game/menu/controller_pak/controller_pak_menu.h"
#include "game/menu/controller_pak/controller_pak_race_record_save_flow.h"
#include "game/menu/main_menu/controller_main_menu_flow.h"
#include "game/race/player/race_player_input.h"
#include "game/save_data.h"

extern u8 D_59AAA0[];
extern u8 D_59DFE0[];
extern u8 D_593D10[];
extern u8 D_598A70[];
extern u8 D_60F1A0[];
extern u8 D_60F990[];
extern f32 D_800E09E0;
extern void *gActiveMenuTask;
extern s32 D_8010ADE0;
extern s32 D_8010ADE4;
extern void *D_8010ADE8;
extern s16 gMenuFadeAlpha;

void initControllerPakRaceRecordSaveScorePanel(void);
void initControllerPakRaceRecordSaveStatusChoicePrompt(void);

void initControllerPakRaceRecordSaveFlow(void) {
    f32 aspect;

    resetAllViewports();
    aspect = D_800E09E0;
    configureViewport(0, 0xA0, 0x78, 0x120, 0xD0, 0x140, 0xF0, aspect);

    gFramebufferSwapDelay.value = 0;
    gControllerPakStatusCodes[0] = 0;
    gMenuChoicePromptState[0] = 0;
    gControllerPakRetryCounts[0] = 0;
    gRacePlayers[0].menuState = 0;
    gMenuSelectionConfirmTimer = 0;
    gCurrentGameTask->fade = 0xFF;
    gActiveMenuTask = 0;
    D_8010ADE0 = 0;
    D_8010ADE4 = 0;
    D_8010ADE8 = 0;
    gMenuFadeAlpha = gCurrentGameTask->fade;
    gGameSaveDataBuffer[0].money = gRacePlayers[0].money;

    loadCompressedRomAsset(D_59AAA0, D_59DFE0, 0x21);
    loadCompressedRomAsset(D_59AAA0, D_59DFE0, 0x24);
    loadCompressedRomAsset(D_593D10, D_598A70, 0x22);
    loadCompressedRomAsset(D_598A70, D_59AAA0, 0x23);
    loadCompressedRomAsset(D_60F1A0, D_60F990, 0x29);

    initCallbackTaskScheduler(0);
    gActiveMenuTask = createCallbackTask(
        initControllerPakRaceRecordSaveScorePanel, 0, 0x61);
    D_8010ADE8 = createCallbackTask(
        initControllerPakRaceRecordSaveStatusChoicePrompt, 0, 0x60);

    gControllerPakRaceRecordSaveStatusTransition.step = 0;
    gControllerPakRaceRecordSaveStatusTransition.alpha = 0;
    gControllerPakRaceRecordSaveStatusTransition.targetStatus = 0;
    gControllerPakRaceRecordSaveStatusTransition.nextStatus = 0;
    gControllerPakMenuState.state = 0;
    gControllerPakMenuState.confirmChoice = 0;

    setCurrentGameTaskCallback(updateControllerPakRaceRecordSaveFlow, 0);
}
