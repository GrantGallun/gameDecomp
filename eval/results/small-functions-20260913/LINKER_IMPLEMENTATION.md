# Linker and declaration integration work

Production changes are limited to `eval/prepare_integration.py` and
`tests/test_prepare_integration.py`. The deployment should additionally include
the already implemented and tested main `solver/function_boundary.py` and its
tests: the frozen campaign still used the older jal-only/raw-relocation-equality
checker. The existing frozen workspace call site already supplies every required
ROM/symbol/extent argument. The object-section gate is unchanged.

## Safe declaration support

Integration preparation now preserves declaration-only `extern` objects and
function prototypes. Before touching the opaque destination TU, an actual Clang
AST probe under the project's frontend recipe checks the exact declarations with
their included headers. It requires one ordinary external VarDecl/FunctionDecl,
no initializer, body, inline definition, extra declaration, attribute or expanded
source range. Failed syntax/type compatibility declines preparation. Probe hashes,
resolved types and frontend recipe are recorded in manifest lineage.

The context-free `candidate_parts` readiness check remains conservative: externs
still require contextual admission. This preserves the existing intake preference
for recompiling proper header-based candidates. Declarations are retained before
the replacement function, rather than silently discarded or borrowed from a
reference body. Full-TU checking and the whole-ROM gate remain required.

## Actual fresh-source proof and useful failures

`linker-pilot.py` first showed that the existing main ROM-bound relocation support
admits `osCreateMesgQueue` and `fadeOutMultiplayerCourseSelectMenu`; frozen support
declines both. The third candidate, `drawRaceMotionAnimationDebugViewerMotionNumber`,
still declines because its local `.rodata` ownership is unresolved. No broad data
or BSS admission was added.

The seven-source fresh compile snapshot was checkpoint **13502**, containing all
five previously integrated functions plus these two. Fresh standalone function and
frontend checks passed, but real full-TU integration exposed two declaration errors:

1. `fadeOutMultiplayerCourseSelectMenu` declared `gFramebufferSwapHold` as signed
   byte; the full-TU diagnostic required unsigned byte. A separately logged
   alternative changes only that extern type.
2. `osCreateMesgQueue` declared `__osThreadTail` as a complete `OSThread`. The SDK
   header named by the compiler instead declares a two-field sentinel object.
   A separately logged alternative includes that SDK header and casts the sentinel
   address to the queue's `OSThread *` view. No reference function body was used.

Failed unchanged six/seven-source build logs remain intact. These failures are
evidence that function matching does not establish correct data ownership or
declaration compatibility. They were not converted into pass statuses.

Both corrected candidates passed fresh standalone frontend/function certificates
and the **combined seven-source build reproduced the original 8,388,608-byte ROM**:

`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`

The five prior integrated members were preserved:
`calculateFixedAngleBetweenXZPoints`, `osSpTaskStartGo`, `rmonPrintf`,
`updateRaceCameraMenuPreview`, and `updateRacePlayerPostUpdateAttack`.

Successful proof artifacts:

- `linker-union-1789318026368143892/sentinel-1789318340254484789/seven-integration.json`
- Adjacent `.build.log`, `.rebuilt.z64`, `prepared/manifest.json`, and `report.json`.
- Final preparer replay emits the identical verified manifest:
  `e70551a653c4e0f3159214d391aacc0a156ba33264daec77e59a3b1b0bf5be1a`.
  See `final-prepare-proof.log` and the adjacent final-prepare-check directory.

## Candidate lineage for ordinary adoption

All paths below are relative to `linker-union-1789318026368143892`.

| Function | Original selected attempt | Corrected private source | Source SHA256 |
|---|---:|---|---|
| `osCreateMesgQueue` | 90545 | `sentinel-1789318340254484789/sentinel-view.c` | `4905b7e24bac9542db59db06259cd1b4030d7f6a2ca5fe1d93551a02709fb1be` |
| `fadeOutMultiplayerCourseSelectMenu` | 71566 | `followup-1789318187711780903/unsigned-extern.c` | `c0d966c225f74582d67c7b8c4bbb6f15178a4be72fe67e097c0452a5fed3e68d` |

Each function has its own private attempt database: fresh selected compile is
attempt 1 and the compiler-diagnosed alternative is attempt 2. Their original
canonical source/verification bindings are in the base `report.json`. Adoption
must freshly verify against the paused/drained current campaign parent and use
normal acceptance/integration. These private results are not live promotions.

## Validation

`74 passed in 6.70s` across boundary, object-certificate, preparation and completion
campaign tests, including actual Clang positive/negative declaration controls and
the existing header-reconciliation regression. `linker-focused-tests.log` retains
the result. No live database or canonical game source was changed by this work;
only private source-binding databases were copied during follow-up preparation.
