#include "common.h"

typedef union {
    s32 word;
    struct {
        s16 yaw;
        s16 pitch;
    } half;
} PackedAngles;

typedef struct {
    char pad0[0x2C];
    PackedAngles angle;
    s16 scale;
    s16 tilt;
    s16 pitchVelocity;
} RaceIntroFlyoverCamera;

void approachRaceIntroFlyoverPitchVelocity(
    RaceIntroFlyoverCamera *camera, s16 target)
{
    s16 diff = target - camera->pitchVelocity;

    if (diff >= 5) {
        diff = 4;
    }
    if (diff < -4) {
        diff = -4;
    }

    camera->pitchVelocity += diff;
    camera->angle.half.pitch +=
        (((-camera->pitchVelocity * 2) - camera->angle.half.pitch) >> 3);
}
