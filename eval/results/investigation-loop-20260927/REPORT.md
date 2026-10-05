# Investigation loop, September 27

An integrated first version is implemented in the development checkout. It uses
the existing campaign and verifier. Live experiments are isolated native WSL
copies; the main frozen campaign has not been upgraded or had its results changed
by these canaries. No model weights, KB evidence, or real game build-path C changed.

## Implemented behavior

1. `inspect_compiler` compiles the active candidate with the resolved TU recipe,
   checks correspondence to its ordinary candidate object, and captures actual
   pre-as1 assembly. Non-reproducing observations are explicitly incomparable.
2. `replace_source` permits complete source reconstruction alongside bounded edits,
   without granting access to reference C. Frontend and object verification remain
   authoritative. The default expanded budget is 12 turns and 16 compile units.
3. Append-only notebooks retain hypotheses, source lineage, failed actions and
   measured residual changes across visits. Target/evidence/compiler identities
   scope retrieval, including IDO binary hashes. Revised inputs reopen bounded
   investigation visits. Model explanations stay unverified and training-ineligible.
4. Shared measured issues can generate scoped engineering requests. The fast
   controller now dispatches the actual investigation/capability profile and
   imports requests. The existing isolated worker requires a failing reproduction,
   regression checks and separate transfer checks; it cannot deploy its own repair.
5. `execution_case` accepts bounded register/player-memory inputs, runs original
   instructions first, and compares the candidate. Completed cases are reused for
   later candidates in the same investigation. Failures can disprove candidates;
   finite passes retain execution and domain limitations.

The action menu now appears before the large source context. Structured responses
have action-specific required fields and no conflicting assistant prefix. Repeated
lookups are masked. Prompt preflight shrinks optional history/notebook views while
preserving durable events and current verification context; an irreducibly large
request stops once instead of spending every remaining turn on the same error.

## Measurements

The final native integration suite passed **188 tests, one skipped**, including
the context and identity fixes (`final-native-tests.xml`). The skip requires an
unavailable campaign KB fixture. The current portable subset passed **137 tests,
five skipped** on Windows (`final-python-tests.xml`); Windows skips include native
MIPS/controller checks exercised under WSL. Scoped `git diff --check` passed.

Two real native compiler controls reproduced the ordinary candidate objects before
capturing pre-as1 output. The execution control found a behavioral disagreement for
`drawRaceTypeSelectPortrait`; the chosen `__osPopThread` input did not complete the
target. These are tool controls, not source-reconstruction successes.

Live canaries use `gpt-oss:20b`, existing assisted candidates/headers, a private copy
of the campaign database, and pinned code snapshots. Selected functions had no prior
raw exact in either ledger at selection. This is exposed development evidence,
not a held-out comparison or evidence of generalization.

Initial runs mostly repeated lookups. Revision v3 exercised complete replacements.
Revision v4 exercised six valid source-edit proposals across two functions, with
zero new exacts and no improvement over the best source available when model repair
started. `drawRaceTypeSelectPortrait` rose from 71.25 to 87.083 during deterministic
preparation; that gain is not attributed to model repair. Context errors in v4 led
to the final prompt-budget fix. Revision v5 tested that completed implementation:

| Function | Starting score | Retained score | New exact |
| --- | ---: | ---: | --- |
| `__osPopThread` | 54.286 | 54.286 | No |
| `drawRaceTypeSelectPortrait` | 71.250 | 87.083 | No; deterministic preparation gain |
| `requestMusicSequenceBank` | 0 | 0 | No |
| `__osSiDeviceBusy` | 62.636 | 99.091 | No; model-proposed source gain |

All four completed their 12-turn budgets with no context-budget errors. Total
function wall time was approximately 222 seconds. The last case is a concrete
success for the mechanism: after inspecting the compiler, the model introduced a
local array to recover the stack frame. Both original and changed candidate phase
captures reproduced their ordinary objects. The remaining diff uses `t6` where
the target uses `a0` in a load and mask; 3 of 48 text bytes differ. Frontend checking
passed. Semantic execution remains unavailable because `SI_STATUS_REG` requires a
hardware-register environment. None of this is counted as an exact match.

The same run still spent 25 of 48 turns on invalid or duplicate requests. This
small assisted trial supports useful compiler-driven hypotheses, not reliable
autonomous completion. Separate Codex-directed controls tested plain versus
`register` scalar temporaries, then removed extra stack storage and tested a
volatile load. Scores were 80.000, 97.273, 98.182 and 98.182: none beat 99.091.
The compiler answered the hypotheses directly: the plain temporary spills;
`register` changes both value and address allocation; removing the array restores
the eight-byte frame; volatile qualification does not change this remaining code.
These negative experiments and phase captures are saved in their own notebooks,
separately from autonomous local-model results.

Full summaries, events, notebooks and code hashes are under `receipts/`. Native
compiler artifacts and complete proposal/attempt ledgers remain under
`/home/grant/decomp/experiments/investigation-loop-20260927/`. Earlier packaging and
model errors are retained, not removed from the record.

## Remaining limits and next decision

- Stronger joint caller/callee interface reconstruction is still open. This version
  shares existing hypotheses and validates candidates individually; it does not
  implement transactional multi-function source reconstruction.
- Execution inputs are synthetic and supported only within the existing emulator
  environment. Global writes decline without independently admitted writable
  extents. Device state, missing execution cases and arbitrary harness changes still
  need scoped implementation work. Cases do not establish valid game-domain values.
- Shared engineering requests currently require an existing failing reproduction.
  Automatic residual-family clustering, independently generated positive tests and
  automatic promotion into a new frozen run are not implemented here.
- Pre-as1 captures diagnose generated candidates. They do not recover the target's
  historical optimizer/register-allocation trace.
- More tools and more turns have not yet demonstrated higher exact-match yield.
  The next useful experiment compares investigator policies on the same fixed
  functions, measuring verified gains, distinct discriminating experiments and
  repeated failures per compile/model budget.

Enable the implementation for a newly frozen campaign with
`--scheduler investigation-v1 --investigation-turns 12`; optional
`--investigation-compiles 16 --investigation-seconds 900` control the independent
budgets. Seconds bound admission of new actions; in-flight tools have their own
timeouts and preparation/final validation are separate. Existing configurations
retain their behavior. Do not silently replace a running campaign's pinned code.
