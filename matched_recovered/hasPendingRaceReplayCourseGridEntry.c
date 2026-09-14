#include "common.h"

typedef struct {
    s16 status;
    u16 pad02;
    u32 pad04;
    u32 pad08;
    u32 pad0C;
} CourseGridEntry;

extern CourseGridEntry *D_800DC490[];
extern s16 gRaceCourseIndex;

s32 hasPendingRaceReplayCourseGridEntry(void) {
    CourseGridEntry *entry;
    s16 status;
    s16 sentinelMinusOne;
    s16 sentinelMinusTwo;

    entry = D_800DC490[gRaceCourseIndex];
    sentinelMinusOne = -1;
    sentinelMinusTwo = -2;
    for (;;) {
        status = entry->status;
        if (sentinelMinusTwo != status) {
            if (sentinelMinusOne != status) {
                return 1;
            }
            entry++;
            continue;
        }
        return 0;
    }
}
