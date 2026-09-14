#include "common.h"

#define RACE_POSITION_PLAYER_COUNT 4
#define RACE_POSITION_PLAYER_SIZE 0x60C

typedef struct {
    u8 pad0[0x520];
    s32 smoothedPathOffset;
    u8 pad524[RACE_POSITION_PLAYER_SIZE - 0x524];
} RacePositionPlayer;

extern s8 *gRaceCoursePlayerPathOffsetTables[];
extern RacePositionPlayer gRacePlayers[RACE_POSITION_PLAYER_COUNT];
extern s16 gRaceCourseIndex;

s32 updateRacePlayerSmoothedPathOffset(
    s32 playerIndex, s32 pathIndex, s32 rankSlot)
{
    s32 courseIndex;
    s32 pathIndexCopy;
    s8 *entry;

    courseIndex = gRaceCourseIndex;
    entry = gRaceCoursePlayerPathOffsetTables[
        (courseIndex * RACE_POSITION_PLAYER_COUNT) + playerIndex];
    pathIndexCopy = pathIndex;
    if (courseIndex == 7) {
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
        (courseIndex * RACE_POSITION_PLAYER_COUNT) + playerIndex];
    pathIndex = entry[pathIndexCopy] << 0x12;
    pathIndex -= gRacePlayers[rankSlot].smoothedPathOffset;

    if (pathIndex > 0x60000) {
        pathIndex = 0x60000;
    }
    if (pathIndex < -0x60000) {
        pathIndex = -0x60000;
    }

    gRacePlayers[rankSlot].smoothedPathOffset += pathIndex;
    return gRacePlayers[rankSlot].smoothedPathOffset;
}
