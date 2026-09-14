# Assisted alLoadParam experiment — 2026-09-05

Outcome: the last of the original seven compile failures now has an IDO-compiling,
frontend-passing candidate, with **580/580 differential cases passing**. It is
**not byte-exact**, not an autonomous solve, and not integrated into the game.

Selected source: `eval/results/alLoadParam-hints-selected-v1.best.c`.
Selected receipt: `eval/results/alLoadParam-hints-selected-v1.json`.
Source attempt 29647, fresh selected reverify 29649, SHA256
`85c428028fee01065d078e3f7fa85e34521f28fbcb11c95be14b14b31938a3e5`.
The weighted score is 78.621, **not a percentage of equal bytes**. The residual
reports 250 positional equal bytes and distance 230 over target text size 480;
candidate text size 464. These include object padding/position effects, not a
claim that 230 independent source fixes remain. No new object-exact match.

## Provenance and interventions

The intact original draft is attempt 29535, SHA256
`d543725ea1e7bb616169e01673d15293a0cc64e97f42ed6ccc42d12358380c2b`.
The prior v11 replay root 29625 was already damaged (stray typedef fragments,
missing switch). It was not an appropriate root for this hint comparison.

No official implementation body was read or supplied. Inputs were the target
assembly, the original draft, existing headers, and an explicitly inspected
metadata-only projection of the target TU's includes/signature/diagnostic macros.
The header information and supervisor-written scaffold make this an assisted
development experiment; do not promote it to heldout or binary-only capability.

The supervisor supplied the type-role map in `types.md`. The header-only compiler
probe `layout_probe.c` checked 26 offsets plus the 32-byte ADPCM state size;
`layout_probe.frontend.json` records its pass. This confirms the supplied header
layout, not automatic type inference from the binary.

Metadata inspection found `compiler_diagnostics.h`, with a push and
`CLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE` immediately before the reference signature
(load.c:348–350), and pop at line 425. The target does not define a common v0
return value on all paths. `scaffold.c` restores that **existing local policy**,
the header signature, and typed declarations; all original unknown member-access
statements remain for OSS to repair. No checker flag was disabled globally.

This exposed a real guard bug: `type_transaction.signature` included the
standalone diagnostic macros as part of the return type and rejected unchanged
signatures. The fix recognizes those three specific decorators. Regression tests
still reject changed return/parameter types and retain unknown declaration tokens.

## What the trials actually showed

All model calls used local gpt-oss:20b, low thinking, temperature .35, base seed
20260905, and 10,000 output-token cap. Paired arms held root, seed schedule,
headers, edit protocol, and maximum calls fixed. Single-seed observations do not
establish reliability across models/functions. Different stages are NOT one
controlled A/B: their scaffolding and inputs change explicitly.

| Trial / receipt suffix (`alLoadParam-hints-…json`) | Calls | Result |
|---|---:|---|
| baseline-v1 (root 29535) | 2 | Both ABI-invalid; invented structs / ALFilter-Acmd confusion |
| types-v1 (same root) | 2 | Chose useful ALLoadFilter/wave fields, but both ABI-invalid; retained void return and bad declarations |
| checklist-v1 (same root) | 2 | Applied signature/declaration edits, no compile; fixed line-number advice became stale after first edit |
| scaffold-v1 (root 29632) | 3 | Rejected by our diagnostic-decorator ABI parser bug |
| scaffold-v2 (same root, corrected guard) | 5 | Repaired all member types; best child 29636 passed frontend but failed IDO on two C89 declarations |
| c89-control-v1 (root 29636, no extra hint) | 2 | Compiled by replacing input-dependent selectors with zero; score 48.397, only 52/580 differential passes |
| c89-v1 (same root, c89.md) | 1 of 2 | Correct two assignment edits; score 80.259, 532/580 passes |
| existing c89.to_c89 (same root, attempt 29640) | 0 | Compiled, frontend passed, score 80.259; a pre-existing deterministic mechanism handles this fault |
| return-v1 (root 29639, raw-return.md) | 2 | First child 29647 fixed v0 on raw-wave exits; 580/580 passes, score 78.621 |

