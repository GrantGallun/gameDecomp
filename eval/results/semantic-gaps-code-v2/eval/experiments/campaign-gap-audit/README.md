# Campaign gap audit — compact working context

2026-09-06. Objective: make successful standalone mechanisms reachable through
one unattended controller, then classify its next failures before adding layers.

## Latest handoff: untouched-cohort progress audit

See [autonomy-progress.md](autonomy-progress.md): semantic-panel lifecycle and
optional logging metadata fixes; preserved failed v1 and completed frozen v2
eight-function run. 24 visits, four model calls, no byte-exact matches; one new
compiler/frontend pass with 57/64 sampled semantic cases passing. Post-run
recovery-churn cap is tested but not yet live-replayed. All workers stopped.

## Previous handoff: stack buffers and concrete leaf calls

Implemented and tested; all workers stopped. No game source integration, model
calls, SOLVED promotion, or new byte-exact matches. The historical 24-function
v14 and expansion-8-v2 campaign checkpoints are untouched.

- `stack_buffers.candidates` binds a unique binary call's stack argument to a
  source `&byte_local`, proposes an array from the next occupied stack slot, and
  uses the ordinary compiler/differential gates. Frame spacing is a hypothesis,
  not proof of a C allocation. No function or m2c-variable names are embedded.
- Resilient `modelrepair` runs it on compiling as well as failing roots/children,
  before spending model calls. Attempt lineage and the evidence packet are logged.
- `callee_execution.load_binary_leaves` automatically admits narrow integer
  leaves into `semantic_lane.Panel`. It checks symbol identity, extraction/ROM
  hash, contiguous bytes, and reassembles the **parsed instructions** back to the
  ROM words. Matching annotations alone are insufficient; a tampering test guards
  this. No completed C body is read or supplied.
- Leaves run in shared memory with actual register/return effects, aliasing and
  pointer escapes. Their traces remain separate from caller coverage; model
  feedback shows bounded prefixes and omission counts. Step budget, unknown
  stack initialization and frame limits fail closed. Frame limits do not recover
  all C subobject bounds. Recursive calls, relocations and hardware still decline.
- `OutputBuffer` is a generic **opt-in environment assumption**, not an automatic
  fact. Its non-escaping/write-only/address-insensitive contract supplies seeded
  bytes on a declared success code. Other stack-pointer arguments decline rather
  than ignoring possible aliases. It is not DMA/controller emulation.
- Coverage exploration now retains unsupported-execution examples outside the
  selected cases. Passing only supported cases yields
  `observed_pass_with_execution_debt` if such an obstruction remains.

### Measured __osBlockSum replay

`stack-callee-replay-v3.json` and `stack-callee-summary-v2.json` compare true parent
30149 against the automatically generated array, using identical inputs and
environment. The binary checksum leaf executes; the __osContRamRead success
output is an explicitly assumed 32-byte write, motivated by its ROM copy loop.
__osPfsSelectBank remains opaque.

| Source | Automatic panel | Directed loop/error panel | Combined |
|---|---:|---:|---:|
| Saved one-byte local, reverified 30172 | 51/64 | 8/72 | 59/136 |
| Generated 32-byte array, reverified 30173 | 64/64 | 72/72 | 136/136 |

Root failures expose `output buffer 0x20000ff7+32 escapes active caller frame`.
The combined array panel covers all modeled target/candidate instructions and
branch outcomes; this is still finite evidence under an assumed environment,
not all-input or hardware equivalence. Both score **81.492, nonexact**. The
worker-selected array is attempt 30171, exported as
`eval/results/stack-callee-worker-v3.best.c`. Paired 30173 is a separately hashed
source variant, not interchangeable provenance.

The ordinary worker does **not** silently enable the output assumption. Its
64 supported cases pass but it reports `observed_pass_with_execution_debt`:
the target checksum success path needs data the remaining opaque read never
initializes. This is environment work, not evidence that the model failed.

### Transfer and activation audit

The same default worker on two other current DEV sources retains 64/64 passes:
alLoadParam (30165, score 91.897) and updateEndingTommyWaitThenFinalPhase
(30167, 99.744). Source-bound replay confirms **8 actual alCopy calls** and
**7 actual setCallbackTaskCallback calls** on each side, respectively. These
were already passing callers, not newly solved functions. Both remain nonexact.
Receipts: `callee-transfer-alLoadParam-v1.json`, `callee-transfer-ending-v1.json`.

`stack-callee-prevalence-v1.json` audits the same 32 DEV functions: 29 have
selected sources to inspect, three are hardware/no-candidate nodes. Stack
proposals activate on __osBlockSum, __osPfsSelectBank and osPfsIsPlug. Five
ROM-bound leaves are usable by nine callers. The latter two stack proposals
are **not yet compile/repair replays**; prevalence is not transfer success.

