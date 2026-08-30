#include "common.h"

/* Global variables used by the function. */
extern u8  gRaceRumbleEnabled;
extern u8  gRumblePakConnectedMask;
extern u16 gRumbleMotorRequestStates[];

/* Request that the rumble motor at the given index be started.
 * The argument is a 16‑bit index; only the low 16 bits are used.
 */
void requestRumbleMotorStart(u16 idx)
{
    /* Step 1: check if rumble is enabled for the race. */
    if (gRaceRumbleEnabled == 0)
        return;

    /* Step 2: check if the rumble pak is connected at the requested index. */
    if ((gRumblePakConnectedMask & (1U << (idx))) == 0)
        return;

    /* Step 3: request the rumble motor at the index. */
    gRumbleMotorRequestStates[idx] = 1;
}