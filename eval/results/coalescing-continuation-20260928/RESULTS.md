# Generous continuation budgets: one guided match, no automatic matches

Completed 2026-09-28. All 40 first-round sources and both larger continuations
finished. Total new work: **20,357 native compile attempts and 32,303 optimizer-key
calls**, including failures, rechecks, probes, baselines and repeated ablations.
This is an exposed development experiment on retained candidates, not a fresh
capability evaluation. Sources use project headers; earlier lineage remains
unverified, so the recorded assistance tier is **unknown**, not unassisted.

## Outcome

| Work | Native compile attempts | Result |
| --- | ---: | --- |
| 17 functions, 40 starting sources, 512 effective units each | 16,839 | 0 exact functions; 39/40 runs improved their own starting residual |
| Hold case: two best descendants, another 2,048 units each | 3,281 | 0 exact functions; better branch plateaued |
| Separate assembly-guided hypotheses on 3 functions | 15 | `updateRacePlayerMode48AerialTrick` certified exact |
| Timer-edit ablations, including the initial baseline-only check | 8 | Same edit alone did not match either earlier root; matched the searched root |
| Guided timer edit on original root, followed by existing search | 214 | Same function certified exact again |

The repeated successes are **one distinct matched function**, not three new
matches. The result is an isolated object-section certificate plus frontend
pass; no final ROM claim or global-ledger novelty claim is made. Candidate C,
certificates and frontend receipts are saved beside this report. No live
campaign, KB or build-path source was changed.

## The successful composition

The best automatic continuation of the coalesced AerialTrick source had residual
`[8,10,10]`. Inspection of its target assembly suggested removing the later use
of a wide velocity temporary as a narrow timer cache:

```c
/* Before */
temp_v0_2 = player->updateTimer;
if (temp_v0_2 < 0x2D) {
    player->updateTimer = temp_v0_2 + 1;
}

/* Successful hypothesis */
if (player->updateTimer < 0x2D) {
    player->updateTimer += 1;
}
```

That change produced an exact certificate. No reference function body was read.
The complete current one-step generator on that actual searched parent offered
94 variants, and none contained this direct increment form or the winning body
ignoring whitespace. The winning source was absent from that run's compile and
key-resolution logs. This establishes a missing immediate proposal, not that
no longer path through existing proposals could reach a match.

The follow-up ablation used a source-syntax proposal for a cached conditional
increment. Its first strict pattern did not fire because an unrelated field
increment sat between the load and condition; those two baseline compiles are
retained and counted. The revised bounded pattern preserves that intervening
increment and declines when it updates the same field.

| Starting source for timer edit | Residual after edit | Exact |
| --- | --- | --- |
| Original retained source | `[2,11,14]` | No |
| Retained coalescing child | `[2,11,14]` | No |
| Best automatic continuation of coalescing child | `[0,0,0]` | Yes |

Then a fresh native search from **original source + timer edit**, with the same
512-unit settings, matched in 214 compiles and 330 key calls (260.2 effective
units). The last automatic step was `field_local:var_v1:compound`.

This supports composition: the existing search can finish once given the
missing timer representation. It does not prove coalescing necessary; the
successful guided-root search began from the original source. The experimental
timer proposal is in `aerial_ablation.py`; it has not been added as a production
mutation family. Promoting it requires the usual generator eligibility review,
motivating-case and decline tests, and wiring check.

## Equal-budget first round

Budget means actual compiles plus `0.14 * optimizer_key_calls`. The existing
engine can slightly overshoot an action boundary: nominal total was 20,480;
actual charged work was 20,500.14. All fresh baseline gradients reproduced.
All compiler/environment checks and source/result bindings passed.

Relative to each function's original-source run at the same nominal budget:

| Retained starting source | Better final residual | Tied | Worse |
| --- | ---: | ---: | ---: |
| Best gradient child, 15 functions | 8 | 2 | 5 |
| Distinct best score child, 4 functions | 1 | 0 | 3 |
| Same-object alternative, 4 functions | 1 | 2 | 1 |

These extra roots spend extra work. Taking their best result together does not
constitute an equal-total-budget advantage over one original run. Selection was
adaptive from prior exposed results, one seed per root, and these small counts
are descriptive.

The same-object win was `updateRacePlayerTrickSubstateHold`: original continuation
`[2,20,22]`, score/gradient-child continuation `[2,22,24]`, same-object alternative
continuation `[0,18,20]`. This demonstrates a useful alternate source state on
this run. It does not establish that a no-op was necessary for reachability.

