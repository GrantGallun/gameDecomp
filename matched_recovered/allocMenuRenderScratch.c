#include "common.h"

extern u8 *gMenuRenderScratchPtr;
extern u8 *gMenuRenderScratchStart;
extern u32 gMenuRenderScratchUsedSize;

void *allocMenuRenderScratch(s32 size)
{
    u8 *oldPtr = gMenuRenderScratchPtr;
    u8 *newPtr = ((0, oldPtr)) + ((((u32)(size + 3)) >> 2) * 4);
    s32 new_var2;

    new_var2 = newPtr - gMenuRenderScratchStart;
    newPtr++;
    newPtr--;

    if (gMenuRenderScratchPtr) {
    }

    if ((u32)(newPtr - gMenuRenderScratchStart) >= 0x8000) {
        return 0;
    }

    gMenuRenderScratchPtr = newPtr;
    gMenuRenderScratchUsedSize = new_var2;

    return oldPtr;
}
