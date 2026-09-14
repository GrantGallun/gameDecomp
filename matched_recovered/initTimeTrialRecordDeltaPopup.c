#include "common.h"

struct RaceElapsedTimer;

extern s16 gRaceCourseIndex;
extern u8 gGameSaveDataBuffer[];
extern struct RaceElapsedTimer gRaceElapsedTimer;

extern u8 calculateRaceTimerDelta(struct RaceElapsedTimer *timer,
                                  void *courseData,
                                  u32 *delta);
extern void setCallbackTaskCallback(void *actor, void (*callback)(void *));
extern void updateTimeTrialRecordDeltaPopupSlideIn(void *actor);

typedef struct {
    u8 pad00[0x1C];
    s32 field1;
    s32 field2;
    u8 pad24[4];
    s32 field3;
    u32 delta;
    u8 flag;
} RaceUiTimeTrialRecordDeltaPopupActor;

void initTimeTrialRecordDeltaPopup(
    RaceUiTimeTrialRecordDeltaPopupActor *popup)
{
    popup->field2 = -0x2C;
    popup->field1 = 0x1A4;
    popup->field3 = 0x38;

    popup->flag = calculateRaceTimerDelta(
        &gRaceElapsedTimer,
        (void *)(gGameSaveDataBuffer + (gRaceCourseIndex * 4) + 0x12A),
        &popup->delta);

    setCallbackTaskCallback(popup, updateTimeTrialRecordDeltaPopupSlideIn);
}
