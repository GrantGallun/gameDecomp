# Protocol: which local-type change fixes each width residual, localized by the compiler's own line records

Written 2026-09-24 before `linemine.py` existed. Follows `draft-reference-mining-20260924` (same split, same pairs)
and `scope.py` there: in 319 of 504 pairs the draft's and reference's primitive local types differ, 135 of them
with a width residual. The first pass had no type features and no localization.

## Data
Mining pairs from `draft-reference-mining-20260924/pairs.py` (the scored population and sealed sets excluded,
reference kept on the WSL side), usable, with a residual. Each pair's `ref.c` and `draft.c` are recompiled with the
pipeline's own line capture (`solver.source_attribution.prepare`, the recipe with the pre-strip object kept) and
read with `source_attribution.parse_dump`. Instruction k of a normalized dump is line record k; a pair whose
record count is below its normalized count is dropped and counted.

## Events
Target (= reference, exact modulo relocation names) and draft instruction lists, relocations masked, aligned with
difflib `SequenceMatcher` (autojunk off). A **width instruction** has an opcode in {sll, sra, srl, andi, lb, lbu,
lh, lhu, sb, sh}. Each non-equal alignment step involving one gives an event:
- `missing:<op>` (target only; draft line = that of the nearest preceding aligned draft instruction),
- `extra:<op>` (draft only; reference line = that of the nearest preceding target instruction),
- `opcode:<t>/<d>` (a replaced pair with different opcodes), `field:<op>` (same opcode, different operands).
On the event's draft line and reference line, the **variables** are the identifiers declared as a local or a
parameter with a primitive type (s8 u8 s16 u16 s32 u32 int char short and their unsigned and signed forms; pointers,
floats and aggregates excluded). A **pairing** is made when each side has exactly one such variable on its line,
or when both lines assign to one (the left-hand side is paired). Otherwise the event is counted as `unpaired`,
never dropped silently.
A paired event records (kind, draft type, reference type, local or parameter).

## Counting
Clone families collapsed as in `dedup.py` (target length, draft shape, reference shape). An event counts once per
(clone family, kind, draft type, reference type).

## Rule candidates
A cell (kind, draft type) with n >= 8 deduplicated paired events in which one reference type accounts for at least
60% is a **rule candidate** "draft T_d at this residual -> declare T_r". The reported **type-change rate** per kind
is the share of paired events whose types differ (a low rate means the width fault is not a local-type fault).

## Positive control (must fire)
H13 (`mask-type-20260923`, partial rule): a u8 or u16 local assigned an int-valued call result emits an `andi` that
s32 does not. So at least one of these cells must be a rule candidate: (`extra:andi`, u8 or u16) -> a wider or
signed type, or (`missing:andi`, s32 or int) -> u8 or u16. If both cells have n < 8, the control is untestable
and says so. If they have support and neither qualifies, the instrument is suspect: fix the instrument before
reading any rule.

## Reading
- Rule candidates are hypotheses. Each goes through paired synthetic compiles (a protocol of its own), then a
  mechanism gated on its residual with a fire test, then the paired population rerun.
- Reach: for each rule candidate, the number of unsolved population functions whose best node carries its
  residual kind on a line whose paired variable has the draft type. Measured after the rules, with the same code.
- Also reported: events per kind, pairing rate, type-change rate per kind, and the most common transitions
  overall.
