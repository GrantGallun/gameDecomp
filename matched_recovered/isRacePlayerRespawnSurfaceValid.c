#include "common.h"
#include "common.h"

typedef struct {
    char pad00[0x1c];
    s32 field1C;   /* param0+0x1c */
    char pad20[0x4];
    s32 field24;   /* param0+0x24 */
    char pad28[0x4da];
    s16 field502;  /* param0+0x502 */
} RacePlayer;

extern s32 getRaceCourseSurfaceType(s32 a0, s32 a1, s32 a2);

/* 0-byte stack frame – no locals, no spilled registers */
s32 isRacePlayerRespawnSurfaceValid(RacePlayer *player) {
    /* Load the three arguments exactly as the target does */
              /* lw a1,0x1c(a3) */
              /* lw a2,0x24(a3) */
             /* lh a0,0x502(a0) */

    /* Call getRaceCourseSurfaceType with the three arguments */
    s32 surface = getRaceCourseSurfaceType(player->field502, player->field1C, player->field24);

    /* The function simply returns 0 for the listed surface types,
       otherwise it returns 1.  The comparison sequence is
       arranged to match the exact branch layout of the target. */
    if (surface == 3)      return 0;
    if (surface == 6)      return 0;
    if (surface == 7)      return 0;
    if (surface == 8)      return 0;
    if (surface == 0xC)    return 0;
    if (surface == 0xE)    return 0;
    if (surface == 0xF)    return 0;
    if (surface == 0x10)   return 0;
    return 1;
}