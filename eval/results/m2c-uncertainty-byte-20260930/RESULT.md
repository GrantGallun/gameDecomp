# Byte verified m2c uncertainty guidance

Pointing the model to uncertain C expressions and verified instruction bytes is
implemented and tested, but it does not yet improve seed validity over ordinary
model repair. On 32 further fresh functions, both model arms produce 19 valid
seeds from 18 valid intake seeds and retain four exact candidates. Guidance
produces one small valid assembly-score improvement. Keep this connection
experimental; the next useful development is constrained alternative
interpretations with their dependent uses, rather than additional prompt prose.

## What was connected

`solver.m2c_uncertainty.Observer` records m2c's unrecovered address additions and
late pointer-store views without extra formatting, type unification or changes
to ordinary output. Reports carry the emitted C hash. Explicit draft extraction
keeps only surviving lexical expression occurrences; these are associations,
not proof of instruction ownership.

`solver.m2c_source_binding.SourceBoundObserver` registers explicit assembly
snapshots to resolve m2c's shortened filenames. It rejects ambiguous basenames
and checks available annotated instruction words against the isolated target
object. Contradictions remove byte guidance while preserving ordinary C.
This trial's trigger admits only surviving expressions with verified target
words. All admitted observations are address additions: store observations
currently have no instruction association and are excluded.

The actual campaign intake and existing model repair loop run in private native
WSL workspaces. Original candidates remain archived. Three arms share the same
trigger: ordinary repair, uncertainty-guided repair, and one optional frozen
byte-address redraft. Each model arm permits three root-level proposals with
the same draw seeds and local gpt-oss:20b model digest. The model receives the
existing assembly, candidate and diagnostics; guided repair also receives the
bounded uncertainty packet and a request for one local edit. The existing schema
can still admit multiple textual replacements; this is not hard enforcement of
one operation or an exhaustive permutation search. No production caller or
campaign default enables the observer or guidance.

## Sample and results

Selection was frozen before candidate generation. These 32 functions exclude
the previous 12, 64 and preliminary 48 development functions, plus all sealed
evaluation names. Sixteen broad functions come from TUs outside all earlier
panels; sixteen use a shifted-address binary pattern and may share earlier TUs.
Together they cover 26 TUs, with at most two functions per TU. No bad outcome is
replaced. These are fresh functions for this adapter's development evaluation,
not a claim that every solver component has never encountered them. No reference
C enters generation or prompts.

The earlier 48-function comparison lacked explicit byte associations and is
reported [separately](../m2c-uncertainty-20260930/RESULT.md). Its prompts and results
must not be pooled as verified byte-guidance evidence.

| Arm | Valid seeds across all 32 | Isolated exact candidates | New compiler attempts | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Ordinary intake | 18 | 4 | 72 | 0 |
| Ordinary model repair | 19 | 4 | 49 | 27 |
| Byte-guided model repair | 19 | 4 | 45 | 27 |
| One byte-address redraft | 18 | 4 | 40 | 0 |

The trigger fires on nine of the sixteen address-pattern functions and none of
the sixteen broad functions. All broad arms simply reverify and retain their
roots: fourteen valid seeds and three exact candidates. In the address group,
each model arm has five valid seeds and one exact candidate; the byte redraft
arm has four and one. All arms complete, and no valid intake seed is lost.

Both model arms obtain the additional valid seed on `Fnext`. Only guided repair
improves an already valid seed's score: `compressRaceRecordReplayData`, from
86.819 to 86.889. Its model tries both an incorrect scaling rationale and a
plausible two-byte offset correction; compiler comparison prefers the latter
candidate. This is a small full-function assembly-score improvement, not a
semantic proof, new exact match or evidence of faster campaign convergence.
The 54 logged proposals comprise 30 schema-valid responses, eleven duplicates
and thirteen invalid responses. Schema validity does not mean valid C.

Valid means both native IDO compilation and the strict frontend check pass.
Available valid candidates are audited separately from the existing repair
ranking's selected candidate; their counts agree here. Exact counts require
independent object certification and describe isolated candidates, not global
SOLVED counts or whole-ROM integration. No full campaign continuation or
differential execution was performed.

## Retention and byte binding limits

All 69 successful translations in the new intake equal ordinary m2c output,
with zero observation or byte-binding errors. The audit checks 168 original
instruction-word associations; these include repeated observations across
translation passes, not 168 distinct instructions.

A separate dry run preserves every ordinary candidate label and source hash
on the earlier 64-function panel. Both motivating residuals still trigger:
`drawScaledAssetTableSprite` has six verified regions per ordinary variant;
`initRacePlayerLandingSnowSpray` has two in each of four variants. Overall,
29 functions have verified regions, with 1,382 checked word associations.

Four translation passes on `drawTimeTrialHud` and `initRaceSceneFlow` decline
byte binding. Linked disassembly can contain a resolved address where an
unlinked target object contains a relocation placeholder. For example,
`drawTimeTrialHud` annotates `3C068012` while the isolated target contains
`3C060000`. Those words cannot be accepted by this exact-word check. Guidance
remains unavailable for that pass; ordinary C remains unchanged.

The first two dry-retention harness runs incorrectly let this verification
exception decline ordinary drafts. Review reproduced identical ordinary output
and historical candidate hashes, identifying a harness exception rather than
observer mutation. The final harness uses the preparation runner's existing
catch, logs the reason and preserves C. Both failed runs and the diagnostic are
retained. Product modules and the 32-function trial were unaffected.

## Verification and next development

`verify.py` audits all 206 compiler attempts, 109 recomputed compiled-object
certificates, 54 proposals, 27 guided packets, source and assembly snapshots,
byte associations, parent edges, original roots, per-arm budgets, frozen code
hashes, selection exclusions and retention. Both private knowledge tables have
zero rows. The final native observer, binding, repair and fixture suite passes
71 tests; Windows passes six and skips ten native-dependent observer tests.
Receipts are in `test-results.json`. All artifacts are training-ineligible; no
KB, real build-path C or live campaign is changed.

The missing bridge is a bounded set of compatible C interpretations for an
observed operation, together with constraints on its uses. The model still
tries unrelated provisional type substitutions, repeats edits, or repairs one
use while leaving a callee or assignment incompatible. A useful next experiment
would enumerate byte-view, stride-supported array-view and unchanged choices,
include relevant assignment/call constraints, and let the model select or order
those choices before compilation. This recommendation is an inference from the
logged failures, not a confirmed production pattern. Relocation-aware binding
and instruction associations for stores are separate evidence gaps.

Native roots remain under `/home/grant/decomp/experiments/` as
`m2c-uncertainty-byte-panel-20260930-v1`,
`m2c-uncertainty-byte-intake-20260930-v1`,
`m2c-uncertainty-byte-comparison-20260930-v1` and
`m2c-uncertainty-byte-retention-20260930-v3`. `portable/` contains main receipts;
`portable.zip` preserves full sources, objects, databases, snapshots and failure
artifacts, with archive integrity and SHA-256 recorded in `portable/archive.json`.
