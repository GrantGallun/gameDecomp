# Expanded repair mechanisms — September 22, 2026

Two new composable representation repairs close `Fvibup` and `Fvibdown` through
the normal compiler search. Six existing residual generators are now reachable
through that same search. All proposals remain hypotheses until the frontend
and independent compiler-backed certificate pass.

## Mechanisms

| Mechanism | What it repairs | Guard |
|---|---|---|
| Unsigned float conversion | m2c's explicit signed conversion plus `2^32` fixup and allocated temporaries | Narrow unsigned local, adjacent single use, explicit f32 rounding, no side effects or conditional evaluation |
| Byte cursor rebasing | Deferred pointer advances and offsets that hide the target's cursor lifetime | Target self-increment on the corresponding argument register; complete constant byte views; unconditional insertion; every later offset adjusted |
| Existing residual generators exposed | Byte pointer step, element width, pointer difference scale, comparison operand order, comparison sentinel, signed comparison | Existing residual-specific guards, with edits confined to the selected function |
| MMIO poll and word access | Empty hardware polling loops and OR-tagged uncached word reads/writes | Encoded address/mask witnesses, big-endian o32, closed register chains, no shadowing; volatile accesses retained |

The two new families compose in either order. They do not recognize function
names, install type facts, or alter runtime admission. Both are registered in
`solver.regalloc_mutations`, so `regalloc_search`, its public tool action, and
the experimental scheduler share the expanded mechanism set.

`repair-mmio` is a separate registered source-transform action, included in the
scripted controller after wide reconstruction. It uses the current workspace's
function-scoped assembly and ABI, proposes source, and leaves exactness to the
controller's compiler and certificates.

## Equal-budget compiler comparison

Eight frozen development drafts; both arms have a 60-compile ceiling including
baseline, beam 3 and depth 4. The control excludes only the three new mutation
families. Each generated child records its actual parent receipt at generation;
repeated action labels are never treated as unique identities.

| Function | Previous menu | Expanded menu | Calls, previous / expanded |
|---|---|---|---:|
| `Fvibup` | Nonexact, best 73.261 | **Object-exact** | 60 / 29 |
| `Fvibdown` | Nonexact, best 73.958 | **Object-exact** | 60 / 29 |
| `releaseMenuAssetHandles` | Nonexact, best 98.269 | Nonexact, best 98.846 | 52 / 60 |
| `loadMusicSequenceBank` | Object-exact | Object-exact | 41 / 41 |
| `FrandPan` | Intake failure | Intake failure | 1 / 1 |
| `allocTranslationOnlyFixedMatrix` | Nonexact, best 95.865 | Nonexact, best 95.865 | 60 / 60 |
| `Fdistort` | Object-exact | Object-exact | 10 / 10 |
| `__MusIntProcessWobble` | Object-exact | Object-exact | 12 / 12 |
| **Total** | **3/8 exact** | **5/8 exact** | **296 / 242** |

Every control success is retained. Similarity numbers are diagnostic scores,
not percentages of verified bytes. The saved `FrandPan` draft conflicts with the
current header's function type and call-argument count in both arms; it is an
intake failure, not a search-budget failure.

The two newly solved functions were used to develop the generators. This is an
exposed development comparison, not a sealed holdout or broad transfer claim.
The other functions check useful coverage and regressions, with mixed results.
The unsigned-conversion scaffold also occurs in the larger
`__MusIntProcessEnvelope`, but its nested, multiply used values are deliberately
outside the current guard; it is not claimed as covered.

## Verification and inventory

The actual registered `regalloc-search` action, driven through `run_episode`,
reproduces both new exacts in 31 total compiler calls each (the controller and
runner baseline plus controller confirmation are all charged). Exported source,
current verdict, transcript hash and compiled candidate agree. All five successful
comparison outputs then pass fresh compilation in separate native WSL workspaces.

