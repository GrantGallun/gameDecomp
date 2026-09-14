#include "common.h"

s32 compressRaceRecordReplayData(u8 *src, s32 srcLen, u16 *dst) {
    volatile u8 framePad[0x10];
    s32 v0;
    s32 v1;
    s32 a3;
    s32 t0;
    s32 t1;
    s32 t2;
    s32 t3;
    s32 a2;
    s32 a0;
    u16 *t5;
    u8 *s1;
    u8 *t4;
    u8 t9;

    v0 = 0;
    *dst = (u16)srcLen;
    v1 = 1;
    a3 = srcLen;
    t0 = 0;
    t1 = 0;
    t2 = 1;
    t5 = dst + 1;

    for (;;) {
        a0 = a3;
        if (a3 >= 0x40) {
            a0 = 0x3F;
        }
        a2 = v0 - 1;

        for (;;) {
            if (a2 < 0) {
                break;
            }
            a3 = 0;
            t3 = 0;
            if (a0 > 0) {
                t4 = src + v0;
                s1 = &src[a2];
                for (;;) {
                    t3++;
                    if (*t4 != *s1) {
                        break;
                    }
                    t4++;
                    s1++;
                    a3++;
                    if (t3 == a0) {
                        break;
                    }
                }
            }
            if (t1 < a3) {
                t0 = t2;
                t1 = a3;
            }
            t2++;
            a2--;
            if (t2 >= 0x400) {
                break;
            }
        }

        if (t1 <= 0) {
            t9 = src[v0];
            v1++;
            t5 = (u16 *)((char *)t5 + 2);
            t5[-1] = (u16)t9;
            v0++;
        } else {
            *t5 = (u16)((t1 << 10) | t0);
            v1++;
            t5 = (u16 *)((char *)t5 + 2);
            v0 += t1;
        }

        if (srcLen == v0) {
            break;
        }
        a3 = srcLen - v0;
        if (v1 >= 0x1000) {
            return -1;
        }
        t2 = 1;
        t0 = 0;
        t1 = 0;
    }
    return v1;
}
