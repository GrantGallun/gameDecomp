# Local DREAM-style search: measured development result

Built a local replay and scheduling layer using the existing deterministic repair
generators, IDO compiler and object verifier. The final policy preserved two known
development matches using **15 instead of 22 compiles (31.8% fewer)**. It tied the
existing search on the final two separate development functions, with **no new
exact matches**. Keep this experimental; the production scheduler is unchanged.

## What was built

- `eval/search_scheduler.py`: live parent-local mutation streams, explicit source
  lineage, charged baseline/failed compiles, persistent world checkpoints.
- `eval/search_replay.py`: immutable revealed observations, strict source/object/
  frontend certificate checks, compatible-world merging, incomplete-replay
  rejection, and bounded selection among deterministic scheduling recipes.
- Three development cycles below. No model weights were trained and there were
  no model API calls. The policy family was developed by the coding assistant;
  offline selection among its candidates is automatic. This is a local adaptation
  of the exploration-policy idea, not a reproduction of the paper or an autonomous
  code-writing policy developer.

Policies never receive hidden children, function names or candidate source. They
see node ID, depth, observed score and number of expansions. Missing history
means unsupported coverage; it cannot improve a policy's ranking. Exactness
requires the supported object-certificate kind/status/schema, source and target
binding, and a frontend pass. The policy objective is exact count, then summed
score improvement, then fewer compiles; incumbent wins ties.

## Results and iteration

**First cycle (`pilot-v2`).** Three calibration functions selected greedy search.
On four different development functions, greedy found 0/4 exacts in 192 compiles;
breadth and the existing beam each found 1/4 in 156 compiles. Greedy missed
`__MusIntProcessWobble`: a small 97.045 -> 97.5 score increase distracted it from
the exact candidate. This candidate policy was rejected, not promoted.

**Expanded calibration (`evolution-v1`).** Move the observed Wobble regression
into calibration alongside Fdistort. Breadth finds both in 22 compiles. Greedy
and both per-node visit-penalty recipes find only Fdistort, so selection returns
to breadth. Fresh comparisons on three other development functions find 0/3
exacts for both breadth and beam. A descendant resetting its visit penalty is
the observed failure mechanism behind the next proposal.

**Feedback-driven revision (`adapted-v1`).** Score a branch as
`observed_score - penalty * depth`. Three penalties (1, 4, 16) were compared
against the expanded frozen replay history. Missing coverage remains disqualifying;
penalty 1 wins the complete replay and is frozen before fresh compiler validation.

| Calibration function | Breadth compiles | Revised policy compiles | Certificate |
|---|---:|---:|---|
| Fdistort | 10 | 3 | Object-exact, frontend passes |
| __MusIntProcessWobble | 12 | 12 | Object-exact, frontend passes |
| Total | 22 | 15 | Both preserved |

Both are **header-assisted**, already known development repairs, not new global
matches. Both sources were independently recompiled in separate native workspaces.
The first cycle's existing beam result is 12 compiles for Wobble; the preceding
residual-repair experiment's existing beam result is 10 for Fdistort.

The final policy then faced two different, already exposed development states:

| Function | Initial score | Best observed score, all three arms | Compiles per arm | New exacts |
|---|---:|---:|---:|---:|
| updateRacePlayerLeanAngle | 39.403 | 43.956 | 32 | 0 |
| __ll_rem | 66.188 | 66.188 | 1 (generator exhausted) | 0 |

The three arms are breadth, the frozen revised policy, and the existing beam.
The final two-state panel shows no additional benefit or observed regression.
It is too small to establish generalization. Earlier fresh panels become
development evidence once inspected; no sealed test or held-out answer body
was used. The reference-source-assisted development function was excluded.

## All compiler cost

| Run | World collection | Fresh comparison | Independent confirmations | Total |
|---|---:|---:|---:|---:|
| pilot-v1 (stopped on timestamp conflict) | 19 | 0 | 0 | 19 |
| pilot-v2 | 155 | 504 | 1 | 660 |
| evolution-v1 | 127 | 185 | 2 | 314 |
| adapted-v1 | 37 | 99 | 2 | 138 |
| **Total** | **338** | **788** | **5** | **1,131** |

Each attempted compile, including failures and baselines, has a private SQLite
receipt. The 31.8% number is **search cost on two calibration states**, not net
savings after development cost. Offline replay makes no compiler or model calls.
All runs use sequential WSL-native build workspaces on the available four CPUs.

## Verification and limits

138 focused tests passed on Windows and WSL, covering the new scheduler plus
existing mutation, beam-search, bounded-search, tool-boundary and residual suites.
Fresh code review found certificate-shape/target-binding gaps, missing fingerprint
inputs, and omitted calibration confirmation; all received failing regression
tests and fixes. A compiler-exception logging bug also received a real SQLite
regression test. Timestamp-only diff feedback is canonicalized while raw feedback
is preserved. The final depth-penalty behavior has an online/replay regression test.

The first two prototype runs retain their original records. Their context binding
predates the review fixes; they are excluded from current replay and were not
silently upgraded. Final policy selection uses the hardened `evolution-v1` worlds.
World checksums detect accidental edits, not malicious record fabrication, and
the build fingerprint is not a hermetic dependency certificate.

Legacy beam callbacks do not supply unique parent IDs. Beam attempts are logged
with unknown lineage and are not imported into replay. Beam `best_score` in raw
reports means maximum observed score; its retained source is selected by the
existing register gradient and can differ from the score champion.

No production KB, real game source, model, default solver or automatic promotion
was changed. Current production status remains **348 object-exact: 256 SOLVED,
12 header-assisted, 25 reference-type-assisted, 55 recovered**. The experiment
does not add to those counts. All experimental rows remain training-ineligible.

## Reproduction

Use the SBK1 Python environment inside WSL, from `/mnt/c/Code/gameDecomp`:

```bash
/home/grant/decomp/sbk1/.venv/bin/python eval/results/dream-search-20260922/pilot.py --run-id new-pilot
/home/grant/decomp/sbk1/.venv/bin/python eval/results/dream-search-20260922/evolve.py --run-id new-evolution --budget 32
/home/grant/decomp/sbk1/.venv/bin/python eval/results/dream-search-20260922/audit.py
```

The preserved final revision is `adapt.py`; its already-frozen decision is
`adaptation-selection.json`, and its fresh verification is
`adapted-v1/selection.json` plus `adapted-v1/report.json`. Run directories are
append-only by convention: the driver refuses to overwrite an existing run.
The adaptation driver refuses to overwrite its frozen selection as well.
Per-run JSON worlds contain full candidate sources, verdicts and provenance.
Native databases live under
`/home/grant/decomp/experiments/dream-search-20260922/<run>/attempts.sqlite`.

The next useful research step is broader repair-action coverage and a larger
function-disjoint evaluation, not another training run on these few exposed cases.
