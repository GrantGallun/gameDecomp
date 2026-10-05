# The capability numbers, audited before they grow again

**Date:** 2026-09-17 · **Goal rounds 1–4** · model calls 0

## FINAL, measured over all 333 exact functions (re-run after cohort v19)

| | n |
|---|---|
| byte-exact | **333** |
| labelled SOLVED | 267 |
| labelled header-assisted | 11 |
| recovered | 55 |
| **labelled SOLVED whose winning source dereferences a member through a header-declared type** | **143** |
| **corrected SOLVED** | **124** |
| **corrected header-assisted** | **154** |

Measured, not derived: `eval/match_claim_audit.py --from-db` re-run over all 333, 0 missing sources.
Ceilings 190 header-assisted / 58 mentions-a-header-type / 85 unqualified. The cross-tab's
`SOLVED-by-label` rows total 275 rather than 267 because `status.py`'s set also includes on-disk exacts
that are not in the attempts table; the correction is taken against the label rows, which is the
conservative direction.

Cohort v19 contributed 14 settled nodes, all `REPRODUCED-EXACT`, of which the audit makes **4
header-assisted, 6 mentions-a-header-type, 4 unqualified** — so the honest increment was +4 SOLVED, not
+14, and it was audited before being quoted.

**The whole-set answer: 139 of the 261 functions labelled SOLVED have a winning source that
dereferences a member through a header-declared type.** Byte-exact is 319 and does not move; the tier
split does. `eval/status.py` currently prints 253 SOLVED / 11 header-assisted / 55 recovered.

## 0. The whole-set check (item 2), over all 319 exact functions

`eval/match_claim_audit.py --from-db` reads the winning exact attempt's `source_code` for every
function the knowledge base calls exact — the source the object actually accepted, not the draft.
All 319 had a source; nothing was skipped for missing evidence.

| source audit ceiling | n of 319 |
|---|---|
| header-assisted — dereferences a member through a header-declared type | **186** |
| mentions-a-header-type only | 52 |
| unqualified — no project declaration involved | 81 |

Cross-tabulated against the label rule the project uses today:

| `status.py` label | audit ceiling | n |
|---|---|---|
| SOLVED | header-assisted | **139** |
| SOLVED | mentions-a-header-type | 50 |
| SOLVED | unqualified | 72 |
| header-assisted | header-assisted | 2 |
| header-assisted | unqualified | 1 |
| recovered | header-assisted | 45 |
| recovered | mentions-a-header-type | 2 |
| recovered | unqualified | 8 |

The 45 `recovered / header-assisted` rows are not a second finding: a recovered source IS the reference
decompilation and uses project types by construction, and `recovered` is already excluded from SOLVED.
Double-booking them would inflate the correction, which is why they are separated here.

**So the corrected split is approximately 253 − 139 = 114 SOLVED, 11 + 139 = 150 header-assisted, 55
recovered, 319 byte-exact.** And 119 of the labelled-SOLVED take no struct layout at all (72 wholly
unqualified, plus 47 that name a header type but contain no `->`), which is the part of the number that
survives the strictest reading.

## 0b. Not applied, and why

The classification is NOT changed here. `eval/status.py` carries the reasoning: an include-based test
would reclassify existing SOLVED matches and lower the count, so whether that is a correction or a
regression needs the check run across the whole matched set rather than applied to one match. **That
precondition is now satisfied — the check is done, over all 319** — so applying it is a bounded change
rather than a guess. It is left as the operator's explicit call because it moves the headline number by
139, and because the label rule is the project's own published definition rather than a bug I found.
The honest position until then: quote 319 byte-exact, and do not quote 253 SOLVED without saying it is
label-based. The ratchet is byte-exact, and byte-exact is 319 under every reading.

## 1. What the audit does, and the distinction it turns on

`eval/match_claim_audit.py --from-ledger <glob>` audits the source that **actually matched** — the
ledger node's `...-artifacts/<stamp>-<name>.best.c`, not the workspace's `base.c`, because the draft is
where a run starts and the best source is what the object accepted. The route matters: `tools/claude`
builds m2c's `ctx.c` from the project's own `src/**.c`, so a draft may already carry the reference
layout, and a match resting on that layout is not independent capability.

**Mentioning a type is not the same as taking its layout.** The first pass counted any header-declared
type the source names and returned **68 of 91** as header-assisted. That over-claims: a type named only
in a prototype costs the translation unit nothing. The test is whether the source **dereferences a
member through** a header-declared type — `RacePlayer *p; ... p->field` — because that is the header
supplying the offsets.

## 2. Result over every cohort ledger (`failure-coverage-fresh-paired-*.json`)

| ceiling | n of 91 |
|---|---|
| **header-assisted** — dereferences a member through a header-declared type | **27** |
| mentions-a-header-type only | 41 |
| **unqualified** — no project declaration involved | **23** |

And the 41 middle rows are not a grey zone in the direction that would help: **38 of them contain no
`->` at all**, so they take no struct layout from anything and their SOLVED status cannot be a header in
disguise. Three contain a few arrows and were inspected.

## 3. The test under-counts, and one inspection shows how

`returnToRaceTypeSelectMenu` (`arrows=4`, classified mentions-only) dereferences **globals**:

```
20:    gRacePlayers->menuState = 0;
22:    gCurrentGameTask->fade = 1;
38:/* Warning: struct RaceUiRankTrigger is not defined (only forward-declared) */
```

`gCurrentGameTask->fade` reads a member of a header-declared struct — the header supplied that offset —
so this row IS header-assisted and the regex missed it, because it only looks for a **local** declaration
`Type *var`. `RaceUiRankTrigger`, by contrast, is only forward-declared and that part is clean.

So the honest statement is a range, not a point:

> **At least 27 and at most 30 of the 91 cohort-reconciled matches are header-assisted.** On the audit's
> strict reading SOLVED would be **253 → 226** and header-assisted **11 → 38**; on the loose reading
> (any header-declared type named) it would be 185 / 79. The strict reading is the defensible one; the
> loose one is not.

Fixing the classifier is a specified, bounded next step: the missing case is member access through a
**global** whose type a header declares (`extern RacePlayer *gRacePlayers;` in `include/game/**`), which
is resolvable by reading the headers for global declarations rather than by inference.

## 4. What this round does NOT do

It does not change the counts. Relabelling attempts to move a number is the thing the audit exists to
prevent, in either direction, and `eval/status.py` already carries the reasoning that this needs the
whole-set check before it is a correction rather than a regression. The whole-set check is now half
done — every cohort-reconciled node has been audited; what remains is the non-cohort matches, of which
`header-assisted` currently counts 11.

**Byte-exact is 319 under every reading.** That number is the ratchet, and it did not move.

## 5. The loop kept running

Cohort v19 launched at `--per-stratum 12`, `--model-calls 0`, `PYTHONPATH=.`, single-flight. It is
producing as this receipt is written (`updateRacePlayerPostUpdateAttack` already
`function_exact_pending_integration` at 100.0); its settled nodes will be reconciled per node and
audited with `--from-ledger` **before** any of them is quoted as capability.

## Files

| path | what |
|---|---|
| `eval/match_claim_audit.py` | `--from-ledger`, the member-access test, the `arrow_uses` diagnostic |
| `eval/results/claim-audit-all-cohorts-20260917.json` | all 91 nodes, each with its ceiling and the evidence for it |
| `eval/results/claim-audit-v18-20260917.json` | the 14 nodes from cohort v18 |
