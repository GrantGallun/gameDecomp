#include "common.h"

extern s32 gRaceCourseSurfaces;
extern s32 gRaceCourseSurfaceCoords;

typedef struct RaceCourseSurfaceSpawnRecord {
    char pad0[0x10];
    s16 referenceCoordIndex;
    s16 pathAngle;
} RaceCourseSurfaceSpawnRecord;

typedef struct RaceCourseSurfaceCoord {
    s16 x;
    s16 y;
    s16 z;
} RaceCourseSurfaceCoord;

void getRaceCourseSurfaceSpawnTransform(
    s32 surfaceIndex, s32 *posX, s32 *posY, s32 *posZ, s16 *angle)
{
    s32 *surfaceBase;
    s32 surfaceOffset;

    surfaceOffset = surfaceIndex * 0x1C;
    surfaceBase = &gRaceCourseSurfaceCoords;
    *posX = ((RaceCourseSurfaceCoord *)(*surfaceBase +
        (((RaceCourseSurfaceSpawnRecord *)(
            gRaceCourseSurfaces + surfaceOffset))->referenceCoordIndex * 6)))->x
        << 0x11;
    *posY = ((RaceCourseSurfaceCoord *)(*surfaceBase +
        (((RaceCourseSurfaceSpawnRecord *)(
            gRaceCourseSurfaces + surfaceOffset))->referenceCoordIndex * 6)))->y
        << 0x11;
    *posZ = ((RaceCourseSurfaceCoord *)(*surfaceBase +
        (((RaceCourseSurfaceSpawnRecord *)(
            gRaceCourseSurfaces + surfaceOffset))->referenceCoordIndex * 6)))->z
        << 0x11;
    *angle = -((RaceCourseSurfaceSpawnRecord *)(
        gRaceCourseSurfaces + surfaceOffset))->pathAngle;
}
