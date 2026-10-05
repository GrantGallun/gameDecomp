# uopt's own allocation trace: unblocked, gated, and measured against the model

2026-09-14. Tools: `tools/ido-trace/` (patched uopt), `solver/uopt_trace.py` (parser and
check, tests: `tests/test_uopt_trace.py`), `eval/uopt_trace_census.py` (corpus run).

## Why

HYP-20260912-01: register choice cannot be recovered from finished assembly. The
callback investigation (`../callback-allocation-backend-v1/REPORT.md`) reached uopt's
level-6 trace and lost it to a crash in stock ido-static-recomp's unimplemented `ecvt`,
leaving priorities "unknown". Both level 5 and level 6 crash at the first float.

## The unblock is safe

See `tools/ido-trace/README.md`. The ROM built with the patched uopt is the stock ROM,
and all 386 trace-mode recompiles (193 game C files x levels 5 and 6, with the build's
own `.mdebug` removal) are byte-identical to the build's objects.

## What the trace says about the callback residual

`callback/baseline.zdbug{5,6}.txt` is the campaign candidate (98.511). Colour k is
`v0 v1 a0 a1 a2 a3 t0...` for k = 1, 2, 3...

| value | node | live range | colour | adjsave | forbidden when coloured |
|---|---|---|---|---|---|
| pool index local | {848} M -14 | 58 | 2 (v1) | 2.0 | 1 3 4 5 |
| insertAfter | {944} M -8 | 61 | 6 (a3) | 8.33 | 1-5 |
| newTask | {1008} M -4 | 64 | 7 (t0) | 3.33 | 1-6 |

Only 76, 2 and 90 (the parameters) are constrained, coloured by descending adjsave.
Everything else is unconstrained and coloured lowest-free in live-range number order,
whatever its priority. The target keeps newTask in v1 and never makes the index a
variable: it is `andi t9,t8,0xffff` on the decremented count, a ugen temporary. So the
target needs (a) no index range and (b) newTask's range numbered before insertAfter's.
Range numbers follow the first store in u-code order.

The prediction was tested before it was believed (`callback/probe.jsonl`, `regalloc_probe`):

| variant | exact | score | gradient | signatures |
|---|---|---|---|---|
| baseline | no | 98.511 | 3, 31, 37 | temp_numbering 23, temp_vs_variable 14 |
| v1 index inlined | no | 97.411 | 3, 55, 68 | temp_numbering 49, variable_colour 19 |
| **v2 index inlined, newTask stored first** | no | 98.156 | **2**, 44, 49 | **temp_numbering 49 only** |
| v3 pre-decrement in index | no | 96.773 | 5, 36, 45 | mixed |
| v4 pre-decrement, newTask first | no | 96.773 | 5, 36, 45 | mixed |

v2's trace (`callback/v2_inline_newtask_first.zdbug*.txt`) shows the mechanism, not just
the outcome: newTask {1008} becomes range 58 and takes colour 2 (v1); insertAfter {944}
becomes range 64 and takes colour 6 (a3), matching the target. Every variable-colour
difference is gone. The remaining residual is ugen temporary numbering, a different
stage, and it grew (23 -> 49); the byte score fell. **Not a match, and not claimed as
progress on the function** — it is a confirmed explanation of the uopt half.

## Corpus: does the colouring model hold? (`census-summary.json`, `census-functions.jsonl.gz`)

115 traced TUs (the 78 others are libultra at `-O1`, where uopt does not run), 1,976
procedures, 11,310 colouring decisions.

| rule | kind | result |
|---|---|---|
| level-6 colour equals level-5 colour | trace self-consistency | 11,310 / 11,310 |
| `forbidden` = colours of neighbours coloured earlier | observed semantics | 0 violations |
| unconstrained ranges coloured in increasing range number | model | 0 violations |
| constrained ranges in non-increasing adjsave | model | 1,907 / 1,976 procedures; 95 items |
| colour = lowest not forbidden, bands 1-13 / 14+, parameters may keep a0-a3 | model | 1,397 / 1,976 procedures; 781 items |

Explained so far, not yet modelled:

- **Order, 95 items.** 17 are a float range after an integer one: splitting classes at
  colour 23 removes them, so integer and float ranges are ordered separately. Of the 78
  left, 44 involve a split piece (split ranges re-enter with new priority). **34 are
  unexplained.** One candidate: level 5 prints adjsave after colouring, not at selection.
- **Lowest-free, 781 items.** Dominated by a0-a3 chosen while v0 was free (colour 5
  over 1: 211; 3 over 1: 126; 4 over 1: 105), including parameters moving to a
  different argument register and expression ranges: argument-register preference
  beyond incoming parameters. Colours 24-33 are floating-point registers, which a band
  starting at 14 misdescribes (104 items).

## Follow-up: the colour-selection model (same day)

The 781 lowest-free exceptions were not noise. They share one feature: the last column
of a range's per-block row (`- live bb -  B  x  y  P`). P was 0 for all 3,518 decisions
that took the lowest colour from v0, and 3-5 for 1,110 of the 1,158 that scanned from a0.
`solver.uopt_trace.select_colour` now encodes, per band (integer caller 1-13, integer
callee 14-23, float caller 24-29, float callee 30+):

