#include "common.h"

/*
 * Mode53 scaffold experiment.
 *
 * Keep the candidate's recovered control flow, but make every RacePlayer
 * access explicit.  This prevents an invented partial struct from silently
 * moving fields when the compiler lays it out.
 */
#define RP_PLAYER_INDEX(p)   (*(u16 *)((u8 *)(p) + 0x000))
#define RP_SOUND_DISABLED(p) (*(s8  *)((u8 *)(p) + 0x014))
#define RP_POS_X(p)          (*(s32 *)((u8 *)(p) + 0x01C))
#define RP_POS_Y(p)          (*(s32 *)((u8 *)(p) + 0x020))
#define RP_POS_Z(p)          (*(s32 *)((u8 *)(p) + 0x024))
#define RP_VEL_X(p)          (*(s32 *)((u8 *)(p) + 0x040))
#define RP_VEL_Y(p)          (*(s32 *)((u8 *)(p) + 0x044))
#define RP_VEL_Z(p)          (*(s32 *)((u8 *)(p) + 0x048))
#define RP_UNK_6E(p)         (*(s16 *)((u8 *)(p) + 0x06E))
#define RP_UNK_74(p)         (*(s32 *)((u8 *)(p) + 0x074))
#define RP_STATE_TIMER(p)    (*(s32 *)((u8 *)(p) + 0x07C))
#define RP_STATE_ANGLE(p)    (*(s16 *)((u8 *)(p) + 0x07E))
#define RP_UNK_254(p)        (*(s32 *)((u8 *)(p) + 0x254))
#define RP_UNK_264(p)        (*(s32 *)((u8 *)(p) + 0x264))
#define RP_STATE_FLAGS(p)    (*(s32 *)((u8 *)(p) + 0x2FC))
#define RP_UPDATE_STATE(p)   (*(s16 *)((u8 *)(p) + 0x302))
#define RP_UPDATE_TIMER(p)   (*(s16 *)((u8 *)(p) + 0x304))

extern s16 gFrameCounter;

void setRaceMotionAnimation(void *player, s32 animation);
void stepRaceMotionAnimationUntilEnd(void *player);
void updateRacePlayerLeanAngle(void *player, s32 unk254, s32 arg2);
void clampRacePlayerVectorXZSpeed(void *velocity, void *player);
s16 fixedSine(s16 angle);
void resetRacePlayerTrickSubstate(void *player);
void initRacePlayerLandingSnowSpray(void);
void createCallbackTaskWithUserIdPreservingArgs(
    void (*callback)(void), s32 arg1, s32 arg2, u16 userId);

void updateRacePlayerMode53AerialTrick(void *player)
{
    s32 verticalVelocity;

    if (RP_UPDATE_STATE(player) == 0) {
        setRaceMotionAnimation(player, 4);
        RP_UPDATE_STATE(player) += 1;
        RP_STATE_FLAGS(player) |= 0x200;
        RP_STATE_TIMER(player) = 0;
        resetRacePlayerTrickSubstate(player);
        setRaceMotionAnimation(player, 0x19);
        RP_UPDATE_TIMER(player) = 0;
    }

    stepRaceMotionAnimationUntilEnd(player);
    updateRacePlayerLeanAngle(player, RP_UNK_254(player), 0);

    RP_VEL_Y(player) -= RP_UNK_264(player);
    clampRacePlayerVectorXZSpeed(&RP_VEL_X(player), player);

    verticalVelocity = RP_VEL_Y(player);
    RP_POS_X(player) += RP_VEL_X(player);
    RP_POS_Y(player) += verticalVelocity;
    RP_POS_Z(player) += RP_VEL_Z(player);
    RP_UNK_74(player) = verticalVelocity;

    if (RP_STATE_FLAGS(player) & 0x400) {
        s16 sine = fixedSine(RP_STATE_ANGLE(player));
        RP_UNK_6E(player) = (s16)((sine * -0x1800) / 0x1000);
    } else {
        s16 sine = fixedSine(RP_STATE_ANGLE(player));
        RP_UNK_6E(player) = (s16)((sine * 0x1800) / 0x1000);
    }

    RP_STATE_TIMER(player) += 0x16;
    RP_UPDATE_TIMER(player)++;

    if (RP_UPDATE_TIMER(player) == 8) {
        setRaceMotionAnimation(player, 0x1A);
    }
    if (RP_UPDATE_TIMER(player) == 0xF) {
        setRaceMotionAnimation(player, 0x1B);
    }
    if (RP_UPDATE_TIMER(player) == 0x1E) {
        setRaceMotionAnimation(player, 0x1C);
    }

    if (RP_STATE_TIMER(player) > 0x400) {
        RP_STATE_TIMER(player) = 0x400;
    }

    RP_STATE_FLAGS(player) |= 2;
    if (RP_STATE_TIMER(player) < 0x3D0) {
        RP_STATE_FLAGS(player) |= 0x800;
        if ((RP_SOUND_DISABLED(player) == 0) && (gFrameCounter & 1)) {
            createCallbackTaskWithUserIdPreservingArgs(
                initRacePlayerLandingSnowSpray, 5, 2,
                RP_PLAYER_INDEX(player));
        }
    }
}