Replays ran from preserved `eval/results/stack-callee-code-v1`, `-v2`, and `-v3`
snapshots. v3 bounds nested model feedback and records a call-result boundary in
the caller value graph, preventing attribution to stale pre-call registers. The
final replay preserved all measured verdicts. Drivers: `callee_replay.py`, `callee_prevalence.py`,
`callee_summary.py`; receipts refuse overwrites. Windows suite: **1,196 passed,
11 skipped**; targeted WSL suite including actual ROM/admission tests: **40 passed**.

Next useful work: verify the other two stack proposals; recover or validate
hardware-facing effect contracts before enabling them in unattended campaigns;
extend concrete execution beyond relocation-free leaves. The separate eager
semantic-panel artifact-lifecycle gap still exists. Changed code requires a
new campaign fork, not an unchanged resume of v14.

## Historical September 5 handoff

The following describes the earlier state; the measured follow-up above
supersedes its unresolved stack/callee statements.

Carry forward these historical facts:

- Old 24-function v11 is a compile-only DEV checkpoint: 8 exact, 12 compiling
  nonexact, 1 noncompiling, 3 hardware parked. It is not an exhausted full run.
- Original alLoadParam draft 29535 was automatically compiled by the bounded
  header-layout/type-constraint worker (selected 29693). Finite panels pass
  64/64 and 580/580; 72/480 positional text bytes still differ. No target body
  or earlier hand-written typed scaffold was supplied to that worker.
- alSynSetFXMix has an observed integer/float store error; earlier OSS diagnosis
  improved but source-edit targeting did not. __osBlockSum's stack-pointer call
  disagreement is not yet classified as C error versus harness/ABI debt.
- Passing tests, coverage, object exactness and full-ROM integration are separate.
  Keep semantic/byte champions, true attempt parents, heldout exclusions, and
  all failed receipts. Never put a nonexact candidate in the game build.

Change: completion_campaign now gives compile/frontend failures one explicit
zero-model compile_recovery visit per source hash, independent of model budget.
It invokes existing resilient worker mechanisms, not a second recovery engine.

Protocol:

1. Replay the same 24 DEV functions without importing historical winners; use
   normal mode, zero model calls. Inspect intake plus differential handoff first.
2. Diagnose distinct failures, then bounded model/exactness work where justified.
3. Extend to additional preselected DEV functions, preserving heldout exclusions.
4. Record measured results and remaining debt; no blanket semantic/SOLVED claim.

The initial v12 replay safely paused on a concurrent edit to solver/gfx_packets.py
after six accepted intake visits. The guPerspective inflight receipt is preserved.
Do not relabel this partial run complete or overwrite its pins. Subsequent work
uses a copied code snapshot so shared-worktree edits cannot contaminate a run.

Pre-snapshot validation: 66 focused tests passed; full live suite 1,163 passed,
9 skipped. These are software regression counts, not function matches.

## Finished bounded runs

Receipt paths are under `eval/results/`. No reference function body was supplied
to repair. No game source/header, ROM or SOLVED promotion changed. No worker is
left running. These are development runs, not heldout evaluation.

| Receipt | Result |
|---|---|
| `autonomy-wavefront-24-v13.json` | Fresh original cohort, no imported historical winners, 39 zero-model visits: 6 accepted exact; 6 sampled-pass nonexact; 1 differential disagreement; 7 noncompiling; 1 object-exact but frontend-rejected; 3 hardware parked. |
| `autonomy-wavefront-24-v14.json` | Explicit v13 fork after fixes, 39 zero-model visits: 6 accepted exact; **7 sampled-pass nonexact**; 1 differential disagreement; **6 noncompiling**; 1 frontend rejection; 3 hardware parked. Every compiling/frontend-passing nonexact selection received a panel. |
| `autonomy-gap-expansion-8-v1.cohort.json` | Eight zero-prior-attempt functions preselected by stable hash, two per game/SDK x medium/large stratum. Heldout exclusions enforced. |
| `autonomy-gap-expansion-8-v1.json` | Interrupted at __osPfsDeclearPage's dataflow stall; five completed intake receipts retained. Not a completed census. |
| `autonomy-gap-expansion-8-v2.json` | Same eight, explicit fork after convergence fix, 16 zero-model visits: one compiling/frontend-passing, sampled-pass nonexact candidate; seven noncompiling; zero exact. |
| `campaign-gap-audit-v1.json` | Disjoint stopping-stage audit of v14 and expansion v2, generated by `summarize.py`. Refuses unfinished checkpoints and stale semantic source hashes. |

The old v11's higher totals included earlier repairs. Lower fresh-start totals
do not delete those successes or reduce the global match count; they expose
recovery not reproduced by clean intake within this zero-model budget. Both final
campaigns are budget-paused, not complete or exhausted. Seven expansion compile
failures are **not** seven exhausted LLM failures: no model was called there.

## Changes that actually fired

