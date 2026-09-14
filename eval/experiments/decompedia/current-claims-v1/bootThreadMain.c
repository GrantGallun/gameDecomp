#include "common.h"

/* Forward declarations of external objects and functions */
struct OSThread;

extern struct OSThread gGameThread;
extern u8 gPiManagerQueue[];
extern u8 gPiManagerMessages[];
extern u8 D_80328480[];
extern void gameThreadMain(void *);

extern void osCreatePiManager(u8 queue, void *queuePtr, void *msgPtr, u8 msgCount);
extern void osCreateThread(struct OSThread *thread, u32 priority, void (*func)(void *), void *arg,
                           void *stack, u32 stackSize);
extern void osStartThread(struct OSThread *thread);
extern void osSetThreadPri(struct OSThread *thread, u32 priority);

/* Entry point for the boot thread */
void bootThreadMain(void *arg)
{
    osCreatePiManager(0x96, gPiManagerQueue, gPiManagerMessages, 0xC8);
    osCreateThread(&gGameThread, 2, gameThreadMain, arg, D_80328480, 0xA);
    osStartThread(&gGameThread);
    osSetThreadPri(0, 0);
    for (;;)
        ;
}