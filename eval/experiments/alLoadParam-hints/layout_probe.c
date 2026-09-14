/* Header-only o32 layout check: no reference function body. */
#include "common.h"
#include "synthInternals.h"
#include "PR/libaudio.h"
#define OFFSET_ASSERT(name, type, member, expected) \
    typedef char name[(__builtin_offsetof(type, member) == (expected)) ? 1 : -1]
OFFSET_ASSERT(filter_handler, ALLoadFilter, filter.handler, 0x04);
OFFSET_ASSERT(filter_lstate, ALLoadFilter, lstate, 0x18);
OFFSET_ASSERT(filter_start, ALLoadFilter, loop.start, 0x1c);
OFFSET_ASSERT(filter_end, ALLoadFilter, loop.end, 0x20);
OFFSET_ASSERT(filter_count, ALLoadFilter, loop.count, 0x24);
OFFSET_ASSERT(filter_table, ALLoadFilter, table, 0x28);
OFFSET_ASSERT(filter_book, ALLoadFilter, bookSize, 0x2c);
OFFSET_ASSERT(filter_sample, ALLoadFilter, sample, 0x38);
OFFSET_ASSERT(filter_lastsam, ALLoadFilter, lastsam, 0x3c);
OFFSET_ASSERT(filter_first, ALLoadFilter, first, 0x40);
OFFSET_ASSERT(filter_memin, ALLoadFilter, memin, 0x44);
OFFSET_ASSERT(wave_base, ALWaveTable, base, 0);
OFFSET_ASSERT(wave_len, ALWaveTable, len, 4);
OFFSET_ASSERT(wave_type, ALWaveTable, type, 8);
OFFSET_ASSERT(wave_adpcm_loop, ALWaveTable, waveInfo.adpcmWave.loop, 0xc);
OFFSET_ASSERT(wave_raw_loop, ALWaveTable, waveInfo.rawWave.loop, 0xc);
OFFSET_ASSERT(wave_book, ALWaveTable, waveInfo.adpcmWave.book, 0x10);
OFFSET_ASSERT(book_order, ALADPCMBook, order, 0);
OFFSET_ASSERT(book_predictors, ALADPCMBook, npredictors, 4);
OFFSET_ASSERT(adpcm_start, ALADPCMloop, start, 0);
OFFSET_ASSERT(adpcm_end, ALADPCMloop, end, 4);
OFFSET_ASSERT(adpcm_count, ALADPCMloop, count, 8);
OFFSET_ASSERT(adpcm_state, ALADPCMloop, state, 0xc);
OFFSET_ASSERT(raw_start, ALRawLoop, start, 0);
OFFSET_ASSERT(raw_end, ALRawLoop, end, 4);
OFFSET_ASSERT(raw_count, ALRawLoop, count, 8);
typedef char state_size[(sizeof(ADPCM_STATE) == 0x20) ? 1 : -1];