1. **Zero-model dispatch.** v13 recovered alLoadParam as attempt 29883 from fresh
   intake, exactly reproducing the earlier standalone source hash
   `be80e2f39a543376f044c4c58447b7e3033e1798b401e43e40225109bf0e5ead`.
   It passes 64/64 automatic cases and still differs at 72/480 positional text
   bytes. v14 reverified it. The earlier 580-case audit is of this identical
   source; it was not rerun as part of these campaigns.
2. **Bitcast dialect recovery.** The existing m2c union lowerer now handles narrow
   integer promotion and supported negation/32-bit casts, at the original
   evaluation point, and runs in resilient worker normalization too.
   `alSynSetFXMix-bitcast-recovery-v1.json`: failed campaign source 29860 becomes
   compiling/frontend-passing 29905, **64/64 cases**, zero model calls. This avoids
   the earlier numeric-float-conversion candidate's six failures. Score 71.550;
   target 160 bytes/candidate 176, positional distance 92. Nonexact. v14 dispatched
   the same adapter automatically.
3. **Dataflow convergence.** A loop alternated a stack identity between unknown
   and `load(stack)+1`. The reduced old-code reproducer exceeded 2,000 block
   transfers. Monotone weakening drops disputed facts consistently; backedges
   also preserve the entry boundary. Original __osPfsDeclearPage now takes about
   **0.0031 seconds**, with 24 blocks and 85 memory accesses (79 resolved). Its C
   remains noncompiling, but the analysis no longer monopolizes the campaign.

`alSynSetFXMix-bitcast-exactness-v1.json` tested the existing exactness worker:
two OSS calls, 422 generated tokens. One proposal tried to edit read-only assembly
and was rejected; one C edit compiled without improving the selected score.
64/64 cases still pass. **No new byte-exact match** resulted.

## Stack-pointee debugger audit

`stack-pointee-audit-v1.json`, reproduced by `stack_probe.py`, compares a synthetic
read-one-word callee as an opaque call versus a literal inlined load:

| Candidate change | Opaque comparison | Concrete load comparison |
|---|---|---|
| Identical control | pass | pass |
| Same initialized buffer moved four stack bytes | fail | pass |
| Same stack address, buffer value changed 7 to 8 | pass | fail |

Equal opaque return overrides isolate the issue from the default address-dependent
return hash. Stack-pointer labels are not pointee/extent/alias contracts. This
does not prove __osBlockSum is correct: its draft declares a one-byte local and
passes its address with length 0x20 to __osSumcalc. Source storage and callee
modeling both need investigation. The production gate was **not relaxed**.

## Next concrete gaps

| Class | Representative evidence |
|---|---|
| Stack aggregates and extents | guPerspective has undeclared sp28; __osPfsSelectBank/osMotorInit retain stack-register pseudo-C; __osCheckPackId has an unknown stack record. |
| Complex bitcasts | New alSynAllocVoice contains `(bitwise f32) (sp2C->offset - 0x40)`, deliberately outside the simple-scalar adapter. A transfer boundary, not evidence against bitcast lowering. |
| Incomplete/connected types | renderSnowboardTrailEffect has incomplete SnowboardTrailState; prepareRaceResultsFlow has no solution in the current supplied-header/direct-field dialect. Other representations remain untested. |
| Globals/aggregate uses | drawRaceTypeSelectOption0Frame combines an undeclared tile-map symbol with aggregate-to-integer expressions. |
| Callee effects and edit binding | Opaque allocation helpers do not populate output pointers; stack-pointee contents/aliasing are not generally modeled. The exactness trial still attempted an assembly edit instead of a C edit. |
| Artifact lifecycle | Several fresh noncompiling workspaces lack normalized target-object dumps at eager semantic-panel construction. Lazy target/panel preparation is a separate wiring gap, not an unsupported game opcode. |

The sole expansion compiling function is drawCharacterSelectCourseExitPopup:
64/64 diagnostic cases, score 91.652, nonexact. Passing cases and branch coverage
are not all-input equivalence or concrete execution of opaque callees.

## Reproduction and verification

Preserved code snapshots `campaign-gap-audit-code-v1`, `-v2`, `-v3` are hashed by
the checkpoints. v2 adds the bitcast changes to v1; v3 adds the dataflow fix.
`pytest.ini` excludes generated result trees from duplicate test collection.

```sh
cd /mnt/c/Code/gameDecomp/eval/results/campaign-gap-audit-code-v3
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state /mnt/c/Code/gameDecomp/eval/results/autonomy-wavefront-24-v14.json \
  --resume --model-calls 0 --max-work-items 12
```

Use the expansion v2 state path to resume that cohort. Changed model budget/code
requires an explicit new fork. Rerunning the untouched-function selector would
choose a different cohort; do not call that the same experiment.

Final tests: **1,178 passed, 9 skipped**. Read-only evidence audit: 2,842/2,842
checkable stored accesses agree, zero disagreements; 1,728 explicitly uncheckable
accesses. This does not regenerate or prove every new symbolic fact. No full-ROM
integration was run in this audit.
