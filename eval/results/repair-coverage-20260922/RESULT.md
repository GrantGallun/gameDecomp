# Repair coverage results — 2026-09-22

Seven additional functions now have independently verified object-exact attempts
in the main inventory. `python -m eval.status` changed from 348 to 355 object-exact
functions, and SOLVED from 256 to 263. Header-assisted remains 12,
reference-type-assisted 25, recovered 55. Main receipts are 95857–95863.
These are isolated compiler/frontend/object results, not integrated game TUs or
a whole-ROM build. No reference function bodies were used.

## What changed

The main gap on this panel was repair coverage and routing. The project already
had closed wide-arithmetic reconstruction in compile recovery, but the tool
registry and regalloc-only experiments did not expose it. `reconstruct-wide`
now offers that operation to the scripted policy, including compiling nonexact
drafts. Five successes reuse existing reconstruction capability.

Two new reconstruction recipes cover entire target instruction streams:

- Quotient/remainder stores through two pointers, with a 64-bit value and trailing
  halfword divisor. The old split-word signature placed the divisor incorrectly.
- Signed remainder adjusted when its sign disagrees with the divisor's sign.
  Whole-operation reconstruction replaces the malformed split-word draft.

Both require a matching whole instruction stream, a big-endian o32 ELF target,
and the configured `-mips3 -32` compiler recipe. Function names do not select the
operation. The controller compiles every proposal; no new runtime/callee
admission was added. A regression test exercises each motivating stream and
negative cases change offsets, arithmetic, branches, traps, and ABI.

## Measured comparison

Each arm has a ceiling of 40 compiler calls including its baseline. The narrow
regalloc-only arm exhausts its available proposals early; unused calls are not
charged. The wired arm performs one reconstruction action after baseline.

| Function | Regalloc-only best score | Regalloc calls | Wired calls | Wired result | Coverage |
|---|---:|---:|---:|---|---|
| `__ll_rem` | 66.188 | 1 | 2 | Object-exact | Existing recipe exposed |
| `__ull_divremi` | 79.370 | 2 | 2 | Object-exact | New recipe |
| `__ll_mod` | Did not compile | 1 | 2 | Object-exact | New recipe |
| `__ull_rem` | 66.188 | 1 | 2 | Object-exact | Existing recipe exposed |
| `__ull_div` | 66.188 | 1 | 2 | Object-exact | Existing recipe exposed |
| `__ll_mul` | Did not compile | 1 | 2 | Object-exact | Separate route check |
| `__ull_rshift` | Did not compile | 1 | 2 | Object-exact | Separate route check |
| **Total** | **0/7 exact** | **8** | **14** | **7/7 exact** | **2 new, 5 reused** |

This is a small exposed development panel, not a full-project baseline or proof
of general RSI/training improvement. The two separate functions were selected
after freezing the recipes and exercise existing recipe routing, not transfer of
the two new recipes to unseen instruction shapes. All attempts are explicitly
training-ineligible; no model was trained.

## Source handoff repair and provenance

The first live comparison reached exact internally, but its independent check
compiled the old source. `run_episode` synchronized `context.candidate` only
before the next decision, so immediate success or budget exhaustion could export
the parent. It also stamped the adopted verdict with the parent's source hash.
Both are fixed; regressions cover exact stops, budget stops, callback state,
and source/verdict hash agreement.

The original reports and failed confirmation remain in this directory as failure
evidence. Only `verified/verification.json` and its source files support the final
comparison. That corrected run checks callback output, final context, transcript
hash, actual compiled source hash, and independent confirmation together.

`verified/verify-used.py` archives the exact measured harness and matches the
manifest's original script hash. Subsequent review added `logged_compile.py` to
preserve timeout/exception receipts in confirmation and inventory work. This
harness-only change is covered by a real SQLite failure/lineage regression and
is not retroactively described as part of the measured comparison. Production
implementation hashes still match the frozen manifest.

## Cost and checks

| Phase | Compiler calls |
|---|---:|
| Assembly-led exploratory probe | 21 |
| Original comparison | 22 |
| Failed stale-source confirmation | 1 |
| Corrected comparison | 22 |
| Independent confirmations | 7 |
| Main inventory confirmations | 7 |
| **Total** | **80** |

All costs include failures and baseline compiles. Every compile has a receipt;
known parent relationships are explicit. The legacy regalloc callback does not
provide parent identities, so those attempts are labeled unknown lineage rather
than assigned invented parents. The read-only receipt audit validates all saved
source hashes, exact certificates, explicit edges, and complete costs.

The focused Windows and WSL regression suite contains 141 passing tests. A
broader WSL run had 167 passes and three preexisting failures in
`test_compile_recovery_reapplies_absolute_adapter_to_later_drafts`,
`test_the_stage_is_present_and_appends`, and
`test_the_stage_runs_before_the_strategies_it_unblocks`; all concern the existing
placeholder-recovery stage, not this change. Read-only code review found no
remaining Critical or Important issue after the logging fix.

Artifacts: [comparison](verified/verification.json),
[inventory receipts](inventory-receipts.json), [ratchet](inventory-ratchet.json),
[status before](status-before.md), [status after](status-after.md),
[receipt audit](audit.json).

The result supports expanding verified operation reconstruction and ensuring
the policy can call it before further scheduler evolution or adapter training.
Small syntactic mutations cannot repair a missing or wrongly represented ABI
operation reliably; a better search policy alone does not supply that operation.
