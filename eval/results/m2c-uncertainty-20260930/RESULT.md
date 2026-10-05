# Preliminary m2c uncertainty comparison

The passive observer works, but this comparison does not establish a benefit
from uncertainty guidance. Ordinary model repair and guided repair each produce
27 compiler-and-frontend-passing seeds across 48 scheduled functions, starting
from 24 valid intake seeds. Ordinary repair retains five isolated exact
candidates; guided repair retains four. The later
[byte-verified comparison](../m2c-uncertainty-byte-20260930/RESULT.md) tests the
user's byte-level proposal more directly.

## Scope and sample

Selection excludes the previous 12 development functions, the subsequent 64
transfer functions and sealed evaluation names. Thirty-two broad functions use
TUs outside both earlier panels; sixteen additional functions use a binary
shifted-address pattern and may share prior TUs. Together they cover 41 TUs.
These are fresh functions for this adapter's development sample, not proof of
unseen inputs for every historical solver component. No reference C was supplied
to generation or prompts. Selection was frozen before C generation, with no
outcome-driven replacements.

The initial attempt to obtain a larger all-new-TU address sample found too few
eligible functions. The revised group definitions were frozen before candidate
generation. The inherited panel script's introductory all-TU exclusion wording
is overbroad; its saved selection policy and the group definitions above describe
the actual exclusions.

The observer forwards m2c operations once, records existing formatted
expressions, and does not introduce extra formatting or type unification. All
103 successful observed translations equal ordinary m2c stdout. A separate dry
run preserves all 64 previous ordinary candidate panels, with zero observer
errors; both previously useful residuals still trigger.

m2c shortens instruction metadata filenames. This first observer cannot bind
those names to the saved assembly paths, so its packets contain operation
associations and C occurrences but no explicit instruction words or byte
addresses. The model still receives ordinary assembly. This is a preliminary
operation-guidance comparison, not a verified byte-guidance experiment.

## Native outcomes

All three arms use the same surviving-expression trigger, which fires on 12
functions. Non-triggered functions retain their reverified intake root. Each
model arm permits three root-level proposals with identical per-function draw
seeds, temperature 0.4, and the existing structured repair schema. The local
model is gpt-oss:20b. The deterministic arm offers at most one frozen byte-address
redraft; that redraft can affect multiple operations. Exact results stop early,
and malformed or duplicate proposals do not consume unnecessary compiles.

| Scheduled group | Ordinary valid seeds | Guided valid seeds | Byte redraft valid seeds | Ordinary exact | Guided exact | Byte redraft exact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Broad 32 | 18 | 17 | 17 | 4 | 4 | 4 |
| Address 16 | 9 | 10 | 8 | 1 | 0 | 1 |
| All 48 | 27 | 27 | 25 | 5 | 4 | 5 |

Ordinary repair makes 67 new compiler attempts and 34 model calls; guided repair
makes 73 and 36; the byte redraft arm makes 59 compiler attempts and no model
calls. Intake adds 121 compiler attempts, giving 320 audited attempts overall.
The unequal call totals reflect early exact termination, not unequal ceilings.

The byte arm records one incomplete redraft for `initRaceCameraFollowPlayer`,
whose lowering lacks a required witness. Its original root remains archived and
the function stays in the denominator. Both model arms complete. The 70 model
receipts contain 44 schema-valid proposals, 21 duplicates and five invalid
responses; schema validity does not imply valid C.

These are available valid candidates within each arm, audited separately from
the repair loop's selected candidate. The existing ranking can prefer byte
equality over frontend validity; no such selected-versus-available count
divergence occurs in this run. No valid intake seed is lost. Exact counts describe
isolated object certificates, not global SOLVED or whole-ROM integration.

## Findings and verification

Guidance changes which type hypotheses the model tries, without reliably
resolving their dependencies. For example, guided repair changes the temporary
type in `updateEndingTommyWaitThenFinalPhase` but leaves another use incompatible;
ordinary repair finds frontend-passing alternatives. Conversely, guidance finds
a passing candidate for `getRacePlayerPathOffset` that ordinary repair misses.
Neither example establishes original source types or layouts.

`verify.py` checks all 320 attempt receipts, 157 independently recomputed object
certificates, 70 model proposals, prompt/source bindings, parent edges, source
archives, frozen code identities, selection exclusions and original candidate
retention. There are zero observer errors and zero valid-seed regressions.
The private database has zero evidence or inference rows. All artifacts are
training-ineligible. Production calls, campaign defaults, KB and real build-path
C are unchanged.

The native roots are retained in `/home/grant/decomp/experiments/` as
`m2c-uncertainty-panel-20260930-v2`, `m2c-uncertainty-intake-20260930-v1`,
`m2c-uncertainty-comparison-20260930-v1` and
`m2c-uncertainty-retention-20260930-v1`. `portable/` contains the main receipts;
`portable.zip` preserves full sources, objects, databases and snapshots with an
integrity check and hash recorded in `portable/archive.json`.
