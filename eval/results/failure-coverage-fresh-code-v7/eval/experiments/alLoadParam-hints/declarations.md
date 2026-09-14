ASSISTED DEVELOPMENT: supervisor-authored declaration/edit checklist, no reference
function body. Apply this together with the header field map. This is a repair of
the original 87-line draft, not of any previous rejected proposal.

Critical: L9 currently starts with void, but the public header requires s32.
Replace only that opening line with:
s32 alLoadParam(void *filter, s32 paramID, void *param) {
    ALLoadFilter *f = (ALLoadFilter *)filter;
    ALWaveTable *p = (ALWaveTable *)param;

Do NOT insert all old temporaries into L9. They already have their own slots.
Change their existing declarations in place:
L12 -> ALADPCMBook *temp_a0;
L13 -> ALWaveTable *temp_v0_2;
L14 -> ALWaveTable *temp_v0_3;
L15 -> ALWaveTable *temp_v0_4;
L16 -> ALWaveTable *temp_v0_5;
L17 -> ALADPCMloop *temp_v1;
L18 -> ALRawLoop *temp_v1_2;
L19 -> ALADPCMloop *temp_v1_4;
L20 -> ALRawLoop *temp_v1_5;
Leave existing u8 temporaries L10 and L11 alone. Each variable declared once.

ALLoadFilter has NO direct handler field. Both callback assignments MUST use
f->filter.handler, because the ALFilter base is nested at field filter.
WaveTable length is len, not state. Retain signed division by 9 then multiply9.
ADPCM loop state is temp_v0_3->waveInfo.adpcmWave.loop->state.

Return handling is NOT solved by this type transaction. For this experiment,
keep the bare return statements while correcting all the types. A subsequent
compiler failure specifically about return values is useful progress. Do not
abandon the type edits or add bogus constants to conceal that remaining issue.
