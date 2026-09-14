#include "common.h"

#define RACE_POSITION_PLAYER_COUNT 4

extern s8 *gRaceCoursePlayerPathOffsetTables[];
extern s16 gRaceCourseIndex;

s32 getRacePlayerPathOffset(s32 playerIndex, s32 pathIndex)
{
    s8 *entry;

    if (gRaceCourseIndex == 7) {
        if (playerIndex == 0) {
            return 0xFFF40000;
        }
        if (playerIndex == 1) {
            return 0xC0000;
        }
        if (playerIndex == 2) {
            return 0xFFDC0000;
        }
        if (playerIndex == 3) {
            return 0x240000;
        }
    }

    entry = gRaceCoursePlayerPathOffsetTables[
        (gRaceCourseIndex * RACE_POSITION_PLAYER_COUNT) + playerIndex];
    return entry[pathIndex] << 0x12;
}
