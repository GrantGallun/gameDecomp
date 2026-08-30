#include "common.h"

/* Forward declarations of external symbols used in this file */
extern void createCallbackTask(void *callback, void *arg, u32 param);
extern void removeCallbackTask(void *task);
extern void addRenderCallback(void *list, void *callback, void *arg);

extern void *initRaceSetupPlayerCountCursor(void);
extern void *drawRaceSetupFourPlayerOption(void);

/* Structures representing the data layout seen in the binary */

typedef struct {
    /* 0x00 .. 0x17 */ char pad0[0x18];
    s16 x;                     /* offset 0x18 */
    /* 0x1A */               char pad1[0x1];
    char dpad1b[0x1];
    u8 state;                  /* offset 0x1C */
} MenuItemActor;

typedef struct {
    /* 0x00 .. 0x1B */ char pad[0x1C];
    u32 fadeStep;               /* offset 0x1C */
} CurrentGameTask;

/* Global variables referenced by the function */
extern CurrentGameTask *gCurrentGameTask;
extern void *gMenuRenderCallbackList;

/* Function implementation matching the assembly exactly */
void updateRaceSetupFourPlayerOption(MenuItemActor *item)
{
    switch (item->state) {
    case 0:
        item->x -= 0x20;
        if (item->x == -0x50) {
            item->state = 1;
            createCallbackTask(initRaceSetupPlayerCountCursor, 0, 0x63);
        }
        break;
    case 1:
        break;
    case 2:
        item->x -= 0x20;
        break;
    default:
        break;
    }

    if (item->x < -0x108) {
        removeCallbackTask(item);
        gCurrentGameTask->fadeStep = 2;
    } else {
        addRenderCallback(&gMenuRenderCallbackList, drawRaceSetupFourPlayerOption, item);
    }
}