1. a preferred colour P inside the band: the first one not forbidden, or scan upward from
   the first when all are forbidden;
2. otherwise a parameter live in block 0 scans upward from the band's argument colour
   (a0 = 3, float 26), not from its own incoming register;
3. otherwise the lowest colour not forbidden.

Each rule was chosen over a scored alternative (`select_variants.py` reproduces the table
from the census traces; the tests pin each rule on the shape it was fitted to):

| variant, integer caller band (8,757 decisions) | correct |
|---|---|
| lowest free (the refuted rule) | 7,589 |
| preference column, no parameter rule | 8,707 |
| **model** | **8,754** |
| model, but only the first preference is tried | 8,754 (no case distinguishes them) |
| model, but every parameter scans from a0 | 8,716 |
| model, preferences outside the band also honoured | 8,752 |

In-sample on the SBK1 census after rescoring (`census-summary.json`): **10 misses in
11,310 decisions**, in 4 procedures: 3 integer-caller, and 7 integer-callee in the
libultra audio functions `_pullSubFrame`, `alEnvmixerPull`, `alAdpcmPull`. Both callback
traces are now fully consistent (24/24 and 23/23).

**Held out.** The model was fitted on reference-decomp source only. It was then run
unchanged on campaign candidates: one m2c-bannered source per workspace (which excludes
copied reference source), seeded sample, 750 eligible (`heldout-sources.json`), traced with
each workspace's recorded compile command (`eval.uopt_trace_census --candidates`). All
compiled; plain, level-5 and level-6 objects were identical for every one.

| held out: 699 procedures, 2,375 decisions | correct |
|---|---|
| lowest free | 1,482 / 1,956 integer caller |
| **model, integer caller** | **1,956 / 1,956** |
| **model, integer callee** | **397 / 397** |
| **model, float** | **22 / 22** |
| constrained adjsave order | 685 / 699 procedures (14 exceptions) |

Caveat: candidate procedures are smaller than the census average (3.4 decisions against
5.7), so this is a narrower test than the census, not a second census. `heldout-summary.json`, `heldout-functions.jsonl.gz`.

The band is still read from the chosen colour: nothing printed says whether a range must
survive a call. The meaning of P (plausibly the argument slot the value is passed in, per
block) is an interpretation, not tested.

## Follow-up 2: the band (whether a range is callee-saved)

The selection model reads the band from the colour uopt chose. Predicting it needs to know
where the calls are, and the uopt trace never marks a call (no `-zdbug` level or other `-z`
option does). `solver/uopt_calls.py` gets them from ugen's tree dump of the same compile:

- **Mapping.** uopt ends a block after every call, has empty nodes the re-emitted u-code lacks,
  and prints its flow graph out of program order. Walking both graphs from the entry, with
  labels as anchors and empty nodes as pass-throughs, maps 1,788 of 1,967 SBK1 procedures
  and 686 of 699 held-out ones, with no call left in an unmatched block. It declines rather
  than guesses.
- **`-d` is not code-neutral.** It renumbered ugen temporaries in 27 of 193 TUs, so the dump
  is taken in its own compile and that object is never used (`--ugen` in the census).
- **A correction.** HYP-20260912-01 said every call-crossing value is in s0-s8. Its replay only
  ever marked non-caller-saved webs as crossing, so that was true by construction.

Measured with uopt's own ranges, crossing a call is not sufficient: 532 caller-saved ranges
are live into and out of a call node, against 1,788 callee-saved. Every one of them had a
callee-saved colour free, so it is a choice. The loop-weighted number of calls crossed
(10 ** (loopdepth - 1) per call node) separates the bands almost monotonically, and
parameters switch earlier (weight 2-3) than locals and constants (3-10). An already-open
callee register tips a few more. No exact rule fits; the residue is a cost trade-off whose
terms the trace does not print.

`uopt_calls.predict_band`: callee-saved when the loop-weighted crossing weight is >= 3.
A per-kind threshold gained 9 decisions in-sample and was not adopted.

| integer decisions in mapped procedures | SBK1 (in-sample) | held out (750 candidates) |
|---|---|---|
| always caller-saved | 6,732 / 8,712 (77.3%) | 1,823 / 2,218 (82.2%) |
| **band predicted** | **8,227 (94.4%)** | **2,117 (95.4%)** |
| callee-saved recall / precision | 80.4% / 94.3% | 82.0% / 91.5% |
| exact colour, predicted band + selection model | 8,218 (94.3%) | 2,117 (95.4%) |

Band errors are nearly all of the end-to-end error. This still needs a compile: the forbidden
sets and the crossing come from the trace and the dump. `band-census-summary.json`,
`band-heldout-summary.json` (same 750 sources as `heldout-sources.json`).

## What this changes

- The trace is the ground truth for uopt decisions on any candidate source; nothing
  about allocation needs to be inferred from assembly again.
- `patterns/hypotheses.json` `uopt-ordering-law-reproduces` (79%, from post-allocation
  reconstruction) is superseded by `uopt-trace-ordering-law`, measured here.
- Nothing consumes the trace automatically yet. It is diagnostic, like `uopt_replay`,
  and writes nothing to the knowledge base.
