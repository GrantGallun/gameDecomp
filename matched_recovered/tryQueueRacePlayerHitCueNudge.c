#include "common.h"

typedef struct {
    char pad0[0x2EC];
    s16 facingAngle;
    char pad2EE[0xE];
    s32 stateFlags;
} RacePlayerHitCueView;

extern s32 gRacePlayerHitCueId;
extern s32 gRacePlayerHitAngle;
extern s32 gRacePlayerHitDistance;

s32 tryQueueRacePlayerHitCueNudge(RacePlayerHitCueView *player)
{
    s32 temp_v0;
    s32 var_v1;
    s32 temp_t9;

    temp_v0 = player->stateFlags;
    if (temp_v0 & 0x43000) {
        return 1;
    }
    var_v1 = gRacePlayerHitAngle - player->facingAngle;
    var_v1 += 0x800;
    temp_t9 = var_v1 & 0xFFF;
    var_v1 = (s16) temp_t9;
    if (temp_v0 & 0x400) {
        var_v1 = (s16) (var_v1 + 0x800);
    }
    var_v1 += 0x400;
    var_v1 = (s16) (var_v1 & 0xFFF);
    if ((gRacePlayerHitDistance < 0x14000) || (var_v1 >= 0x801)) {
        return 1;
    }
    if (gRacePlayerHitCueId < 2) {
        gRacePlayerHitCueId = 2;
    }
    return 0;
}
