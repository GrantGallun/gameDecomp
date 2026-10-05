# Storage repairs: two reproducible development fixes, transfer still unproven

The normal mutation stream now proposes register-qualified local storage,
immediate field-address reuse, and reuse of an otherwise unused parameter.
These close two previously unresolved generated drafts: `__osDequeueThread`
and `osGetThreadPri`. Both are header-assisted, frontend-valid and certified
object-section exact, including relocation checks. They also succeed through
the public `regalloc-search` action, with independent recompilation.

The separate follow-up panels produced **zero additional repair-attributed
exacts**. A parameter-first revision regressed a previous success; that revision
is preserved, diagnosed and corrected. This is useful repair engineering, not
evidence of autonomous RSI or of broad transfer.

## What caused the original failure

The frozen `__osDequeueThread` draft had two pointer locals that IDO spilled under
this target's actual `-O1 -mips2` recipe. The target has an eight-byte frame too:
the residual was extra local stack accesses, not merely the existence of a frame.
Its normalized similarity score was 27.600.

| Causal change | Score | Certified exact |
| --- | ---: | --- |
| Original generated draft | 27.600 | No |
| Natural loop cleanup | 34.182 | No |
| Register pointer-to-link only | 57.600 | No |
| Register node pointer only | 53.167 | No |
| Both register declarations | 99.938 | No |
| Field-address reuse alone | 27.600 | No |
| Both register declarations plus field-address reuse | 100 | Yes |

After fixing storage, the remaining load used the node register instead of the
already established address register. The adjacent statements `q = &p->next;`
and `p = p->next;` became `q = &p->next; p = *q;`. This composition was confirmed
in a fresh workspace. A natural-loop-plus-register alternative also matched
and was independently confirmed. The ten causal probe calls are in `probe.json`
and `probe-alias.json`; failures are included.

The first follow-up exposed a related but different case. `osGetThreadPri`
copied its parameter to a local, then never used the original parameter again.
Removing that copy and using the parameter's own local storage changed 42.417
to certified exact. Baseline, repair and confirmation consumed three logged
calls in `parameter-probe.json`. Once inspected, this function became a
development case rather than transfer evidence.

## Implementation and boundaries

`solver/storage_repairs.py` is wired into `regalloc_mutations.variants`, so both
the existing beam search and experimental scheduler can compose the proposals.
No function name selects a repair. Renamed motivating shapes fire in tests.

- `register_storage`: candidate-only stack `lw/sw` in the visible diff triggers
  up to eight eligible leading C89 locals, one combined proposal and individual
  ablations. The diff is a search hint, not a proof of original storage.
- `address_reuse`: a changed load base register triggers adjacent same-field
  local pointer statements. Unbraced conditional dominance, unresolved global
  bases, multihop fields and shadowed locals decline.
- `parameter_reuse`: requires the first executable statement to copy a simple
  parameter to a local of the same declared type, with no other original
  parameter uses. Address escapes, member/tag namespace collisions, complex
  callback declarators and unsupported scalar typedefs decline.

These are conservative source hypotheses, not a complete C semantic rewriter.
Only compilation, frontend validation and the existing certificate accept them.
Thirty-seven focused storage tests cover the motivating firings, composition,
bounded generation and reviewed refusal cases.

## Equal-budget measurements and the failed revision

Each arm used the same frozen initial source, target, compiler recipe, assistance
tier and 32-call ceiling. Scheduling stayed at the prior experimental depth
penalty 1.18754. Every version used a separate frozen native WSL code snapshot,
private attempt database and isolated build workspaces. Exact sources received
independent confirmations. There were no model calls.

**V1, register storage and address reuse:** `__osDequeueThread` changes from
nonexact after 32 calls to exact in 3. All five previously known successes are
retained at the same costs: Fvibup, Fvibdown, Fdistort and loadMusicSequenceBank
at 3 each; Wobble at 12. Three prior negative cases remain negative.

The first follow-up selected eight functions before generating drafts; six had
usable existing target workspaces. `osGetThreadId` and `osSetTime` were unavailable.

| First follow-up | Control -> expanded calls | Result |
| --- | --- | --- |
| `__osPopThread` | 2 -> 6 | Nonexact; best score 34.444 -> 54.286 |
| `osGetThreadPri` | 2 -> 6 | Nonexact; 42.417 in both |
| `osSetThreadPri` | 32 -> 32 | Nonexact; 91.091 in both |
| `osGetTime` | 1 -> 1 | Does not compile; unresolved global/type context |
| `osSetEventMesg` | 1 -> 1 | Does not compile; unresolved global/type context |
| `osAiGetLength` | 1 -> 1 | Nonexact; 96.667 in both |

