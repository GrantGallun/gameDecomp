# Next histogram: call repairs open the object-matching stage

September 22, 2026. Same 200 saved candidates from
`clean-composition-20260922/final.json`. Project headers remain available: these
are header-assisted results, not header-free or global campaign counts. All
compiles ran in isolated native WSL workspaces with private attempt logging.

## Result

| Same 200 candidates | Before | After intake | After object repairs |
|---|---:|---:|---:|
| IDO compiles | 47 | 60 | **60** |
| Frontend passes | 45 | 58 | **58** |
| Both compilers pass | 44 | 57 | **57** |
| Object-exact | 5 | 6 | **7** |
| Frontend diagnostics | 1,960 | 1,853 | **1,853** |
| Functions with call-arity errors | 68 | 17 | **17** |

Thirteen additional functions pass both compilers. Fifty-eight candidates have
fewer diagnostics. No retained candidate loses IDO acceptance, frontend
acceptance, exactness, compiled similarity, or complete error-count quality.

![Next failure histogram](histogram.png)

There are now **143 compilation-blocked candidates, 50 compiled nonmatches, and
seven exact objects**. The compiled-nonmatch bucket grows from 39 to 50 because
13 functions enter it and two reach exact. Its larger bars are not regressions.

## What was blocking this round

The call-arity bucket combined different operations. Some drafts carried spare
register values as excess call arguments; others represented one 64-bit argument
as two 32-bit words. The intake sequence did not invoke the existing
`solver.frontend_repair` code that handles supported ABI packing and related
representation repairs. A baseline census found proposals in 15 functions,
including 21 split 64-bit arguments, before wiring changed.

The new `frontend_abi` adapter exposes that existing implementation with fresh
full diagnostics and the workspace target object/assembly. The separate
`call_arity` adapter uses `solver.call_arity_repair`: it projects excess arguments
onto an unambiguous included header contract while retaining their evaluation in
a comma expression. It preserves the return value and declarations. It requires
supported o32 word parameters, matching source/target direct-call counts, and
current diagnostic locations. Missing arguments, wide or ambiguous contracts,
callee macros/shadows, complex discarded expressions, and overlapping edits
decline. These are compiler-tested hypotheses, not recovered callee signatures.

The same bounded composition search revisits owners on changed sources. The
reviewed arm uses at most three rounds, 12 child attempts and a beam of three.
It logs **424 children**; 194 functions stop at a fixed point within that bounded
archive, and six stop exact. This is not proof that other alternatives cannot help.

## Two new exact objects

1. **`addSchedulerClient`**: projecting the extra argument to `osSetIntMask`
   clears the compiler blocker and reaches exact. [Final source](states-confirmed/addSchedulerClient/final.c).
2. **`drawEndingCreditsTumblingSnowboard`**: call repair first admits the function
   at 99.688 similarity. Its sole object difference is `addiu a0,a0,0x18` versus
   `0x5a0`: C scales `arg0 + 0x18` by the 60-byte inferred struct. The new shared
   `byte_pointer_offset_rewrites` generator tests an explicit byte view and
   reaches exact in one compile. [Final source](states-confirmed/drawEndingCreditsTumblingSnowboard/final.c).

The byte-offset generator requires corresponding scaled argument-register
instructions, a matching literal expression and supported parameter slots. It
declines parameter mutation, escape and shadowing, including parenthesized and
comma-declarator forms. The catalog replay examines all 51 compiled nonmatches
after intake. It emits two proposals: the snowboard exact and an improvement to
`updatePatrolCourseObject`, 93.132 to 93.158, which remains nonexact.

Both new exacts reproduce in separate fresh workspaces and pass the frontend.
Their `mips_object_section_certificate` covers allocated text/data/BSS and
relocation expressions under the same link environment. Neither is a whole-ROM
or game-integration claim. Read-only research-DB checks found zero previous exact
attempts for each function. Their new attempts remain in the private experiment DB.

## What causes most failures now

Type and layout representation dominate. **98 functions** have at least one
member-access class; adding declaration conflicts covers **115** functions.
Those sets overlap. The largest individual groups are:

| Observed class | Affected functions | Functions with only this class reported |
|---|---:|---:|
| Declaration conflict | 44 | 8 |
| Member access on `void` | 43 | 10 |
| Member access on a scalar or array | 42 | 5 |
| Missing member | 37 | 1 |
| Invalid binary operands | 34 | 12 |
| Indexing a non-pointer | 31 | 2 |
| Call arity | 17 | 1 |

The remaining call errors include eight missing-argument diagnostics in four
functions, plus 15 excess-argument diagnostics in 13 functions that fall outside
the supported correspondence/contract shapes. The next large repair targets are
the actual parameter/local representations behind member errors and declaration
conflicts. Twelve functions report only invalid binary operands, ten only
member-on-void, and eight only declaration conflicts; each may still need several
repairs. Diagnostic counts and class counts are not distances to a match.

Of the 50 compiled nonmatches, 49 have register observations and 47 structural
observations. These are overlapping heuristic labels, not proven root causes.
The raw object diff identified the precise byte-unit issue above.

## Verification and receipts

The final related intake/frontend/header/ABI check passes **224 tests**. The final
rewrite check passes **102 tests**, including the last nested-parenthesis guard
regressions. The full suite reports **4,348 passed, 13 failed, 41 errors, 7 skipped,
1 xfailed and 2 xpassed**. No failed test/phase identity is new relative to the
recorded baseline. Two old training-control failures are absent; no repair gain
is attributed to that. The full run began before the final whitespace-only guard
tightening; that tightening is covered by the final 102-test check and complete
object replay. `tests-failures.json` retains the full pre-existing failure details.

The final audit checks **748 private attempts**, every stored source hash and
parent edge, all 624 nodes in the confirmed intake search, and final source/verdict
identities. The immutable evidence snapshot contains 72,845 rows and has the same
SHA256 as the prior round. Intake implementations and the separately measured
object generator match their recorded code hashes.

Authoritative artifacts:

- `baseline-reviewed.json`: 200 fresh baseline compiles.
- `paired-confirmed.json`: complete reviewed intake replay and its alternatives.
- `object-replay.json`: final generator across all 51 compiled nonmatches.
- `exact-confirmation.json`: two independent exact rebuilds.
- `final.json`, `summary.json`, `states-confirmed/*/final.c`: selected results.
- `inventory-before-reviewed.json`, `inventory-after.json`, `histogram.png`,
  `histogram.svg`: file-aware diagnostic sites, operation groups and object profiles.
- `attempt-log-check.json`, `code-verification.json`: receipt and implementation audits.
- `tests-focused.log`, `tests-rewrites.log`, `tests-full.log`,
  `tests-comparison.json`: verification output.

`paired-reviewed.json` is the superseded partial exploratory arm, stopped after
review found guard gaps. `object-pilot.json` and
`object-pre-spacing-review.json` are exploratory object receipts. Their attempts
remain logged but are not the authoritative final comparison. No production KB
inference, game integration or frozen campaign deployment was performed.