Four previously unrecorded main-inventory functions were independently compiled
again, receipts 95864–95867:

| Function | Contribution | Assistance |
|---|---|---|
| `Fvibup` | New generator result | Source-independent |
| `Fvibdown` | New generator result | Source-independent |
| `loadMusicSequenceBank` | Existing search also finds it | Project-header-assisted |
| `__MusIntProcessWobble` | Previously known private solution, now recorded | Project-header-assisted |

`python -m eval.status`: object-exact **355 → 359**, SOLVED **263 → 265**,
header-assisted **12 → 14**, reference-type-assisted 25 and recovered 55 unchanged.
No existing exact function was removed. The increase is four inventory entries;
only two are improvements attributable to the newly added generators.

These are candidate-level object certificates. Production game TUs and ROM
integration remain separate. No reference function body, model call or training
run was used. All experiment attempts are marked training-ineligible.

## Automated hardware repairs

The public `repair-mmio` action reproduces the previously manual repairs of
`osEPiRawReadIo` and `osEPiRawWriteIo`. Both pass frontend checks and schema-3
ROM-backed function certificates in independent native WSL workspaces. They are
**function-exact, not object-exact**: literal addresses replace relocation
expressions. These two were already known; they do not add inventory entries in
this experiment. No whole-ROM integration or device behavior is claimed.

Fresh assembly-generated drafts of `osPiRawReadIo`, `__osSiRawReadIo` and
`__osSiRawWriteIo` are declined without changes. Each hardware run therefore uses
nine compiles: five baselines, two generated repairs, and two independent
confirmations. The reviewed run repeats this result, including the same source
hashes. The action currently covers a narrow combined poll/access shape, not
general hardware code.

## Audit and review

The core experiment logs 653 private compiles plus four inventory compiles:
48 exploratory probes, 538 paired-search calls, 62 public-action calls, five
independent confirmations and four inventory confirmations. Failures and baseline
compiles are included. The audit checks source hashes, exact certificates, 630
explicit private parent edges and the complete cost total of **657**.
Both hardware runs add 18 compiles and eight explicit parent edges, bringing the
complete experiment to **675 compiles**. The audit validates all saved source
hashes, parent links, certificates, and the hardware confirmations' ROM/object
input hashes.

After measurement, guards were tightened to reject conditional evaluation
(`?:`, `&&`, `||`), masked literal/comment expressions, and inserting an assignment
ahead of a C89 declaration. The measured implementation is archived as
`representation-before-review.py.txt`. `review-equivalence.json` verifies identical
outputs from all three affected families at all 47 expanded states in the paired
and public runs. This is a generator-stream equivalence check, not an additional
compiler run or a claim that the post-review source hash was the measured hash.

Independent code review found additional hardware evidence guards to tighten:
calls, clobbers, zero-register destinations, reserved encoding bits, alternate
entry labels, symbol shadows, and noncode source text. Each new regression was
observed failing before its fix. The two measured hardware implementations are
archived; the audit replays the final implementation on all ten measured inputs
and confirms byte-identical generated C. No Critical or Important review issues
remain in the reviewed scope.

Final focused verification: **250 tests passed in WSL**, including host-compiler
execution checks and tool/search integration. On Windows, the broader focused
run had 242 passes and three failures in the parameterized
`test_residual_alternatives.py::test_new_lifetimes_compile_and_execute_like_baseline`:
launching the installed Swift clang was denied by the environment. Those three
checks pass in the WSL run. The final representation/MMIO-only Windows run also
passes all 47 tests. A full repository suite was not rerun for this change.

Artifacts: [paired comparison](comparison/report.json),
[public action and confirmations](public-verification.json),
[inventory](inventory-receipts.json), [status](status-after.md),
[audit](audit.json), [guard equivalence](review-equivalence.json).
Hardware: [initial run](mmio/report.json), [reviewed run](mmio-reviewed/report.json).
Tests: [WSL result](tests-wsl.txt).
