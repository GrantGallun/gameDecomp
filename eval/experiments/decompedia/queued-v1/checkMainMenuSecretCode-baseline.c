#include "common.h"

extern u8 gMainMenuSecretCodeStep;
extern s32 gPlayerInputHeld;

s32 checkMainMenuSecretCode(void)
{
    switch (gMainMenuSecretCodeStep) {
    case 0:
        if (gPlayerInputHeld == 0x20000) {
            gMainMenuSecretCodeStep++;
        }
        goto end0;
    case 1:
        if ((gPlayerInputHeld != 0x20000) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x10000) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 2:
        if ((gPlayerInputHeld != 0x10000) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x400) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 3:
        if ((gPlayerInputHeld != 0x400) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x800) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 4:
        if ((gPlayerInputHeld != 0x800) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 4) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 5:
        if ((gPlayerInputHeld != 4) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 8) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 6:
        if ((gPlayerInputHeld != 8) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x20) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 7:
        if ((gPlayerInputHeld != 0x20) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x10) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 8:
        if ((gPlayerInputHeld != 0x10) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x2000) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 9:
        if ((gPlayerInputHeld != 0x2000) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x200) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 10:
        if ((gPlayerInputHeld != 0x200) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 1) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 11:
        if ((gPlayerInputHeld != 1) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x10000) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 12:
        if ((gPlayerInputHeld != 0x10000) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x4000) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 13:
        if ((gPlayerInputHeld != 0x4000) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 0x100) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 14:
        if ((gPlayerInputHeld != 0x100) && (gPlayerInputHeld != 0)) {
            if (gPlayerInputHeld == 2) {
                gMainMenuSecretCodeStep++;
            } else {
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    case 15:
        if (gPlayerInputHeld != 2) {
            if (gPlayerInputHeld != 0) {
                if (gPlayerInputHeld == 0x1000) {
                    return 1;
                }
                gMainMenuSecretCodeStep = -1;
            }
        }
        goto end0;
    default:
    end0:
        return 0;
    }
}
