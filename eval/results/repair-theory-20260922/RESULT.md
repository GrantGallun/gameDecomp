# Repair theory map: frozen implementation and compiler comparison

Implemented an opt-in goal/prerequisite/alternative map with receipt-bound local
effects, distinct-route retries and continuation from useful noncompiling
candidates. Six adapters expose existing guarded signature, member, frontend ABI
and assembly/header redraft mechanisms. No new source transformation was added.

The compiler experiment retained all seven known exacts in every arm and found
no new exacts. It did preserve one useful intermediate: TimerInterrupt's 12
member errors became one remaining call error. The map identifies the missing
continuation instead of discarding the candidate or retrying it unchanged.

- [Readable measured map](THEORY_MAP.md)
- [Full machine-readable maps and effects](theory-map.json)
- [API and mathematical representation](../../../docs/REPAIR_THEORY_MAP.md)
- [Frozen report](paired/report.json) and [receipt/replay audit](paired/audit.json)

## Comparison and attribution

All arms use the same initial source, target/compiler identity, assistance tier,
transition model, depth policy and maximum 16 compiler calls per case. `baseline`
is the previous transition planner. `intake` adds the six repair adapters,
failed-parent continuation and duplicate control, with theory priority disabled.
`theory` enables that priority on the same routes. Depth is bounded at four,
lookahead at two and proposal preview at eight; preview refill is bounded too.

| Case | Exact in all three arms? | Baseline calls | Intake calls | Theory calls |
|---|---|---:|---:|---:|
| `__osDequeueThread` | Yes | 3 | 3 | 3 |
| `osGetThreadPri` | Yes | 2 | 2 | 2 |
| `Fvibup` | Yes | 3 | 3 | 3 |
| `Fvibdown` | Yes | 3 | 3 | 3 |
| `Fdistort` | Yes | 3 | 3 | 3 |
| `loadMusicSequenceBank` | Yes | 3 | 3 | 3 |
| `__MusIntProcessWobble` | Yes | 12 | 12 | 12 |
| `osSetTimer` | No | 1 | 1 | 1 |
| `__osInsertTimer` | No | 1 | 2 | 2 |
| `__osTimerInterrupt` | No | 1 | 2 | 2 |
| **Paired total** | **7/10 per arm** | **32** | **34** | **34** |

The seven unique exact sources were separately compiled and confirmed again:
**100 paired calls + 7 confirmations = 107 durable attempt receipts**. The audit
checks all 30 worlds, 77 explicit parent edges, source/frontend/certificate
bindings, exact action replay, reconstructed maps and the unchanged development
model. Every failed timer attempt is included in this denominator. Proposal
preprocessing and deterministic m2c redrafting are retained as owner reports;
they are not counted as object-oracle compiler trials.

`intake` and `theory` chose identical source/label/parent sequences on every
case. There is no measured benefit from theory priority in this panel. The two
extra calls per added-route arm tested the only available changed timer
candidates. All follow-up searches stopped because current routes were
exhausted, not because their 16-call ceilings were consumed.

## What went well and what did not

The TimerInterrupt child has source-bound evidence of clearing the member
blocker. The planner preserves it separately from its ordinary score champion
and considers its new call blocker even though it still does not compile.
This is a useful decomposition of the repair task, not an exactness gain.

The InsertTimer scalar-index candidate changes the diagnostic category of four
errors without reducing the total of 18. The local member-class reduction is
stored with the new indexing errors, and the root stays the intake champion.
This shows why a local support label cannot be treated as a success reward.

SetTimer generates no admissible candidate. Its split wide parameter shape and
public declaration exceed the signature owner's supported width correspondence;
the redraft also fails its public ABI guard. The map records this missing
capability explicitly. Increasing the retry budget alone cannot supply it.

The six routes, local-effect definitions and selection heuristic are
developer-authored. The conditional transition model is unchanged from the prior
experiment and was fitted only on the declared Dequeue/GetThreadPri development
worlds. No new weight training, self-generated theory or autonomous capability
generation is established.

## Evidence boundary and reproducibility

`freeze.py` created a native WSL snapshot of 438 files before the run. The
manifest, panel, driver, graph and model were checked before every actual call.
The run used one worker on the four-CPU WSL instance and completed its measured
driver in approximately 70 seconds, including inline replay and confirmation.
That time excludes setup, unit tests and the subsequent independent audit.

The three timer sources revisit the previous failed panel. Five other declared
names have no existing target workspace and remain reported as unavailable.
They are not dropped into an apparent eight-case success denominator. The two
model-development functions and five retention functions are also exposed cases.
Function-level exactness is checked with the existing object-section verifier;
this is not a whole-ROM build or a count of newly integrated matches.

The timer drafts and most retention cases use headers. The report retains the
original assistance tier per case (`Fvibup` and `Fvibdown` are recorded as source-
independent). No held-out reference implementation body was supplied. Main KB
exact-set identity was checked before and after; the private attempt DB and
isolated workspaces contain the experimental mutations. Production TUs and
campaign defaults were not changed. All artifacts remain training-ineligible.

Native snapshot: `/home/grant/decomp/experiments/repair-theory-20260922/code-v1`.
Attempt DB: `/home/grant/decomp/experiments/repair-theory-20260922/paired/attempts.sqlite`.

Run the completed-run audit in WSL (no new oracle compiles):

```sh
/home/grant/decomp/sbk1/.venv/bin/python \
  /mnt/c/Code/gameDecomp/eval/results/repair-theory-20260922/audit.py
```

`measure.py` intentionally refuses to overwrite a run. A new experiment requires
a new output directory and frozen boundary. `scaffold.py` records construction
of the driver from the previous audited harness; do not rerun it over a frozen
measurement.

## Verification and review

The scoped suite passes **260 tests on Windows and 260 against frozen WSL code**;
[Windows receipt](tests-win32.json) and [WSL receipt](tests-linux.json) bind tested
module hashes. It covers the prior search/transition behavior plus 26 new map,
adapter and theory-controller tests. Separate focused checks also exercised the
existing motivating header-signature and void-member guards.

Review findings were addressed before freezing: incomplete or unavailable
checker evidence cannot become a historical rejection; local effects without
an observed premise remain unassessed; exact candidates do not remain listed as
untested; and an exact child outranks its root as intake champion. The unit suite
also covers changed-context retries, duplicate suppression, hard bounds, full
failed-parent lineage, alternative approaches and partial noncompiling chains.
