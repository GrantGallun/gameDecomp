# Clean intake: member accesses, frontend casts, and IDO byte cursors

September 22, 2026. Rechecked the same 200 assembly-only starting drafts from the previous header-conflict round, then continued repairing their exact saved final candidates. Project headers remain available; this is not a header-free capability measurement.

| Check on the same 200 candidates | Before this round | After repairs |
|---|---:|---:|
| IDO compiles | 26 | **46** |
| Frontend passes | 18 | **43** |
| Both IDO and frontend pass | 17 | **42** |
| Object byte-exact | 2 | **4** |
| Frontend errors | 4,830 | **2,862** |

119 candidates have fewer frontend errors; none has more. No candidate loses an IDO pass, frontend pass, or exact match. 122 final sources change. The two additional exact candidates, `addRenderCallback` and `initMenuAsciiFontTexture`, were absent from the research KB's exact set at replay start. Both pass the frontend. They are saved and logged in isolated workspaces, not integrated into the game or added to production progress totals. `initMenuAsciiFontTexture` uses reconstructed game headers and must retain that assistance attribution.

## Why earlier progress looked further along

These populations answer different questions:

| Population | What its count means |
|---|---|
| Earlier 200 reference-seeded drafts | 53 IDO / 35 both / 3 exact. Those drafts carried reference-source information, so they are not the clean baseline. See `../intake-20260921/CONTAMINATION.md`. |
| Same 200 assembly-only drafts, before either September 22 repair | 16 IDO / 12 both / 2 exact. |
| Same clean frame after the header fix | 26 IDO / 17 both / 2 exact. This round starts here. |
| Same frame after this round | **46 IDO / 42 both / 4 exact.** |
| Current research KB | **582 distinct functions have compiled under IDO at least once**; a historical union across assisted and unassisted attempts, not 582 currently frontend-clean sources. |
| Current research exact tiers (`eval.status`) | **348 total: 256 SOLVED, 12 header-assisted, 25 reference-type-assisted, 55 recovered**, across 1,074 attempted functions. This experiment does not change those totals. |
| Separate paused broad campaign | Native checkpoint snapshot: 972 object-exact-or-integrated of a 2,051-function cohort, including 21 integrated. A different assisted campaign and database, not 972 clean intake solves. |

The fresh research counts and command output are in `status-after.json` and `status-after.txt`. The broad campaign snapshot came from `/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json` (commit 28382, paused_budget). It was not restarted or modified.

## Failure histogram

![Failure histogram](histogram.png)

Counts are functions carrying a diagnostic class; classes overlap. The full before/after counts are in `summary.json`. Member-reference failures fall from 133 to 77 states; specifically, accesses through `void *` fall from 120 to 49. Pointer-to-integer errors become visible after member expressions become type-checkable, so that class rises from 73 to 89 despite the reduction in each affected candidate's total errors. Class totals alone are not a progress metric.

## Repairs and wiring

The existing `solver.void_field_repair` was used by model-repair normalization but omitted from this intake route. `eval.intake_runners.void_members` now calls it with fresh, complete real clang diagnostics and target assembly. `frontend_diagnostics.analyse(full_diagnostics=True)` preserves source excerpts needed by the generator; normal callers retain the existing 16 kB raw-text limit. No excerpts are reconstructed in the runtime repair.

The existing `solver.frontend_fixits.propose` then inserts casts at diagnosed expression ranges. The caller still compiles, logs, and adjudicates the proposed child. These first two appended actions change 113 and 15 candidates respectively, bringing the frame to 36 IDO / 32 both / 3 exact.

That reveals eleven frontend-passing candidates rejected by IDO. Ten are fixed by the existing `solver.void_pointer_units.propose`: clang allows GNU void-pointer arithmetic that IDO does not, and IDO also rejects one comparison between differently typed pointers. `ido_byte_cursors` runs only after a failed IDO compile and a fresh passing frontend check. It refuses any proposal containing inferred pseudo-field widths. It adds ten IDO passes and one exact match. `__osDequeueThread` remains the one frontend-passing IDO failure.

All three actions are appended to `eval.intake_probe.SEQUENCE`, after the existing passes. Review also found that the sequential driver passed the original compile flag alongside the current candidate. It now passes the adopted verdict. A regression test runs the actual driver and proves that a preceding successful compile prevents cursor repair. The replay already passed the current verdict; the driver correction makes runtime wiring consistent with that measured behavior.

This changes main-tree intake machinery. It does not redeploy the paused frozen campaign or add actions to the separately trained policy catalog. No model calls, reference C bodies, KB inference mutations, or real game build-path edits were made.

## Verification and receipts

- `paired-final.json`: complete 200-state paired replay, source hashes, code hashes, per-stage decisions, compiler verdicts and fresh frontend reports. `states/*/before.c` and `states/*/final.c` are the exact measured sources.
- `ratchet.json`: no lost acceptance level and no higher frontend error count. `summarize.py` asserts the full denominator and every saved source hash before producing the report and chart.
- Final replay: **338 logged compiler attempts = 200 baselines + 113 member candidates + 15 cast candidates + 10 cursor candidates**. Across exploratory and final runs, all **902** compiler attempts have source text and valid hashes (`attempt-log-check.json`). Private databases and build trees are under `/home/grant/decomp/experiments/clean-members-20260922`.
- `paired.json` preserves the earlier two-action replay. `probe_void.py` was exploratory and reconstructed diagnostic excerpts; it is not the evidence for shipped behavior. The authoritative replay uses actual complete diagnostics. `cursor-probe.json` measures the ten-candidate follow-up before wiring.
- **68 focused tests pass in WSL**, including real positive generators, complete diagnostic retention, missing-evidence declines, inferred-field refusal, and current-verdict driver wiring. Windows-only expanded checks hit local clang/python executable availability problems; the corresponding WSL checks pass.
- Full project suite before the final one-line driver correction: **4,164 passed, 15 failed, 41 errors, 8 skipped, 1 xfailed, 2 xpassed**. The 56 failure identities and phases match the recorded pre-existing baseline exactly (`tests-comparison.json`). The final driver correction passes the focused WSL suite. The suite is not globally green.
- Independent review found the stale verdict and a missing real-proposer wrapper test; both are addressed. `code-verification.json` records the one-line driver difference from replay-time code. The three repair implementations are unchanged from the final measured run.

Remaining large classes are syntax (100 states), integer/pointer conversions (89), incompatible pointers (80), and member accesses (77). The remaining 49 void-member states need closer access-evidence analysis; the adapter deliberately leaves ambiguous widths unresolved.
