# Stagnation round 2: the recovery path was looping on a class it never addressed

**Date:** 2026-09-19 · model calls 0 (analysis) · byte-exact unchanged at 333 · campaign left running

## The measurement that redirected the fix

The size table said *where* the campaign stops (870 work items, 0 exact above 1 KiB). It does not say
why, and a fix chosen from it is a guess. `eval/stagnation_report.py` reads the run's checkpoint and
classifies every pending node by its terminal residual:

| terminal class | n |
|---|---|
| **compiles-not-exact** | **987** |
| compiled=false-with-no-diagnostic | 61 |
| no-residual key at all | 38 |
| frontend-failed | 7 |

**987 of 1,093 pending functions already compile.** They fail on exactness, not compilation. That
reframes the zero-yield table: `compile_recovery`'s 511 work items were spent on a population of 68,
and the classes that produce nothing are not compile-blocked at all — they are matching failures.

## The 61, which the fix targets

They are not a mystery population. The roster is exactly the two classes measured earlier in this
session:

| class | functions |
|---|---|
| blank drafts (m2c produced nothing; `eval/m2c_redraft.py` regenerates 66 of 79) | `_Ldtob`, `_Printf`, `__osDevMgrMain`, `__osLeoInterrupt` |
| m2c's `?` type placeholder (122 drafts, 1,962 attempts on `Empty declaration specifiers`) | `__osCheckPackId`, `__osRepairPackId`, `__osPfsGetInitData`, `MusInitialize`, `MusStartSong` |

The campaign has made **1,587 `compile_recovery` visits** on pending nodes and no strategy in
`solver/compile_recovery.py` addressed either class: it tries header variants, globals, signed-word
parameters, tag definitions and byteview redrafts, all of which reason about diagnostics that the
parse error truncates.

## The fix

`solver/compile_recovery.variants` now offers the placeholder-resolved source as its FIRST stage,
before every strategy that reads `frontend.diagnostics` — because cfe stops at the `?` and truncates
the list they read. It appends a variant; it never substitutes, so a clean source produces nothing.

**Verified firing end to end**, not just asserted: the pre-existing recovery test's source contains
`extern ? D_1234;`, and the corrected assertion shows the stage producing

```
('m2c-type-placeholder', 'extern s32 D_1234;\nint f(void) { return (int)&D_1234; }\n')
```

alongside the absolute-symbol adapter's own variant, both offered for the object to choose between.

That test had asserted `rows[0]`, so it failed on the new stage purely as a property of ordering. It
now asserts membership and explicitly pins the placeholder variant — so it is a firing test rather
than an order-dependent one.

`tests/test_compile_recovery_placeholder.py` (4 tests) pins the wiring: the stage is present, it
appends, it runs **before** `local_call_interface.missing_pointer_interfaces` and
`wide_parameter_repair.propose` (the strategies whose diagnostics it unblocks), and a clean source
produces no variant.

## Suite

**3362 passed, 3 skipped, 1 failed** — the failure is the pre-existing environmental
`test_project64_trace::test_multi_job_validation_accepts_and_rejects`.

## What this does NOT yet do

The campaign is running the code staged at `amend3-20260919`, which **predates this edit**. Taking
effect needs another amendment (pause → stage → resume), and the amendment must be run with the pin
set computed *after* staging — the bug fixed this round. Until then the running campaign keeps looping
on the 61.

Also untouched: the ≥1 KiB wall (870 work items, 0 exact, ever). The classification above says those
functions compile, so that wall is a matching problem, not an admission one, and the same report
(`terminal_classes` = `compiles-not-exact` for 987) is the evidence base for attacking it next.