V1 therefore has one motivating gain, no known losses and zero follow-up gains.
The completed run cost 367 calls including six independent confirmations.

**V2, parameter reuse first:** its motivating `osGetThreadPri` becomes exact
in 2 calls versus control's 6 nonexact calls. However, `__osDequeueThread` regresses
from exact in 3 to nonexact after 32. Its first parameter-reuse edit raises the
score from 27.600 to 49.632, then register storage raises it to 90.812. The depth
scheduler continues along this branch and misses the established exact route.
An improving local score was insufficient evidence that this ordering was good.
V2 cost 556 calls including nine independent confirmations and is not the final
ordering.

**V3, corrected order:** register storage and address reuse precede parameter
reuse. `__osDequeueThread` again closes in 3, `osGetThreadPri` closes in 3, and
all six known successes are retained at their earlier costs. V3 repeats V2's
panel to check the correction; it is not another fresh transfer panel. Its
528 calls include nine independent confirmations. Across all 18 cases, control
is 8/18 exact and expanded is 9/18, with the sole gain on the motivating function.

| Second follow-up, same result in both arms and V2/V3 | Calls per arm | Diagnosis |
| --- | ---: | --- |
| `osStopThread` | 32 | Nonexact, 72.896 |
| `osDestroyThread` | 32 | Nonexact, 66.597 |
| `osStartThread` | 32 | Nonexact, 83.083 |
| `osVirtualToPhysical` | 1 | Initial draft already exact |
| `osViSwapBuffer` | 1 | Does not compile: `__osViNext` has unresolved structure type |
| `osViSetMode` | 1 | Does not compile: same unresolved structure context |
| `osCreateMesgQueue` | 32 | Normalized score 100, certificate rejects relocation ordering |
| `osPiGetCmdQueue` | 1 | Initial draft already exact |

Parameter reuse never fires on the eight second-panel traces. Its zero transfer
gain is a coverage result, not evidence that it fails every applicable case.
The two already-exact drafts are not repair gains. `osCreateMesgQueue` is not
counted as exact merely because normalized assembly scores 100: the certificate
does not accept its different HI16/LO16 relocation ordering. That deserves a
separate linker-aware investigation, without weakening this experiment's gate.

## Public tool verification, cost and audit

The registered `regalloc-search` action, beam 3 and budget 32, reaches
`__osDequeueThread` in 13 compile callbacks and `osGetThreadPri` in 5, including
their baselines. A separate workspace confirms each emitted source: 20 total
calls in `public-verification.json`. This checks the ordinary tool path, not
only the experimental scheduler.

| Stage | Logged compile calls |
| --- | ---: |
| Initial causal probes | 10 |
| Parameter-copy probe | 3 |
| V1 paired measurement and confirmations | 367 |
| V2 paired measurement and confirmations, including regression | 556 |
| V3 corrected measurement and confirmations | 528 |
| Public action runs and confirmations | 20 |
| **Total** | **1,484** |

`audit_all.py` passes. It binds 102 paired worlds and all attempts to stored
sources, verdicts, full certificates, explicit parent receipts and costs;
replays each measured schedule; audits all probes and public tool attempts;
and verifies that the changed current modules match the final frozen snapshot.
There are 24 paired independent confirmations, two public confirmations and
three additional causal-probe confirmations. Shared probes are counted once.

The focused suite passes **183 tests on Windows and 183 on WSL**:

```text
python -m pytest -q tests/test_storage_repairs.py tests/test_structural_mutations.py tests/test_representation_repairs.py tests/test_regalloc_mutations.py tests/test_regalloc_search.py tests/test_search_evolution.py tests/test_search_scheduler.py tests/test_search_replay.py tests/test_tool_registry.py
```

All records remain training-ineligible. Inputs came from generated drafts,
target assembly and project headers; no reference function bodies were used.
The targets are previously exposed SBK1 library/development functions, not a
sealed or family-disjoint holdout. Production TUs and the main KB were not
mutated, and no global inventory increase or whole-ROM match is claimed.

## What this tells us to improve next

Useful progress here was identifying two concrete missing transformations and
making them repeatable through the ordinary solver, while catching a search
regression before accepting the final ordering. More model training would not
by itself establish whether these missing actions were available or selected.

The remaining evidence separates three needs: restore missing type context for
the VI drafts; diagnose new source transformations for the unresolved thread
functions; and examine the certificate-level relocation residual separately
from normalized assembly scores. The next transfer experiment should select
uninspected cases exhibiting the relevant storage residual before looking at
their solutions, freeze the repair stream, and retain this regression archive.
Only a measured improvement on that separate panel would justify a broader
capability claim. Making the system perform this diagnosis and proposal cycle
itself remains unfinished RSI work.
