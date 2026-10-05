# Population transfer: the repair was already written, the search never called it

**367 byte-exact (was 359), SOLVED 271 (was 265).** Eight functions, all independently recompiled
and recorded through the ratchet; `audit.py` binds each one from search world to certificate to
confirmation to main-KB receipt. No model calls. Every compile logged.

The 2026-09-22 Codex session tested each repair it built on a panel of 4-13 functions chosen by
module family (thread functions, timer functions). Most of those names lacked a workspace or failed
to compile, so no family was ever measured on the residuals it targets, and the later layers
(transition planner, theory map, capability map, generated potential) were built on one or two
supporting functions each. This experiment re-runs the question on the **whole population**: every
function with a compiling attempt and no exact attempt (224), best compiling source frozen by hash
before any compile, Codex's own frozen scheduler (depth 1.18754, 32 calls, depth 4). 24 of the 224
were named somewhere in a Codex experiment directory (`exposed.json`); the other 200 are the
transfer set.

## Four arms, one change each

| Arm | Candidate stream | Exact /224 | vs `expanded` |
|---|---|---:|---|
| `control` | `variants()` minus the seven 2026-09-22 families | 2 | |
| `expanded` | `variants()` as Codex left it | 3 | |
| **`routed`** | + 18 unreached `solver/rewrites.py` owners (`solver/owner_rewrites.py`) | **8** | **+5 / -0, sign p=0.031** |
| `prior` | `expanded`, families reordered by cross-fitted improvement rates | 3 | +0 / -0 |

**The seven new families transfer nothing.** On the 200 uninspected functions, `control` and
`expanded` both solve the same 2. The only `expanded` gain is `__osDequeueThread`, their own
development case. Four of the seven (`unsigned_float`, `cursor_rebase`, `cursor_advance`,
`address_reuse`) fire on no root in the population. They are correct for the functions they
were written from, and narrow.

**Wiring existing owners does transfer.** `solver/rewrites.py` holds 27 diff-keyed repair families;
`variants()` reached nine. The other eighteen, including `layout_rewrites` (the `diffrepair`
owner CLAUDE.md credits with the only match of its era), were reachable from no near-miss search.
Exposing them, with no other change, solved five functions no earlier experiment had looked at:

| Function | Path to exact | Tier |
|---|---|---|
| `dispatchRacePlayerMode30Attack` | `owner:layout` (was 99.999, zero variants from every other family) | source-independent |
| `decrementRaceChallengeTimeLimit` | `owner:layout` | source-independent |
| `randomNextMain` | `owner:drop_mask` | source-independent |
| `dispatchRacePlayerAirborneMode` | `owner:drop_mask` then `inline_temp` | source-independent |
| `drawCharacterSelectCourseExitPopup` | `owner:drop_mask` | project-header-assisted |

`drop_mask` is the edit that closed Codex's Wobble (`!(x & 0xFF)` to `!x`); it is general, and
was sitting unwired. Same budget: `routed` used 6,263 compiles to `expanded`'s 6,351.

**Two more were never searched at all.** `__MusIntInitEnvelope` (14 compiles) and `alLink` (3)
close under the pre-existing repertoire. The main KB shows 16 draft attempts and no repair search
for the first, and a `close-nearmiss` stop after its layout prepass for the second: that tool only
searches register-dominant residuals with at most two other faults, a gate that also excludes the
residuals the new families target.

**Learned ordering is an efficiency effect, not a capability one.** Per-family improvement rates are
stable across disjoint halves of the population (median split-half r = 0.89, 5th percentile 0.69),
which is the premise the transition planner needed but fitted on two functions. Using them,
cross-fitted, changes no exact: common exacts take 17 compiles instead of 20, and best scores on
unsolved functions rise. Same result Codex's scheduler work kept finding.

## What this says about the RSI direction

Every exact gain here came from making an existing repair **reachable**, found by asking one
population-wide question: which owners fire on which residuals, and which residuals have no owner.
Not one came from better scheduling, ordering or planning. The census is the part worth making
automatic: it is the system identifying its own largest gap from data, which is what "the system
improves its own repair process" needs. The firing census itself costs no compiles; each pair of
arms over the whole population took about 15 minutes on 4 WSL cores (stage 2: 917 s).

Residuals with **no** firing generator (`analysis.json` `zero_variant_roots`, 15 of 224), by owner:

| Class | Functions | Owner |
|---|---|---|
| Layout offsets | `dispatchRacePlayerMode30Attack` | `owner:layout` (now wired, solved) |
| 64-bit helpers | `__ll_lshift`, `__ll_rshift` | wide reconstruction action exists; not in `variants()` |
| MMIO literal address | `osAiGetLength`, `__osSpGetStatus`, `__osSpSetStatus` | function-exact only (volatile probe refuted) |
| Hand-written assembly | `__osPopThread` | none possible: libultra `exceptasm.s`, `t9` + filled delay slot |
| Certificate-level | `rmonPrintf` (score 100.000, not exact) | relocation-ordering residual, as `osCreateMesgQueue` |
| Broken intake | three sprite drawers at ~1% | draft is for the wrong code shape |

Budget, not exhaustion, stops the rest: 196 of 221 non-exact `expanded` runs (89%) hit the 32-call
cap, and the generic
families (`local_type` 2.9%, `commutative` 1.6%, `decl_order` 0.8% improve rate) take most of it.
Among the new owners `owner:argswap` has the same profile (284 edges, 0 exacts) and is the first
candidate to demote.

## Caveats

- **Contamination.** Starting sources are the KB's best compiling attempts, which descend from
  drafts the 2026-09-21 contamination note covers. `eval.status` finds no reference-only type in
  any of the eight, and they are counted exactly as every other match is; SOLVED remains a lower
  bound on assistance, as CLAUDE.md says.
- **Previously exposed target.** SBK1 functions, not a sealed holdout. "Unexposed" means only that
  no 2026-09-22 experiment named them.
- **Enabling edits.** 0 of 5 multi-step exact paths had a non-improving intermediate step, but a
  depth-penalised score scheduler rarely keeps one, so this does not refute Codex's factorial finding.
- **Disk-full incident.** C: reached 0 bytes mid-run (the campaign had written ~150 GB into WSL on
  2026-09-21). 14 torn rows and 14 stale-workspace rows were quarantined in `rows-quarantine/` and
  rerun; the final 448+448 rows have 0 infrastructure errors. `attempts.sqlite` holds 13,228
  receipts, including orphaned compiles from the killed tasks.
- Tests: `tests/test_owner_rewrites.py` (fire test on the motivating residual, evidence gating,
  raising owner, preprocessor guard) plus adjacent suites, 91 passed on Windows. WSL has no pytest;
  the stage-2 run exercised the module there.

## Files

`freeze.py` / `freeze2.py` (code snapshots and population, fixed before compiling), `run.py` /
`run2.py` (arms), `analyze.py` / `analyze2.py`, `rewrite_census.py` (which unwired owners fire),
`probe.py` (hypothesis probes, `probes.jsonl`), `record.py`, `audit.py`, `exposed.json`,
`analysis.json`, `analysis2.json`, `inventory-receipts.json`, `inventory-ratchet.json`,
`status-before.md`, `status-after.md`. Worlds, rows and receipts:
`~/decomp/experiments/population-transfer-20260922/`.
