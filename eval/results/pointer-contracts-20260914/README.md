# Overnight stagnation: causes and fixes (2026-09-14)

## What the campaign did

`census-deploy-20260914/overnight.py` covers the period since amendment 3, checkpoint 17289 → 18241.
- **Exact or integrated:** 855 → 888. Pending integration: 10 → 20.
- **419 jobs:** 326 were ±0 (78%), 67 improved, 26 exact.
- **Model calls:** 483 of 514 went to ±0 jobs (94%).

| profile | jobs | exact | improved | ±0 |
|---|---|---|---|---|
| regalloc_search | 53 | 22 | 8 | 23 |
| structural_rewrites | 138 | 1 | 36 | 101 |
| semantic_counterexample | 86 | 1 | 2 | 83 |
| semantic_alternative | 64 | 1 | 5 | 58 |
| recertify | 30 | (promoted 10 to function-exact) | – | 30 |
| address_symbols | 23 | 0 | 14 | 9 |

- **Semantic-lane jobs:** 200 jobs, 185 ±0, about 2.5 worker-hours.
- **The campaign was stalled for its last ~80 minutes.**

## Cause 1: a second backtracking generator (fixed, `regalloc-hotfix2-20260914/`)

- **What happened:** `guard_before_load`'s guard-body pattern `(?:(?P=i)[ \t]+[^\n]*\n)*?` backtracked exponentially. A
  worker spun on osEPiRawStartDma for over an hour, and the drain could not complete.
- **The audit:** `spin_audit.py` ran every source-only generator family on all 1070 pending sources with a 3 s alarm.
  Live code spun on 24 sources, all in `guard_before_load`. The fixed code spins on none, and no family is slow.
- **Deploy:** the spinning pool process was killed. The stranded job, which had no result file, re-ran under the fixed
  code through the controller's recovery path.
- **Also stopped:** my own offline census search, which had been spinning for 5.5 hours.

## Cause 2: false differential failures from frame layout (fixed, this directory)

`divergence_kinds.py` over 263 semantic-lane nodes:

| first divergence | nodes |
|---|---|
| final memory | 75 |
| argument value | 73 |
| **stack offset only** | **66** |
| entry memory | 28 |
| other or mixed | 21 |

- **The mechanism:** an opaque callee's identity includes its argument labels, and a local buffer is labelled by
  address (`stack+0xfd4`). Frame layout alone therefore failed the call, and the synthetic returns derived from those
  labels cascaded.
- **Dominant callees:**
  - makeFixedRotationY: 14 nodes
  - makeFixedRotationXY: 11 nodes
  - transformVec3iByFixedMatrix: 10 nodes
  - sprintf: 8 nodes
  - allocFixedTransformMatrix: 4 nodes
- **The fix, `solver/pointer_contracts.py`:** from each opaque callee's ROM-verified extracted instructions (annotations
  equal ROM bytes; the text is reassembled with call targets and `%hi/%lo` masked), it derives the bytes read and
  written through each pointer argument.
  - Stack reads are labelled by a content digest; stack writes by extent, with a payload derived from the other labels.
  - Per-argument disqualification covers value use, escape into calls, memory or returns, negative offsets, and
    conditional stores.
  - Loops, indirect jumps and stack-pointer rewrites decline the whole callee.
  - `eval/semantic_lane.Panel` adds these contracts to its callee environment.
- **Paired probe** (`census-deploy-20260914/pointer-contract-probe-*.json`), on 30 stack-offset-only nodes and 10 passing
  controls:
  - 6 failures became 64/64 passes, and 6 became inconclusive (remaining uncontracted stack arguments);
  - no passing control changed;
  - three nodes still fail, now on a genuine content difference (`stack-read[12]` digests differ);
  - the remaining false failures are loop-based callees: sprintf, drawMenuGlyphScript, osPfsDeleteFile.
- **Validation:** `validate.py` exercises the real panel path.

## Cause 3: stale verdicts on nodes with no remaining work (fixed)

- **The problem:** a node's stored differential failure only refreshes when a job runs on it. Semantic nodes that had
  used both semantic profiles for their evidence key got no further work.
- **The fix:** `revalidate@<semantic environment digest>` is a zero-model re-score, once per source whenever the
  environment code changes. Failing nodes get band -1.
- **Validation:** `validate_revalidate.py` turned two stale failures into observed passes, and they moved to the byte
  lane.
- **On deploy:** 285 revalidate jobs were queued, and stalled nodes fell from 240 to 107.

## Measured, not a mode

- **Rodata float literals vs ROM values** (`census-deploy-20260914/float_literal_census.py`): 34 functions with
  recognisable rodata float loads. 17 match; 17 have count mismatches, mostly `(float)0x…` integer-cast artifacts.

## Still open

- **Loop-based opaque callees receiving stack buffers:** sprintf and glyph/text drawers.
- **Final-memory and argument-value divergences** (148 nodes): mostly genuine source differences, where the
  counterexamples are real.
