# Why the panel always has "a couple of problems"

> **CORRECTION 2026-09-21, after this was written — every number below was measured through a THIRD
> window.** This file fixes two (the runner's six-line one, then `errors[:40]`) and the class set is
> still capped, by **clang's own `-ferror-limit`, which defaults to 20**; the checker recipe passed no
> such flag. In this frame's own receipt the highest error count ever observed is **19**, **352 of 780
> reads (45%) sit exactly on it**, **118 of 200 states** have at least one pinned read, and
> **`errors_truncated` is 0 everywhere** — every truncated observation was labelled complete.
>
> Most exposed: `undeclared-member` ("cleared in 0"), `member-on-typed-pointer`, and the
> `masking_fanout ≈ 1.0` centrepiece — under a hard cap, clearing errors *mechanically admits* new
> ones, which is indistinguishable here from a wall falling. The class-set distribution is a **lower
> bound** for those 118 states.
>
> Fixed in `solver/frontend_check.py`, `solver/frontend_diagnostics.py` and `eval/intake_probe.py`;
> tests in `tests/test_frontend_error_limit.py`. **Re-measure the frame before acting on the repair
> order below.** Full account and the control for the re-run: [ERROR-LIMIT.md](ERROR-LIMIT.md).

Measured on the frozen 200-state intake frame, with `eval/fault_history.py`. Receipts:
`fault-history-current.json` (the panel now, with the blocker graph), `unclassified-frontier.json`.
Commands:

```
python -m eval.fault_history --frame eval/results/intake-20260921/wide-intake-acceptance2.json \
  --compare eval/results/intake-20260921/wide-intake-traced.json \
  --out eval/results/intake-20260921/fault-history-current.json
```

## The instrument changed twice while measuring, and both changes are visible in the numbers

1. **The class set is now built from every error, not the first six.** A state whose first six diagnostics
   shared one class was reported as a one-class state. The class-set distribution moved accordingly: what
   was "63 states with one class, 83 with two" became part of a much longer tail.
2. **The taxonomy had a hole.** `incomplete definition of type 'X'` — the diagnostic
   `source_type_declarations` exists to repair — matched no class and fell into `unclassified`. Naming it
   (plus `aggregate-where-scalar-required` and `incompatible-int-pointer`) moved **31 states out of
   `unclassified`**, which halved that bucket (61 → 30).

The acceptance levels did not move at all — **IDO 32, IDO+frontend 19, byte-exact 2** — which is the check
that this was a change of instrument and not of candidates.

### The panel, measured properly

| classes on the final candidate | states |
|---|---:|
| 0 | 20 |
| 1 | 37 |
| 2 | 64 |
| 3 | 40 |
| 4 | 20 |
| 5 | 16 |
| 6 | 3 |

**79 states carry three or more classes.** The earlier "a couple of problems" reading was partly the
six-error window talking.

## Repairing a blocker reveals about one more class

`masking_fanout_any_attempt` — for every state where the class was there at the start, how many classes
appear later that were not visible then:

| blocking class | states | classes revealed | mean per state |
|---|---:|---:|---:|
| `undeclared-identifier` | 166 | 169 | **1.02** |
| `unknown-type-name` | 125 | 123 | **0.98** |
| `undeclared-function` | 80 | 70 | 0.88 |
| `other-syntax` | 34 | 18 | 0.53 |
| `unclassified` | 18 | 15 | 0.83 |

A mean of ~1.0 is the whole explanation. Each repair clears one wall and the next one becomes visible, so
the class COUNT stays flat **by construction** while the classes change identity. The loop is not failing to
make progress; the number being watched cannot show progress.

## The revealed classes were never new

Classes not visible at step 0 that appear after a repair (`classes_masked_then_revealed`):

| class | states |
|---|---:|
| `unclassified` | **70** |
| `member-on-typed-pointer` | 31 |
| `undeclared-member` | 22 |
| `incompatible-pointer` | 20 |
| `redeclaration/conflict` | 18 |
| `other-syntax` | 13 |
| `undeclared-function` | 10 |

Seventy states only show an *unclassifiable* fault once the declaration wall falls. Nothing created those
faults; the earlier wall hid them. Reported as "more problems", they read identically to a regression.

## Round over round, on the same panel

| | |
|---|---:|
| states whose class set moved | 47 |
| states unchanged | 153 |
| classes removed in total | 39 |
| classes added in total | 62 |
| **byte-exact** | **2 → 2** |
| states that changed BASIN | **17** |

Two things follow. First, the removed/added totals nearly cancel — again, the count is the wrong meter.
Second, **17 of the 47 moved states changed basin** (declaration ↔ unclassified, mostly). The earlier
measurement of this codebase says a basin escape is the predictive event: 0.375 success against 0.036
inside a basin, with only 3.25% of setbacks changing class at all. Those 17 are where the progress is, and
`byte-exact 2 → 2` is where it is not showing yet.

## WHICH BLOCKER CLEARS WHICH — the repair order

An edge means the two classes changed in the **same step**, and it records the action that did it. Depth is
the step at which a class first becomes visible; the order inside a depth is by per-fix yield
(`unlocked_per_clear` = classes revealed ÷ states where this class was actually cleared).

### Depth 0 — nothing hides these

| class | visible at step 0 | cleared in | classes it gates | per clear |
|---|---:|---:|---:|---:|
| `unknown-type-name` | 127 | 112 | **10** | **1.10** |
| `undeclared-function` | 132 | 106 | 9 | 0.85 |
| `undeclared-identifier` | **172** | 62 | 8 | 0.82 |
| `incompatible-int-pointer` | 4 | 7 | 5 | 0.86 |
| `incomplete-definition` | 11 | **1** | 2 | 1.00 |
| `expected-identifier` | 11 | 11 | 3 | 0.55 |
| `other-syntax` | 45 | 27 | **1** | **0.04** |
| `parameter-declarator` | 25 | 20 | **0** | **—** |
| `undeclared-member` | 27 | **0** | — | — |

### Depth 1 — only visible after a repair

`member-on-typed-pointer` (0.80), `unclassified` (0.35), `redeclaration/conflict` (never cleared).

### The chain, with the action attached

```
header_variant      clears unknown-type-name     -> reveals incomplete-definition   34
header_variant      clears undeclared-function   -> reveals incomplete-definition   28
header_variant      clears undeclared-identifier -> reveals incomplete-definition   17
source_type_declarations  clears unknown-type-name -> reveals unclassified          17
resolve_placeholders      clears expected-identifier -> reveals member-on-typed-pointer  4
undeclared_identifiers    clears undeclared-identifier -> reveals incompatible-pointer   6
```

**These four things decide the order:**

1. **`unknown-type-name` and `undeclared-function` are the walls, and they are already being broken** —
   `header_variant` clears them in 112 and 106 states. Nothing needs to change there.
2. **The thing immediately behind them is `incomplete-definition`, and essentially nothing clears it: 49
   states carry it, 11 show it at step 0, and it is cleared in exactly ONE state.** Its owner,
   `source_type_declarations`, is currently spending its fires on the wall in front (`unknown-type-name →
   unclassified`, 17 states) rather than on the class it was written for. Add `undeclared-member` (53
   states on the final candidate, visible at step 0 in 27, **cleared in none**) and `member-on-typed-pointer`
   (62 states) and this family is the largest residual in the frame and the least repaired.
   **This is the blocker to pause for.** It is not a blocker by fan-out — it is a blocker because everything
   behind it stays invisible while it stands and no action clears it.
3. **`undeclared-identifier` is the most widespread and among the most stubborn**: visible at step 0 in 172
   states, cleared in 62. It gates 8 classes, so it is still worth doing — after the two above.
4. **`other-syntax` and `parameter-declarator` are LEAVES.** `parameter-declarator` is cleared in 20 states
   and gates nothing at all; `other-syntax` is cleared in 27 and gates one class at 0.04 per clear. They are
   common, they look like blockers, and repairing them reveals nothing. Do them last.

### Caveats that are part of the result

* The graph is **not a DAG**: four two-cycles exist (`incompatible-int-pointer ↔ incomplete-definition`,
  `member-on-typed-pointer ↔ other-syntax`, `member-on-typed-pointer ↔ unclassified`,
  `unclassified ↔ undeclared-function`). The order comes from first-appearance depth, not a topological
  sort, and the receipt says so.
* Same-step co-occurrence is **correlation at the finest granularity this data has**, not proof of cause.
  Two classes can share one root cause, and one action can clear one thing while revealing another for its
  own reasons.
* A class count is an observation. Nothing here is a difficulty score and nothing here should be a reward.


## What named the frontier: 57 of 97 error lines behind `unclassified` were one class

`unclassified-frontier.json` recovers the final candidate of each state whose *entire* residue was the
`unclassified` class, reruns the real checker, and reads the messages. The dominant one:

```
57x  incomplete definition of type 'X' (aka 'struct X')
      e.g. drawRacePlayerModel: incomplete definition of type 'RacePlayerModelRenderState'
 6x  use of undeclared identifier 'X'        (e.g. alSynStartVoice: 'bitwise')
 5x  incompatible integer to pointer conversion
 4x  field has incomplete type 'union _anonymous'
 4x  implicit declaration of function 'X'
```

A class nobody can name is a class nobody can own. `source_type_declarations` was written for exactly
`incomplete definition of type 'X'`, and the taxonomy had no entry for it — so the largest named residual in
the frame was being reported as an unclassifiable one, and its owner was firing at the wall in front of it
instead. Naming it is what turned the histogram from "`unclassified` is the second-biggest class" into
"`incomplete-definition` is 49 states and almost nothing clears it".

## And the meter that should actually move

1. **Watch `exact`, and basin transitions — not the class count.** Both are in every receipt now.
2. **The order above is the plan**, and its first item is not the biggest class: it is the class that
   everything behind it waits on and that nothing currently clears.
3. The earlier gap analysis explains why clearing it is hard: of 93 undeclared names, **80 are declared in no
   file at all** — m2c-invented type names the binary cannot name for you. A name and a layout with no
   evidence behind them is a decision, not a pass over the headers.
4. **A class count must never become a reward or a distance label.** It is a frontier measure; this file is
   the evidence for that sentence.
