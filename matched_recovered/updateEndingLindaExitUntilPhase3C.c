#include "common.h"

/* External function prototypes */
extern void loopMainMenuSceneModelAnimation(int phase);
extern void setCallbackTaskCallback(void *obj, void (*callback)(void *));
extern void setMainMenuSceneModelAnimation(int phase, int animIndex);
extern void setMainMenuSceneModelRotation(int phase, int rotY, int rotX, int rotZ);
extern void setMainMenuSceneModelPosition(int phase, int x, int y, int z);
extern void addMainMenuSceneModelTexturedDrawCallbackWithUnusedArg(
    int phase, int textureId, int paletteId, int unused);

/* Forward declaration of the callback used in this function */
extern void updateEndingLindaTumbleToPhase3C(void *);

/* Global variable representing the current phase of the ending credits. */
extern u16 gEndingCreditsSequencePhase;

/* Structure that matches the layout accessed by the assembly. */
typedef struct {
    u8  _pad0[0x18];   /* 0x00 - 0x17 */
    int posX;          /* 0x18 */
    int posY;          /* 0x1C */
    int posZ;          /* 0x20 */
    s16  field24;      /* 0x24 */
    s16  rotY;         /* 0x26 */
    s16  field28;      /* 0x28 */
    s16  field2A;      /* 0x2A */
    u8   _pad2C[2];    /* 0x2C */
    u16  textureId;    /* 0x2E */
    u16  paletteId;    /* 0x30 */
} EndingCreditsLinda;

/* The function that matches the given assembly. */
void updateEndingLindaExitUntilPhase3C(EndingCreditsLinda *obj)
{
    loopMainMenuSceneModelAnimation(3);

    /* Add the offset to the position X field. */
    obj->posX += 0xFFF58000;

    if (gEndingCreditsSequencePhase == 0x3C) {
        /* Reset the field at 0x2A. */
        obj->field2A = 0;

        /* Set the callback for the next phase. */
        setCallbackTaskCallback((void *)obj,
            (void (*)(void *))updateEndingLindaTumbleToPhase3C);

        /* Start the animation for this model. */
        setMainMenuSceneModelAnimation(3, 0x5C);

        /* Set the rotation Y to 0xC00. */
        obj->rotY = 0xC00;

        /* Apply the rotation to the model. */
        setMainMenuSceneModelRotation(
            3,
            obj->field24,
            obj->rotY,
            obj->field28);
    }

    /* Set the position of the model. */
    setMainMenuSceneModelPosition(
        3,
        obj->posX,
        obj->posY,
        obj->posZ);

    /* Add a textured draw callback for the model. */
    addMainMenuSceneModelTexturedDrawCallbackWithUnusedArg(
        3,
        obj->textureId,
        obj->paletteId,
        0xB);
}