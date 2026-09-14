#include "common.h"

extern s16 D_2003538;

extern void *allocFixedTransformMatrix(void *arg);
extern void *getRelocatableHeapBlockBase(s32 arg);

extern u8 gRenderMatricesDirty;
extern void *gRegionAllocPtr;
extern s16 gAssetHandles[0x20];

typedef struct {
    u8 pad00[0x24];
    u8 copyBlock[0x40];
    void *matrix;
    u8 pad68[2];
    u8 matrixDirty;
} RaceUiSingleTrailActor;

void renderRaceUiSingleTrailEffect(RaceUiSingleTrailActor *actor) {
    void *temp_v1;
    void *temp_v1_2;
    void *temp_v1_3;
    void *temp_v1_4;
    void *temp_v1_5;

    if (gRenderMatricesDirty != 0) {
        actor->matrixDirty = 1;
    }
    if (actor->matrixDirty != 0) {
        actor->matrixDirty = 0;
        actor->matrix = allocFixedTransformMatrix(&actor->copyBlock);
    }
    if (actor->matrix != NULL) {
        temp_v1 = gRegionAllocPtr;
        gRegionAllocPtr = (void *)((u32)gRegionAllocPtr + 8);
        ((u32 *)temp_v1)[0] = 0xE7000000;
        ((u32 *)temp_v1)[1] = 0;

        temp_v1_2 = gRegionAllocPtr;
        gRegionAllocPtr = (void *)((u32)gRegionAllocPtr + 8);
        ((u32 *)temp_v1_2)[0] = 0xBC000806;
        ((u32 *)temp_v1_2)[1] =
            (u32)getRelocatableHeapBlockBase((s32)gAssetHandles[0xA]);

        temp_v1_3 = gRegionAllocPtr;
        gRegionAllocPtr = (void *)((u32)gRegionAllocPtr + 8);
        ((u32 *)temp_v1_3)[0] = 0xBC000C06;
        ((u32 *)temp_v1_3)[1] =
            (u32)getRelocatableHeapBlockBase((s32)gAssetHandles[0xB]);

        temp_v1_4 = gRegionAllocPtr;
        gRegionAllocPtr = (void *)((u32)gRegionAllocPtr + 8);
        ((u32 *)temp_v1_4)[0] = 0x01020040;
        ((u32 *)temp_v1_4)[1] = (u32)actor->matrix;

        temp_v1_5 = gRegionAllocPtr;
        gRegionAllocPtr = (void *)((u32)gRegionAllocPtr + 8);
        ((u32 *)temp_v1_5)[0] = 0x06000000;
        ((u32 *)temp_v1_5)[1] = (u32)&D_2003538;
    }
}
