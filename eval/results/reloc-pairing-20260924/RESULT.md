# Result: 30 campaign "near misses" are byte-identical and blocked by relocation-record order, which the reference's own source cannot pass either

## Finding
In the campaign ledger (`runs/resume-pipeline-20260908/campaign.sqlite`), 30 functions have a best attempt with score
100, identical `.text` bytes and identical relocation targets, whose object certificate fails with
`object_sections_differ` / `relocation_order_equivalent: false` (7 of them also fail the frontend gate). The binary-types
runs hit the same class (15 functions, overlapping).
The difference is only the ORDER of HI16/LO16 records: for two `lui`/`%lo` pairs of the same symbol, the target lists
(HI@4, LO@8), (HI@0, LO@0xC); every candidate lists (HI@0, LO@8), (HI@4, LO@0xC).

## Tests (`probe.py`; osCreateMesgQueue, recorded recipe -O1 -mips2)
- Six declaration forms of the global (byte array, complete/incomplete struct with `&`, pointer-typed fields, `s32`
  scalar): identical relocation order, never the target's. The declaration is not the lever.
- **Harness check with the reference's own definition compiled in its own translation unit: score 100 and the SAME
  relocation order as our candidate, not the target's.** The certificate would reject the reference decomp's own
  source for this function.

## Reading
The target object's record order is not reproducible by any C in this harness. The likely reason: the workspace
`target.o` is assembled from `target.s` by GNU as, which places each HI16 record immediately before its matching LO16;
IDO's assembler emits them in instruction order. That is untested; the fact above does not depend on it. When every HI16
and LO16 in a group names the same symbol with the same addend, any pairing links to identical bytes, so these are
ROM-equivalent.

## Proposal (not implemented; changes what counts as exact, so it is the operator's decision)
In the certificate's relocation comparison, treat two tables as equivalent when they differ only by HI16/LO16 pairing
within groups where every HI16 and LO16 names the same symbol with the same addend. It needs tests: accepts this case
(osCreateMesgQueue candidate and reference), rejects different addends or symbols. Then re-certify the 30 (and the
binary-types 15) through the campaign.

## Implemented (2026-09-24, operator approved)
`solver/byte_certificate.py`: `same_addend_pairing_groups`, `section_contents`, `pairing_equivalent`, applied in
`certify` as a SECOND stage after strict equality fails. Receipts carry `relocation_pairing_normalized`. Strict
`independent_relocation_groups` and its test ("the same symbol/entry multiset cannot authorize different pairs")
are unchanged: the new stage reads the instruction immediates and relaxes pairing only when every pair of a symbol
has identical immediates and the section bytes are identical. Tests: `tests/test_byte_certificate.py` (accepts the
osCreateMesgQueue shape; declines different immediates, different symbols, different bytes); 83 verifier-related tests
pass.
Real objects (`recertify.py` -> `recertify.json`, main-tree certificate):
- **the reference's own osCreateMesgQueue in its TU: now `object_sections_exact`** (it fires on the motivating case);
- campaign 30: 6 exact through the new stage in this harness; 24 differ HERE (29 of the failing rows are `.text`
  bytes), because their sources depend on the campaign's build context, which this copied workspace does not
  reproduce. They need re-certification inside the campaign;
- binary-types 15: 5 exact through the new stage; the rest differ in bytes or `.rodata` symbols (section-relative vs
  named data), which are other classes.
Nothing is recorded yet: the official recording path runs a frozen code root that predates this change, and the
campaign runs its own snapshot, so both need the redeploy that the integration work includes.
