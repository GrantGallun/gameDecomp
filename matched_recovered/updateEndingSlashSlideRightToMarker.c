#include "common.h"

typedef struct {
    char pad00[0x18];
    s32 posX;
    s32 posY;
    s32 posZ;
} EndingCreditsSlash;

extern void loopMainMenuSceneModelAnimation(int);
extern void setCallbackTaskCallback(void *, void (*)(void *));
extern void setMainMenuSceneModelPosition(int, s32, s32, s32);
extern void addMainMenuSceneModelDrawCallback(int);
extern void updateEndingSlashSlideLeftToMarker(void *);

void updateEndingSlashSlideRightToMarker(EndingCreditsSlash *slash) {
    loopMainMenuSceneModelAnimation(0);

    slash->posX += 0x1D000;

    if (slash->posX >= -0x320000) {
        slash->posX = -0x320000;
        setCallbackTaskCallback(slash, updateEndingSlashSlideLeftToMarker);
    }

    setMainMenuSceneModelPosition(0, slash->posX, slash->posY, slash->posZ);
    addMainMenuSceneModelDrawCallback(0);
}
