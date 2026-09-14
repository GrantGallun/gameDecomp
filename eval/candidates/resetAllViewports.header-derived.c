#include "common.h"
#include "game/engine/viewport_manager.h"

void resetAllViewports(void) {
    s32 i;

    for (i = 0; i < 4; i++) {
        gViewportStates[i].active = 0;
        gViewportStates[i].screenBoundsValid = 0;
        gViewportStates[i].clearFramebuffer = 0;
        gViewportStates[i].overlayActive = 0;
        gViewportStates[i].overlayR = 0;
        gViewportStates[i].overlayG = 0;
        gViewportStates[i].overlayB = 0;
        gViewportStates[i].overlayAlpha = 0;
        gViewportStates[i].unk14 = 0x1FF;
        gViewportStates[i].unk16 = 0;
        gViewportStates[i].unk1C = 0x1FF;
        gViewportStates[i].unk1E = 0;
    }
}