All 17 original-source runs improved their full-listing residual, but none
matched. Better tuple values are only search heuristics, not semantic progress
certificates. One conspicuous counterexample below rewards incorrect call
argument order.

## Larger budget pair

Restarted from each Hold branch's own actually compiled best source. Search
settings stayed fixed; the new allowance was 2,048 effective units per branch.
This restarts at a retained source rather than resuming the old frontier.

| Branch | Starting residual | Best residual | Compiles | Keys | Charged work |
| --- | --- | --- | ---: | ---: | ---: |
| Original descendant | `[2,20,22]` | `[0,22,24]` | 1,658 | 2,786 | 2,048.04 |
| Same-object descendant | `[0,18,20]` | `[0,18,20]` | 1,623 | 3,036 | 2,048.04 |

Both stopped at budget, not at a proof of graph exhaustion. Both reached logged
mutation depth 9. The original descendant found its best at compile 2 and never
improved it again; the same-object descendant never improved its baseline.
The original descendant's apparent improvement contains two wrong calls:

```c
setRaceMotionAnimation(0x18, (RaceMotionState *) player);
setRaceMotionAnimation(0x16, (RaceMotionState *) player);
```

The target takes the motion pointer first. Ignoring register identities in the
primary gradient lets these wrong argument assignments resemble the target's
instruction shapes. Neither passed the byte certificate. This is a measured
reason to avoid interpreting all gradient improvement as movement toward a
correct program, and to investigate call-role-aware ranking or proposal guards.

More budget did permit deeper search: 30/40 first-round runs reached only logged
depth 2 despite a configured maximum of 12. Probe and candidate evaluation cost
can consume the allowance before deeper expansion. Thus these observations do
not absolve selection/budget allocation or prove that vocabulary alone explains
every failure.

## Verification and costs

- First round: 155 failed compile attempts, all counted and logged.
- First-round key reuse: 13,178 events, 299 random audits (299 conclusive),
  3,567 expansion checks (all conclusive), zero observed violations.
- Larger pair: 4,424 reuse events, 95 random audits and 1,788 expansion checks;
  all checks conclusive, zero observed violations.
- Guided-root search: 149 reuse events, 2 random audits and 31 expansion checks;
  all conclusive, zero observed violations.
- The 2% rate is a sampling probability, not a requirement that exactly 2% of a
  finite run be audited. These observations do not prove key equality universally.
- 103 focused helper/coalescing/search/runner tests passed before native work;
  the three root-selection tests also passed on the final rerun. Receipt audits
  bind results to frozen bundles, reconstruct expenditure, check best-source
  hashes/full-listing gradients, and independently recertify all claimed wins.

Across all phases the charged total is 24,879.42 effective units. This excludes
the older coalescing sweep that supplied roots, and includes all new ablation
and repeat-check compile attempts described above. Certificate rereads are not
compiler calls. No experiment jobs remain running.

## Artifacts and reproduction

- `native-receipts.json`: all 40 first-round results and the larger pair, native
  artifact paths, residuals, cost and audit counters.
- `guided-receipts.json`: separately labeled guided proposals, bound parent
  lineage and independently checked winner certificate.
- `ablation-receipts.json`: both ablation batches and guided-root search.
- `timer-neighborhood.json`: the complete 94-proposal one-step inventory check.
- `updateRacePlayerMode48AerialTrick.c`: first guided winner.
- `updateRacePlayerMode48AerialTrick.guided-then-search.c`: repeat winner reached
  by search from the earlier root after the timer edit. Each has adjacent
  `.certificate.json` and `.frontend.json` files.

Native roots, full logs and frozen inputs:

```text
/home/grant/decomp/experiments/coalescing-continuation-20260928
/home/grant/decomp/experiments/coalescing-continuation-extended-20260928
/home/grant/decomp/experiments/coalescing-guided-20260928
/home/grant/decomp/experiments/coalescing-guided-ablation-20260928
/home/grant/decomp/experiments/coalescing-guided-ablation-v2-20260928
/home/grant/decomp/experiments/coalescing-guided-then-search-20260928
```

Use the native `/home/grant/decomp/sbk1/.venv/bin/python` to run this directory's
drivers through `/mnt/c/Code/gameDecomp/`. `run.py prepare --budget 512` freezes a
new output directory; `run.py worker --workers 4 --worker N` runs its four
disjoint shards. `extend.py prepare` and `extend.py run --role original|noop`
perform the adaptive pair. The guided, ablation and audit scripts expose their
paths through `--help`. Reusing an existing output directory is refused.
Settings and selection rules are in `PREREGISTRATION.md`.
