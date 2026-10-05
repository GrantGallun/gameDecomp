# New-function campaign intake panel, September 30, 2026

**The larger sample shows limited transfer and a substantial cost increase.** On 64 new functions across 45 translation units, the temporary byte-address connection supplies one additional valid campaign seed and one additional byte-exact candidate. It preserves the baseline's valid seeds and two exact candidates. Current intake makes **208 compiler attempts**; connected intake makes **295**, with three connected cases hitting the fixed per-function budget. The adapter remains experimental and production wiring is unchanged.

## Sample and frozen boundary

This panel excludes the original twelve adapter-development functions and their seven complete translation units. It also excludes all sealed heldout/test/validation names in the project's set definitions. These are new functions and TUs for this adapter's development sample, drawn from the project's exposed DEV pool. The binary-context policy and other campaign components have separate historical development; this is not a claim that the entire solver has never encountered these functions, and it is not cross-game testing.

The 64 functions comprise two independently reported groups: 32 sampled broadly and 32 selected for shifted address additions with memory accesses. Hash ranking, binary instruction counts and assembly operations determine selection. Each TU contributes at most two functions. The broad group has eight functions in each size range: 4–32, 33–96, 97–256 and 257–800 instructions. The address-pattern group has 9, 10, 10 and 3 respectively. The initially intended eight large address-pattern functions were unavailable under the new-TU restriction; that shortfall was filled from other binary-eligible size bins before any candidate generation or C compiler outcome. Forty functions contain calls and four contain floating-point operations.

`panel.py` writes the selection before assembling isolated binary targets. No function is replaced after a bad generation, compile or intake result. The new panel's `byte_address.py` hash equals the prior experiment's adapter hash, `8146e911c54489e4a4e7fbc031bbd63b0dc3269acf42da7d0ee969253e4a4897`. No adapter change or outcome-driven tuning was made. Once these outcomes are inspected, this panel is exposed development evidence for any subsequent work.

The temporary connection mechanism is copied from the tested intake probe. Changes are limited to sample/budget parameters, timing measurement and reporting incomplete intakes. It still retains ordinary candidates, appends distinct byte-address candidates, uses actual campaign intake, and invokes unchanged `workspace.score` with the real IDO recipe and strict frontend checks. Private native WSL workspaces and databases isolate all changes from the game build and production KB. Model calls, assisted draft/header adapters and reference C inputs are excluded. Project headers remain compiler-check inputs, not generation answers.

## Outcomes

| Group | Current valid seeds observed | Connected valid seeds observed | Current compiles | Connected compiles |
| --- | ---: | ---: | ---: | ---: |
| Broad, 32 scheduled functions | 15 | 16 | 92 | 132 |
| Address-pattern, 32 scheduled functions | 13 | 13 | 116 | 163 |
| All 64 scheduled functions | **28** | **29** | **208** | **295** |

All current intakes terminate normally. Connected intake terminates normally for 61 functions and exhausts its preregistered 16-compile limit on three. Neither arm observes a compiler-and-frontend-passing attempt for any of those three functions. The counts above are valid seeds observed within the fixed budget, not a claim of completed connected intake on all 64. There are 61 complete paired outcomes, with one validity gain and no validity loss. `alResampleParam` has no generated candidate and is parked in both arms; it remains in the scheduled denominator.

The new validity gain is **`drawScaledAssetTableSprite`**. Its current selected draft fails IDO with two unacceptable addition operands; the connected draft passes both compiler and strict frontend and is retained as the selected repair seed. It is in the broad group rather than the address-pattern enrichment.

The additional byte-exact candidate is **`initRacePlayerLandingSnowSpray`**, in the address-pattern group. Its ordinary C already passes the compiler/frontend, but the byte-address draft corrects an unrecovered global indexed-address view. Attempt 292, with ordinary parent 289, has an independent `object_sections_exact` certificate and passing frontend. Both arms also retain their identical exact candidates for `initRaceTypeSelectCornerSprites` and `getRaceCourseNextSurface`. These are isolated candidate outcomes, not new global SOLVED counts or whole-ROM integration claims.

The adapter records changes on 37 functions. In the complete pairs, 49 selected source hashes are identical; eleven selected sources differ, and one pair is parked without C. Changes therefore do not usually produce a new valid seed. The address-pattern proxy enriches opportunities but does not itself identify unresolved non-byte-sized pointees or late assignment-type conflicts.

## Cost, incompleteness and lineage

The extra **87 compiler attempts** represent roughly 42% more compilation work. Measured serial arm execution totals are about 112 seconds current versus 168 seconds connected; generation totals are about 58 versus 95 seconds. This includes observer, report and certificate overhead, uses one host and always runs current first. It is descriptive measurement, not a robust campaign throughput benchmark or proof that production would have the same timing.

The three budget-limited connected functions are `updateRaceHud`, `drawTimeTrialHud` and `updateCourseSelectExtraCourseIconList`. Each makes 16 logged attempts without a valid seed. Their partial attempts, diagnostics and generation observations remain retained. The budget was not increased or the panel resampled after inspecting these results.

`initRaceSceneFlow` exposes an additional reporting edge case: two syntax/stride passes can emit identical child C from different ordinary parents. The temporary wrapper labels the deduplicated source with the later alias while intake chooses the first report's actual parent. Both original parent sources were scored, and every recorded edge is real and backward-pointing. The audit retains two alternate derivation reports explicitly rather than asserting that all reports describe one parent. Eventual production wiring should retain a stable candidate label together with the chosen derivation and alternative origins. No attempt or generation report was rewritten to conceal this discrepancy.

## Verification and remaining validity failures

`verify.py` audits **all 503 logged native compiler attempts**, 110 compiled object certificates, frontend source bindings, selected/frontier source retrieval, ordinary candidate retention, real parent edges, selection/TU exclusions and frozen adapter identity. All snapshot hashes match their preregistration. There are zero evidence or inference rows in the private database. No observer errors or provenance caps are reported on byte-candidate receipt sidecars. The existing byte-address/provenance fixture suite passes all **14 tests** with the native installed m2c.

`diagnostics.py` describes the baseline's 35 selected invalid sources; one further function is parked without C. Nine have call argument type conflicts, nine have unresolved member-access diagnostics, seven have aggregate-conversion diagnostics, seven have address-arithmetic diagnostics, six have undeclared-function diagnostics and two have pointer-return conversion diagnostics. These are overlapping diagnostic families, not proof of original types or permission to silence errors with arbitrary casts. The byte-address adapter does not fix most of these blockers. The next validity investigation should address how provisional callee/return declarations and memory-access views agree with binary witnesses, preserving unknown layouts and retractable alternatives.

All artifacts are training-ineligible. The native comparison is retained at `/home/grant/decomp/experiments/m2c-campaign-transfer-20260930-v1`, with its sealed input panel at `m2c-campaign-transfer-panel-20260930-v2`. Local copies are under `portable/` and `panel/`; `portable.zip` is a compact backup. Scripts and this report remain alongside them. No production generator, live campaign, matching function or KB was changed.

## Decision

The original 12-function result overstated the likely breadth of the validity gain. This new panel demonstrates real transfer, but it does not justify unconditional redrafting of every intake. Keep the option experimental; test a narrower trigger and candidate budget on a separate panel before promotion. Compiler validity alone would miss the additional exact case, whose baseline already compiles, so such a trigger should use observed unresolved address/type operations rather than simply skipping every compiling function.

No model-driven repair continuation was run. This experiment measures seed validity, retention, bounded intake cost and isolated byte equality. It does not establish how many additional matches a full campaign would produce.