The final C89 paired comparison is more informative than compilation rate: both
arms compiled, but the no-hint arm destroyed the selector dataflow. The focused
hint identified the exact erroneous construct and the minimal permitted change.
It prevented a bogus “fix” based on deleting assignments and initializing zeros.

The type map helped select the right object roles, but was insufficient alone.
Do not attribute success solely to more context: typed scaffolding, correct
diagnostic context, the guard fix, compiler-specific syntax explanation, and
branch-local differential evidence each played a distinct role. The initial
checklist also demonstrated that hardcoded physical slot hints must not survive
source revisions unchanged.

## Semantic and byte audit

`audit.py` supplies 580 concrete cases from target branches/header offsets:
four seeds, load/reset/default param IDs, waveform types 0/1/2, null/non-null
loops, six boundary lengths, and null reset tables. Target execution covered all
109 modeled reachable instructions and 24 feasible conditional outcomes; both
impossible division guards were explicitly excluded. Candidate coverage was
also complete in this model. These are not all possible values or alias layouts.

- `alLoadParam-hints-semantic-control-v1.json`: 52 pass, 528 fail.
- `alLoadParam-hints-semantic-v2.json`: 532 pass, 48 fail; final memory equal on
  all 580. Every failing comparison reports v0 on the raw-wave load branch.
- `alLoadParam-hints-semantic-return-v1.json`: 580 pass, zero fail, including v0.

`alCopy` is opaque: arguments and call-time memory are checked, its implementation
and side effects are not executed. Finite coverage is not universal equivalence;
legacy unspecified C returns and untested aliasing remain qualifications. No
semantic gate was relaxed to obtain the 580 passes.

The raw-return hint came directly from a counterexample: target v0 was the table
at 0x12000000; candidate v0 was its loop at 0x12000100 or zero. OSS changed only
the two raw-case return statements to return the table pointer as s32.

**Ranking gap:** that correct behavioral repair lowered weighted byte score
80.259 → 78.621. The standalone byte-ranked controller retained the old source
as `best`, although its frontier/DB retained the better-behaved child. Therefore
the selected receipt explicitly re-verifies 29647; it does not pretend the
byte-ranked receipt selected it. This is evidence for separate semantic and
exactness bests, not a reason to relax byte verification.

## What changed in the pipeline, and what did not

Implemented: the narrow diagnostic-decorator ABI parsing fix and regression test;
the reproducible assisted trial driver; hint artifacts; the targeted audit.
Full suite: **1,128 passed, 9 skipped**.

Not wired by this experiment: automatic source-policy metadata projection,
automatic typed scaffold recovery, C89 normalization inside `modelrepair.search`,
or a semantic-prioritized retention policy for that controller. The C89 module
already exists and is used in other controllers; reconnect it rather than build
a second normalizer. Semantic/exactness stage separation likewise exists elsewhere
and needs consistent routing/retention, not a claim that no such machinery exists.

The v11 campaign checkpoint remains immutable and unchanged. All seven original
failures now have compiling candidates available, but the last one is explicitly
supervisor-assisted and not yet adopted into a new campaign. No game source,
headers, ROM, evidence rows, inference rows, or SOLVED status was modified.

Example reproducible final-stage trial (WSL, from workbench):

```sh
python3 -m eval.assisted_hint_trial --repo /home/grant/decomp/sbk1 \
  --db /home/grant/decomp/kb-sbk1.sqlite --function alLoadParam \
  --attempt-id 29636 --hint eval/experiments/alLoadParam-hints/c89.md \
  --out eval/results/alLoadParam-c89-NEW.json --max-calls 2
```

New output names are required; do not overwrite the original receipts. Continued
campaign work requires a new code-pinned campaign and explicit assisted seed.
