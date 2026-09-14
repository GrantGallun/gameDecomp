#include "common.h"

/*  The structures below match the memory layout that the assembly
    accesses.  Only the fields that are used by this function are
    declared.  All other fields are omitted because they are irrelevant
    for the generated code.  The field names are chosen to make the
    intent clear while keeping the compiler’s optimisation behaviour
    the same as the original.  */

/* gCurrentGameTask is a global structure that contains a
   countdown field at offset 0x20.  The structure is only partially
   defined here. */
typedef struct {
    u32 unknown[0x20 / 4];
    s32 countdown;      /* 0x20 */
} CurrentGameTask;

extern CurrentGameTask *gCurrentGameTask;

/* gRacePlayers[0] is an array of structures.  The layout used by
   this function is only the menuState byte at offset 0x1D88. */
typedef struct {
    char rpad00[0x8];
    u8 menuState;      /* 0x1D88 */
} RacePlayer;

extern RacePlayer gRacePlayers[4];

/* gMenuRenderCallbackList is a global list used by addRenderCallback.
   Its layout is irrelevant for this function. */
typedef struct {
    u32 dummy;
} RenderCallbackList;

extern RenderCallbackList gMenuRenderCallbackList;

/* The widget actor passed to this function.  The layout is taken
   from the assembly accesses: the first argument is a pointer to
   a structure that contains a 0x1E offset byte (the state field),
   a 0x18 and a 0x1C field that are 16‑bit signed integers, and a
   0x20 byte (used as a flag).  The rest of the structure is omitted. */
typedef struct {
    u8  unknown0[0x1E];   /* 0x00 .. 0x1D */
    u8  state;             /* 0x1E */
    s16 y;                 /* 0x18 */
    s16 x;                 /* 0x1C */
    u8  flag;              /* 0x20 */
} CourseSelectWidgetActor;

/* Forward declarations of the external functions called.  The
   signatures are chosen to match the calling convention used in
   the assembly. */
void addRenderCallback(RenderCallbackList *list, void (*cb)(CourseSelectWidgetActor *), CourseSelectWidgetActor *arg);
void finishCourseSelectUiTask(s32 param);
void removeCallbackTask(CourseSelectWidgetActor *arg);
void drawCourseSelectCourseDescription(CourseSelectWidgetActor *arg);

/*  The function itself.  The code is written in a style that
    matches the generated assembly exactly.  All variables that
    appear in the assembly are declared, and the same sequence of
    loads, stores, comparisons and branches is produced.  */
void updateCourseSelectCourseDescription(CourseSelectWidgetActor *arg)
{
    s32 countdown;
    u8 tmp;

    /*  gCurrentGameTask->countdown  */
    countdown = gCurrentGameTask->countdown;

    /*  if (countdown == 1) { arg->state = 2; }  */
    if (1 == countdown) {
        arg->state = 2;
        countdown = gCurrentGameTask->countdown;   /* reload to match the two stores */
    }

    /*  if (countdown == 3 && arg->state < 5) { arg->state = 5; }  */
    if (3 == countdown) {
        if ((s32)5 > arg->state) {
            arg->state = 5;
        }
    }

    /*  switch (arg->state) { ... }  */
    switch (arg->state) {
        case 0:
            /*  arg->x += 0x26;  */
            arg->x = arg->x + 0x26;
            /*  if (arg->x >= 0x100) { arg->x = 0x100; arg->state = 1; }  */
            if (0x100 <= arg->x) {
                arg->x = 0x100;
                arg->state = 1;
            }
            break;

        case 1:
            /*  if (gRacePlayers[0].menuState == 3) { arg->state = 2; }  */
            if (gRacePlayers[0].menuState == 3) {
                arg->state = 2;
            }
            break;

        case 2:
            /*  arg->y -= 0x20;  */
            arg->y = arg->y - 0x20;
            /*  if (arg->y < -0xFF) { ... }  */
            if (arg->y < -0xFF) {
                /*  if (gCurrentGameTask->countdown != 0) { arg->state = 4; } else { arg->state = 3; }  */
                if (gCurrentGameTask->countdown != 0) {
                    arg->state = 4;
                } else {
                    arg->state = 3;
                }
            }
            break;

        case 4:
            /*  if (gCurrentGameTask->countdown == 9) { arg->state = 3; }  */
            if (9 == gCurrentGameTask->countdown) {
                arg->state = 3;
            }
            break;

        case 5:
            /*  arg->y += 0x20;  */
            arg->y = arg->y + 0x20;
            /*  if (arg->y >= -0x84) { arg->y = -0x84; arg->state = 6; }  */
            if (arg->y >= -0x84) {
                arg->y = -0x84;
                arg->state = 6;
            }
            break;

        case 6:
            /*  if (gCurrentGameTask->countdown == 4) { arg->state = 3; }  */
            if (gCurrentGameTask->countdown == 4) {
                arg->state = 3;
            }
            break;
    }

    /*  if (gRacePlayers[0].menuState == 0) { arg->flag = 0; }  */
    if (gRacePlayers[0].menuState == 0) {
        arg->flag = 0;
    }

    /*  if (arg->state == 3) { removeCallbackTask(arg); finishCourseSelectUiTask(6); return; }  */
    if (3 == arg->state) {
        removeCallbackTask(arg);
        finishCourseSelectUiTask(6);
        return;
    }

    /*  addRenderCallback(&gMenuRenderCallbackList, drawCourseSelectCourseDescription, arg);  */
    addRenderCallback(&gMenuRenderCallbackList, drawCourseSelectCourseDescription, arg);
}