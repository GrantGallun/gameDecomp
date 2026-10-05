# Both wirings, verified in their real callers

**Date:** 2026-09-17 · model calls 0 · byte-exact unchanged at 333

Two things were standalone scripts for a session. Both now run inside the machinery that produces the
numbers, and both were verified by firing them **through the caller**, not only in a unit test.

## Wiring 1 — the placeholder stage inside `zero_token_harvest.repair_chain`

`solver/m2c_placeholders.rewrite` is now the FIRST stage of `repair_chain`, before `byte-index`. The
order is the point: cfe stops at m2c's `?` and truncates its error list there, so every stage after it
was reasoning about a file whose real defects were invisible.

Verified end to end, not just unit-tested — `eval/header_admission.py` on two drafts that still carry a
placeholder:

```
__allocParam     harvest_stages: ["m2c-placeholder"]
                 residual: Selector requires struct/union pointer as left hand side (line 17)
__osSiRawReadIo  harvest_stages: ["m2c-placeholder"]
                 residual: Dereferenced a non-pointer (line 17)
```

That is the predicted behaviour and it is worth stating plainly: **the stage does not admit these
drafts, it upgrades the failure from an unactionable truncated syntax error to a named class.** The
`? sp30;` → `sp30.unk0` case now says `Selector requires struct/union pointer`, which is the next thing
to own.

**Blast-radius change made at the same time.** `_masked` now blanks string literals as well as
comments, because a stage that runs on every draft must not read `printf("are you sure?")` as a type
declaration. Offsets are preserved, so the positional rewrite is unaffected.

## Wiring 2 — the tier ceiling carried into the logged strategy

`eval/cohort_reconcile.tier_strategy` decides the ceiling from the source **about to be compiled** — the
same text the object will accept — and puts it in the attempt's label:

| source ceiling | logged strategy |
|---|---|
| header-assisted | `cohort-reconcile:<ledger>:project-header-assisted` |
| anything else | `cohort-reconcile:<ledger>:source-independent` |

The substring `project-header` is load-bearing: `eval/status.py:134` matches `like '%project-header%'`,
so a header-assisted match is now counted as header-assisted **at the point of logging** rather than
being corrected by hand a week later. The summary gains a `source_ceilings` tally so each reconcile
reports its own split.

This is a measurement, not a relabelling: the ceiling comes from `match_claim_audit.audit()` reading the
source, and a source that takes no struct layout is left in SOLVED.

**It applies to future reconciles only.** The v18 and v19 attempts are already logged under the old
label, and `cohort_reconcile` skips functions that already have an exact row — history is not rewritten.
So the recorded 267 SOLVED still needs the standing correction (≈124 by source audit); what changes is
that no *future* cohort adds to the gap.

## Tests

`tests/test_wiring_tier_and_placeholder.py`, 6 tests, each asserting the mechanism FIRES through its new
caller — because a stage wired into a shared path that silently declines is invisible in exactly the
place nobody watches:

- `repair_chain` fires the stage on `_Litob`'s prototype line, and `applied` names it
- `repair_chain` still declines on a clean draft, returning it byte-identical
- a `?` inside a string literal is left alone, at both the `repair_chain` and module level
- `tier_strategy` labels a header-assisted source and the label contains `project-header`
- `tier_strategy` leaves an independent source alone and does not trip the `recovered` rule

Full suite: **3345 passed, 3 skipped, 1 failed** — the failure is the pre-existing environmental
`test_project64_trace::test_multi_job_validation_accepts_and_rejects`.

## What this does not do

It does not change any published count. Byte-exact is 333, and the label-based 267 SOLVED still reads
267 until either the whole-set correction is applied or a future reconcile is run for those functions.
Both wirings change what happens *next*, not what happened.
