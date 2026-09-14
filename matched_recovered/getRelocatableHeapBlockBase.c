#include "common.h"

typedef struct {
    void *start;
    char unk_04[0x10];
} RelocatableHeapBlockAlias;

extern RelocatableHeapBlockAlias gRelocatableHeapBlockStartAliases[];

void *getRelocatableHeapBlockBase(s32 handle) {
    return gRelocatableHeapBlockStartAliases[handle].start;
}
