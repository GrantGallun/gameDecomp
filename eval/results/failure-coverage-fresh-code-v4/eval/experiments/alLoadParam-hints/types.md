ASSISTED DEVELOPMENT EXPERIMENT. These hints were prepared by a supervising
agent from the target instructions and existing included headers, not a reference
function body. They are hypotheses to verify with the compiler, not KB facts.

The type roles in this particular draft are:
- Keep public parameter types void*, s32, void* and header return type s32.
- The filter object is ALLoadFilter, NOT ALFilter. ALFilter is only its first
  embedded member. Introduce ALLoadFilter *f = (ALLoadFilter *)filter as a local.
- param in paramID == 5 is ALWaveTable*, NOT Acmd*. Acmd is an audio command,
  not the waveform being loaded. Introduce ALWaveTable *p = (ALWaveTable *)param.
- temp_v0_2, temp_v0_3, temp_v0_4, temp_v0_5 are ALWaveTable*.
- temp_a0 is ALADPCMBook*.
- temp_v1 and temp_v1_4 are ALADPCMloop*.
- temp_v1_2 and temp_v1_5 are ALRawLoop*.
- All these types already exist in the included headers. Do not define typedefs.

The o32 layout hypothesis, to be checked against compilation and target loads:
filter->unk4  = f->filter.handler
filter->unk18 = f->lstate
filter->unk1C = f->loop.start
filter->unk20 = f->loop.end
filter->unk24 = f->loop.count
filter->unk28 = f->table
filter->unk2C = f->bookSize
filter->unk38 = f->sample
filter->unk3C = f->lastsam
filter->unk40 = f->first
filter->unk44 = f->memin

For an ALWaveTable*: unk0 = base, unk4 = len, unk8 = type.
The base pointer is stored as the s32 memin field, retaining the explicit cast.
For a type-0 waveform: unkC = waveInfo.adpcmWave.loop,
                      unk10 = waveInfo.adpcmWave.book.
For a type-1 waveform: unkC = waveInfo.rawWave.loop.
The same byte offset 0xC has different union views according to the branch.
For ALADPCMBook*: unk0 = order, unk4 = npredictors.
For either loop pointer: unk0 = start, unk4 = end, unk8 = count.
The ADPCM byte address loop + 0xC is loop->state. Use its array decay as the
alCopy source, and f->lstate as destination. Copy 0x20 bytes, NOT 12 loop objects.

Apply the connected type hypothesis to every affected declaration AND access in
one transaction. Preserve the if/switch/case lines exactly (apart from typed
expressions). A line slot replaces the entire physical line. Do not shift the
slot numbers while constructing simultaneous edits. Do not redefine header types
or use macros to hide accesses. Keep the public ABI.

Return caveat: the target's exit does not establish one common constant v0.
Do not claim that return 0 reproduces it. First resolve the type graph; distinguish
remaining return diagnostics from incorrect member access diagnostics. Return
semantics must be audited separately against target paths, not invented.
