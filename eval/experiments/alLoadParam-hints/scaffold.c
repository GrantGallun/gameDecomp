/* Supervisor-prepared from draft attempt 29535, headers and TU diagnostic metadata.
 * No reference implementation body used. Types are scaffolding, not OSS output.
 */
#include "common.h"
#include "synthInternals.h"
#include "PR/libaudio.h"
#include "compiler_diagnostics.h"
Acmd *alRaw16Pull(void *filter, s16 *outp, s32 outCount, s32 sampleOffset, Acmd *p);
CLANG_DIAGNOSTIC_PUSH
CLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE
s32 alLoadParam(void *filter, s32 paramID, void *param) {
    ALLoadFilter *f = (ALLoadFilter *)filter;
    ALWaveTable *p = (ALWaveTable *)param;
    u8 temp_v0;
    u8 temp_v1_3;
    ALADPCMBook *temp_a0;
    ALWaveTable *temp_v0_2;
    ALWaveTable *temp_v0_3;
    ALWaveTable *temp_v0_4;
    ALWaveTable *temp_v0_5;
    ALADPCMloop *temp_v1;
    ALRawLoop *temp_v1_2;
    ALADPCMloop *temp_v1_4;
    ALRawLoop *temp_v1_5;

    if (paramID != 4) {
        if (paramID == 5) {
            filter->unk28 = param;
            filter->unk38 = 0;
            filter->unk44 = (s32) param->unk0;
            temp_v0 = param->unk8;
            switch (temp_v0) {                      /* irregular */
            case 0:
                temp_v0_2 = filter->unk28;
                filter->unk4 = alAdpcmPull;
                temp_v0_2->unk4 = (s32) (((s32) temp_v0_2->unk4 / 9) * 9);
                temp_v0_3 = filter->unk28;
                temp_a0 = temp_v0_3->unk10;
                filter->unk2C = (s32) (temp_a0->unk0 * 0x10 * temp_a0->unk4);
                temp_v1 = temp_v0_3->unkC;
                if (temp_v1 != NULL) {
                    filter->unk1C = (s32) temp_v1->unk0;
                    filter->unk20 = (s32) temp_v0_3->unkC->unk4;
                    filter->unk24 = (s32) temp_v0_3->unkC->unk8;
                    alCopy(temp_v0_3->unkC + 0xC, filter->unk18, 0x20);
                    return;
                }
                filter->unk24 = 0;
                filter->unk20 = 0;
                filter->unk1C = 0;
                return;
            case 1:
                temp_v0_4 = filter->unk28;
                filter->unk4 = alRaw16Pull;
                temp_v1_2 = temp_v0_4->unkC;
                if (temp_v1_2 != NULL) {
                    filter->unk1C = (s32) temp_v1_2->unk0;
                    filter->unk20 = (s32) temp_v0_4->unkC->unk4;
                    filter->unk24 = (s32) temp_v0_4->unkC->unk8;
                    return;
                }
                filter->unk24 = 0;
                filter->unk20 = 0;
                filter->unk1C = 0;
                return;
            }
        }
    } else {
        temp_v0_5 = filter->unk28;
        filter->unk3C = 0;
        filter->unk40 = 1;
        filter->unk38 = 0;
        if (temp_v0_5 != NULL) {
            filter->unk44 = (s32) temp_v0_5->unk0;
            temp_v1_3 = temp_v0_5->unk8;
            if (temp_v1_3 == 0) {
                temp_v1_4 = temp_v0_5->unkC;
                if (temp_v1_4 != NULL) {
                    filter->unk24 = (s32) temp_v1_4->unk8;
                }
            } else if (temp_v1_3 == 1) {
                temp_v1_5 = temp_v0_5->unkC;
                if (temp_v1_5 != NULL) {
                    filter->unk24 = (s32) temp_v1_5->unk8;
                }
            }
        }
    }
}
CLANG_DIAGNOSTIC_POP
