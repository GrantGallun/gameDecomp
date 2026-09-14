# Hypothesis Graveyard

Tested hypotheses and their evidence. "Graveyard" means the claim is no longer floating untested; it may be confirmed, refuted, inconclusive, or superseded.

Update rules:
- Move tested hypotheses here whether they succeed or fail.
- Include the test, evidence, decision, and linked idea when available.
- Prefer superseding or correcting old entries over leaving contradictory claims unresolved.

Created: 2026-08-29

## Tested Hypotheses

### HYP-20260829-01: An asm-differ similarity score of 100 is sufficient evidence that an attempt is byte-exact.
- Status: Refuted
- Tested: 2026-08-29
- Test: Audit every attempt writer, match consumer, router, and near-miss selector; add explicit verifier receipts and adversarial score/verdict tests.
- Evidence: The oracle exposes exact separately; a 100-score relocation mismatch is possible. Before this change the attempts table discarded exact, matched/status inferred it from score, and six selectors excluded score-100 nonmatches. All 218 tests pass after fail-closed migration where historical rows remain NULL.
- Decision: Persist the verifier exact boolean on every new attempt; treat legacy rows as unknown; use score only for ranking and route score-100 nonmatches to relocation.
- Linked ideas: None

### HYP-20260829-02: An unchanged diff-repair run is evidence that the repair method failed.
- Status: Refuted
- Tested: 2026-08-29
- Test: Add activation and compilation receipts, then replay three stored near misses against a disposable KB copy.
- Evidence: The arm activated and compiled on 2/3 functions, improving 99.436 to 99.925 and 95.989 to 96.211. On bootThreadMain it generated zero constraints, so the repair was not applicable and no effectiveness comparison occurred. 221 tests pass.
- Decision: Report not_applicable separately from no_gain, regression, and build failure; invalidate a corpus experiment if the arm never activates.
- Linked ideas: IDEA-20260829-02, IDEA-20260829-03

### HYP-20260829-03: Choosing a typed-residual Pareto-top candidate instead of scalar-best improves deterministic repair closure.
- Status: Inconclusive
- Tested: 2026-08-29
- Test: After append-only reverification removed 34 historical exact functions, rescore stored candidates for 13 unresolved functions above 90 and repair the two functions where Pareto-top differs from scalar-best.
- Evidence: Pareto selected a different anchor on 2/13 functions. Neither arm produced an exact match. On getRacePlayerRankingProgress Pareto reached 96.019 versus scalar 95.330; on initRacePlayerLandingSnowSpray neither improved beyond its anchor. Applicable pass counts differed (3 versus 2 or 1), so this is signal but not clean causal proof.
- Decision: Keep Pareto selection in the evaluator only. Do not integrate into the production pipeline until it produces closures or wins under a budget-matched larger replay.
- Linked ideas: IDEA-20260829-03

### HYP-20260829-04: The current independently matched library contains whole-function siblings similar enough to help the unresolved >=90 percent near-miss set.
- Status: Refuted
- Tested: 2026-08-29
- Test: After reverifying 34 exact sources, rank assembly similarity for all 13 unresolved functions at or above 90 while restricting both eligibility and prompt source to those recovered exact candidates.
- Evidence: 0/13 targets had a sibling at similarity 0.75 or better. Four were in 0.45-0.75 and nine were below 0.45. Retrieval completed without errors. The audit also found the old path ranked names from our pool but copied C from the finished reference repo; that contamination path is now barred and the sibling-pool source digest is fingerprinted.
- Decision: Do not spend GPU budget on whole-function sibling prompting with the current 34-source pool. Re-run the prevalence gate as the pool grows; test basic-block or partial-structure retrieval separately.
- Linked ideas: IDEA-20260829-03, IDEA-20260829-04

### HYP-20260830-01: Perfect type knowledge raises the hard dev exact-match rate materially above a clean-KB baseline.
- Status: Refuted
- Tested: 2026-08-30
- Test: Frozen best-of-2 pipeline A/B on hard_v1 dev: clean disposable KB versus separately tainted oracle-types KB, with identical code, model, source hash, set hash, and flags; 19 typed treatment functions and 20 no-type controls.
- Evidence: Exact matches were 0/39 in both arms, failing the precommitted +2 exact gate. Mean best score fell 31.044 to 21.600 overall. Typed functions fell 26.518 to 11.161 (2 wins, 7 losses, 10 ties), versus 35.342 to 31.518 for controls; difference-in-differences was -11.534.
- Decision: Do not build a broad type-inference tier from this premise. Diagnose harmful type-context injection and prioritize structural program-shape methods; keep the oracle KB labeled as a tainted upper-bound instrument.
- Linked ideas: None

### HYP-20260831-01: Unconditional completed-callee context improves parent generation under the current local-model prompt interface.
- Status: Refuted
- Tested: 2026-08-31
- Test: Three bounded two-parent callsite-context pilots, plus earlier body and on-demand variants, using frozen exact callees and the byte oracle.
- Evidence: The three callsite pilots spent 33,614 recorded generation tokens across 36 draws, produced zero exact matches, and score effects reversed across replications; blanket body and combined context also showed no exact gain.
- Decision: Do not inject callee bodies or contracts unconditionally. Preserve the wavefront mechanism through deterministic callsite validation and targeted repair.
- Linked ideas: IDEA-20260831-01

### HYP-20260831-02: Exact-leaf callsite contracts can mechanically reject compiled parent candidates that contradict completed callees.
- Status: Confirmed
- Tested: 2026-08-31
- Test: Validate target, guided parent, and paired baseline assembly for func_8005804C against two exact getRelocatableHeapBlockBase callsites.
- Evidence: Target and guided candidate passed 2/2 argument checks. Baseline failed 2/2 by passing the linker address plus 0x3e instead of the signed 16-bit loaded handle.
- Decision: Use exact-leaf validation as a zero-token gate for every proposal arm and send only violations to a bounded repair step.
- Linked ideas: IDEA-20260831-01

### HYP-20260831-03: `think=false` is a usable token-efficiency lever for gpt-oss slice calls on the current local Ollama stack.
- Status: Refuted (interface capability)
- Tested: 2026-08-31
- Test: Warm the model, then issue the same exact fenced-format request with `think=low` and `think=false`; proceed to the six-function S1 cohort only if the false arm returns a direct formatted response.
- Evidence: `think=low` returned the required direct answer in 56 tokens and 0.602s. `think=false` consumed the full 512-token cap in 4.585s, left the response field empty, and was recovered only from `thinking`. The pre-registered capability kill fired, so the expensive cohort was correctly skipped.
- Decision: Keep slice thinking at low for this model. Treat a non-reasoning slice model as a separate experiment, not a rescue of S1.
- Linked ideas: None

### HYP-20260831-04: Explicit-seed generation caching can make reruns free without collapsing independent draws.
- Status: Confirmed (mechanical)
- Tested: 2026-08-31
- Test: Request one seeded generation, replay the identical request, then request the same prompt with a new seed; require identical replay text, a cache hit only for the repeated seed, and a distinct key plus real generation for the new seed.
- Evidence: Same-seed replay returned byte-identical text in 0.0063s with zero charged tokens. The adjacent new seed used a different cache key and made a real 55-token generation. Unit tests also reject cache use without an explicit seed and separate behavior-changing reasoning flags.
- Decision: Use the cache only for explicitly seeded experiment draws; report recorded versus charged tokens separately.
- Linked ideas: IDEA-20260829-03

### HYP-20260831-05: The current eligible wavefront contains best-parent candidates with exact-leaf mismatches that justify bounded repair.
- Status: Refuted on the current cohort
- Tested: 2026-08-31
- Test: Replay and compile the best non-recovered stored candidates for the two reserved and three eligible deferred parents; validate exact-leaf arguments and normalized return-consumer flow before any model call.
- Evidence: Four candidates replayed at 97.532%, 68.536%, 96.293%, and 89.644%; all passed every argument and return-flow check after canonicalizing equivalent MIPS spellings. The fifth parent had no replayable stored candidate. WF1 activated zero repairs, spent 0/2,400 reserved generation tokens, and promoted nothing.
- Decision: Make stored-candidate mismatch prevalence a frontier eligibility gate. Preserve verified leaf facts as validation memory, but do not inject or repair from them when the candidate already satisfies them.
- Linked ideas: IDEA-20260831-01, IDEA-20260829-03

### HYP-20260901-01: ABI-sensitive leaf selection makes the leaf-first flywheel compiler-active.
- Status: Refuted on this cohort; confirmed deterministic near-match gain
- Tested: 2026-09-01
- Test: Remove every frozen held-out name before bootstrapping, rank never-attempted small DEV leaves by narrow/floating ABI evidence and caller fanout, spend one seeded cached draw on the top eight, then replay phase-aware deterministic layout and allocation repairs and shadow-test inferred return contracts on stored callers.
- Evidence: The zero-token scan found 11 transfer-qualified ABI-sensitive leaves. Eight first-pass draws used 2,159 generated tokens in 34.249 seconds and produced 0 exact leaves. `randomNextObject` reached 95.625% in 87 tokens. A binary-derived `param0+0x518` opaque layout fixed both field offsets but left the scalar score tied; retaining that phase-complete candidate and enumerating the promoted temporary type raised it to 98.750% with zero new tokens. The remaining residual is exactly one mis-coloured web (two instructions). A fault-ranked search used 21 bounded compiles and did not close it. Its only caller, `updateRacePickupIdle`, has no stored compiling candidate, so upward codegen transfer could not be measured. The audit also found and removed ten frozen-held-out names from the historical DEV leaf file.
- Decision: Keep the ABI selector, seeded cache-only replay, struct-scoped layout repair, and phase-aware frontier behavior. Do not call this a solved-leaf or parent-transfer win: the preregistered 2/8 exact and one caller-delta targets both failed. The next causal surface is a viable parent baseline, not more leaf prompt enrichment.
- Linked ideas: IDEA-20260831-02, IDEA-20260829-03, IDEA-20260829-04

### HYP-20260901-02: A two-lane matchability harvest plus narrow-return transfer will add exact leaves and activate one parent edge.
- Status: Partially confirmed; transfer claim refuted
- Tested: 2026-09-01
- Test: Freeze 16 never-attempted straight-line DEV leaves at no more than 20 instructions, run m2c then at most one seeded cached draw per leaf, create a parent baseline for `updateRacePickupIdle`, and shadow-test `randomNextObject` as u8 versus u32. Follow with zero-new-token project-header, empty-return, dependency-closure, and binary-annotation replays without changing the frozen cohort.
- Evidence: The preregistered >=3 exact target was met with 13/16 exact, but every exact was an empty-return m2c result and the LLM added none. The original leaf arm charged 159 generated tokens; the parent draw charged 1,600, truncated, and did not compile. Post-hoc deterministic preflight recovered all three failures, including two non-empty scheduler pointer getters, taking the cohort to 16/16 with zero new model calls. Header dependency closure reduced the parent failure to one opaque private struct and missing globals; target-derived offset/width annotations then made the unchanged m2c parent compile at 91.064%. Both `randomNextObject` callsites immediately mask v0 with 0xF, the included project header already declares u8, the u8 arm produced no assembly delta, and u32 failed as an incompatible redeclaration.
- Decision: Keep two lanes, project-header/dependency preflight, assembly-certified empty definitions, and provenance-separated binary annotations. Do not count trivial exacts as model yield, and do not rank ABI transfer from narrow-return evidence alone. Require a compiler-active consumer and route the 91.064% parent to reshape/allocation work rather than prototype repair.
- Linked ideas: IDEA-20260831-02, IDEA-20260901-01, IDEA-20260901-02

### HYP-20260901-03: A frozen-parent equal-call A/B can mechanically compare structured child-following repair with independent full-source revisions while preserving lineage and cost receipts.
- Status: Confirmed
- Tested: 2026-09-01
- Test: Three DEV parents, one compile failure plus one 80-95 and one 95-plus parent; four paired calls per arm with explicit seeds and compiler verification.
- Evidence: All 24 calls completed with no exclusions and durable proposal/attempt receipts. Both arms had zero exacts, conversions, or score movement, so efficacy remains untested. Structured repair used 4,972 charged tokens versus 12,523 but yielded 5 evaluated children versus 8; seven schema-invalid responses were genuine model noncompliance. The intended 6/6/6 full panel is unavailable because only two unique near-miss parents remain after exclusions.
- Decision: Keep the harness and comment-only edit rejection. Treat the smoke as mechanical-only. Preregister a prevalence-compatible backfill policy and obtain approval for the roughly two-hour full run before testing efficacy.
- Linked ideas: None

### HYP-20260901-04: A displayed 94-95 percent solver score means approximately 94-95 percent of instruction bytes are already correct and identifies a genuinely local near-miss.
- Status: Refuted
- Tested: 2026-09-01
- Test: Revalidated the two compiled MR1 smoke parents, classified their full stored residuals, ran the weighted scorer in debug mode, extracted both .text sections, and compared raw bytes. Rebuilt the known decompiled ROM as a positive control.
- Evidence: The 94.634 parent had 81 of 528 positional text bytes different, 75 changed diff-side lines, 24 layout faults, and 703 weighted penalties. The 95.346 parent had a 32-byte length deficit and 917-byte positional distance against 1,280 target bytes, with 98 changed diff-side lines, 44 structural faults, and eight missing instructions. The current decompiled reference rebuilt to the expected ROM SHA-1 exactly.
- Decision: Call this a weighted progress score, never percent byte correctness. Keep exact=false prominent and gate local repair on equal instruction length plus low structural and byte residuals, not on score alone. Do not spend the planned long MR1 run on the scalar-selected panel.
- Linked ideas: None

### HYP-20260901-05: A minimal provider-neutral residual-driven repair kernel can run one bounded source experiment, preserve append-only parent/proposal/child lineage, retain a better parent after regression, and use exact=false as the terminal authority.
- Status: Confirmed
- Tested: 2026-09-01
- Test: Implemented exactness-first residual packets, provider adapters, a global call-capped beam runner, fresh-root reverification, and a metadata-only clean-set guard; ran the full suite and one capped GPT-OSS DEV smoke on updateRacePlayerMode53AerialTrick.
- Evidence: 661 tests passed with 7 skipped. Live run agentrepair-1788322510427548697-updateRacePlayerMode53AerialTrick created root attempt 22327, proposal 25, and child 22328 with explicit edges. The child compiled but regressed 94.634 to 94.328, so the root was retained; exact remained false and the receipt reported 81/528 positional byte differences. After excluding both logged attempts and historical result artifacts, sbk1_v4_clean audited clean with 13 untouched held-out functions.
- Decision: Use this kernel as the experimental repair path. Do not claim efficacy from the mechanical smoke; next compare strong and local providers on a frozen DEV panel selected by true residual size, then extract only repairs that transfer unseen.
- Linked ideas: IDEA-20260901-04, IDEA-20260901-03

### HYP-20260902-01: Allowing GPT-OSS bounded inspection tools before patches improves repair over proposal-only search on genuine local residuals.
- Status: Inconclusive
- Tested: 2026-09-02
- Test: Frozen DEV parent randomNextObject at attempt 19565; six calls per arm with identical seeds and caps. Compared proposal-only search with the tool controller after mechanical adapter activation fixes, using the v4 receipt.
- Evidence: Fresh root: exact=false, score 98.75, equal text length, positional byte distance 3, and two register-allocation faults. Tool arm executed three inspections and one compiling patch; the child regressed to score 95.625, byte distance 6, and five register-allocation faults, so the root was retained. Proposal-only made five scored child attempts and also retained the root. Both exact=false; tool charged 127 generation tokens versus 3906. Parent-child lineage was explicit and the held-out audit remained clean.
- Decision: The bounded tool interface is mechanically viable but this one parent provides no efficacy signal. Freeze the adapter and run a preregistered panel of 5-8 genuinely local DEV residuals before adding more prompt or tool features or claiming that tools help.
- Linked ideas: IDEA-20260902-01, IDEA-20260901-04

### HYP-20260902-02: Removing investigation restrictions and offering broad project access causes GPT-OSS to conduct a richer autonomous search before giving up on a genuine local residual.
- Status: Refuted
- Tested: 2026-09-02
- Test: On frozen DEV parent randomNextObject attempt 19565, offered ten open-book calls with broad project search/read, sibling C, prior artifacts, full residual/history access, complete-source rewrite, no inspection-order requirement, no duplicate-action rejection, and only the target reference C definition redacted.
- Evidence: After correcting an inherited bounded-prompt activation defect, GPT-OSS used two calls: one immediate patch compiled but regressed score 98.75 to 95.625, positional byte distance 3 to 6, and register faults 2 to 5; it then finished and asserted the allocation mismatch could not be changed from source. It executed zero search/read actions. Exact remained false and the root was retained. Proposal-only also retained the root. Lineage linked proposal 84 to child attempt 22358; the clean audit retained 13 untouched held-out functions.
- Decision: Tool availability is not a search policy. On this model/prompt/parent, freedom alone did not elicit investigation. Test a materially different fixed investigation or curiosity policy before evaluating whether retrieved evidence improves repairs; do not add more tools as the next move.
- Linked ideas: IDEA-20260902-01, IDEA-20260901-04

### HYP-20260902-03: A minimum evidence-and-experiment stopping policy causes open-book GPT-OSS to conduct richer repair search than free early stopping.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Frozen eight-function DEV panel, equal six-call caps and per-function seeds. Free open-book could stop at will; curiosity required at least four calls, two compilation experiments, and one evidence tool before finish would be accepted.
- Evidence: After correcting schema activation defects, free mode used 16 calls, 4 tool actions, and 1 compiled source experiment. Curiosity used 47 calls, 19 tool actions, and 5 source experiments. Both produced 0 exacts. Curiosity reduced Fwobble from 5 to 3 differing bytes; no other best residual moved. The run completed in 1658.9 seconds and held-out audit remained clean.
- Decision: The stopping policy changes behavior and activation, but richer activity is not yet byte-exact efficacy. Keep activation and quality claims separate. Index or cache broad search before replication, and test a policy that requires materially distinct source experiments rather than only rejecting early finish.
- Linked ideas: IDEA-20260902-01, IDEA-20260829-04

### HYP-20260902-04: Mechanically retrieved compiler design principles improve byte-exact repair over the same curiosity-controlled open-book agent.
- Status: Inconclusive
- Tested: 2026-09-02
- Test: On four of eight frozen DEV parents where catalog retrieval activated, compared curiosity with identical curiosity plus confirmed or explicitly labelled experimental principle guidance, using equal roots, six-call caps, seeds, tools, and stopping policy.
- Evidence: Curiosity on the four applicable parents used 23 calls, 7 tool actions, 3 compiled source experiments, and 1634 charged tokens; principled used 24 calls, 8 tools, 4 experiments, and 1283 tokens. Both had 0 exacts and 0 byte-distance improvements. Principle guidance activated but did not change the best candidate on any function.
- Decision: Do not promote the isolated-register-web hypothesis or claim principle efficacy. Prompt retrieval alone has no quality signal at n=4. Close a discovery case with a mechanically enumerated source-shape family, freeze that recipe, and require unseen transfer before promotion.
- Linked ideas: IDEA-20260829-04, IDEA-20260902-01

### HYP-20260902-05: A bounded semantics-preserving source-shape family closes or improves isolated register-allocation residuals on the frozen applicable panel.
- Status: Refuted
- Tested: 2026-09-02
- Test: Compile declaration-order, initializer, register-qualifier, alias-lifetime, increment, fused-lookup, and global-pointer variants on all three activated DEV roots.
- Evidence: The corrected v2 replay compiled 49/49 variants across roots at 3, 4, and 19 differing bytes; exact closures were 0/3 and byte-distance improvements were 0/3. All 52 root/child attempts have lineage and held-out overlap was empty.
- Decision: Do not promote this rewrite family. Preserve the parent register-web mechanism, but require a materially different source/dataflow graph search before retesting.
- Linked ideas: None

### HYP-20260902-06: A register-independent stage assessor plus binary call-connected selection can mechanically separate existing DEV candidates into logic-reconstruction and exact-polish regimes without target source or model generation.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Freeze eight call-connected non-held-out compiled roots from binary edges, recompile them, and classify calls, non-stack memory effects, control structure, opcode sequence, and exactness.
- Evidence: All eight roots compiled with clean lineage and zero held-out overlap. The assessor separated five compiling-only, two logic-shape, and one structural candidate. It exposed a 94.634 weighted-score function with only 0.263 memory-effect agreement while routing the seven-byte reset helper to structural polish.
- Decision: Use the staged logic-first lane for module reconstruction and retain byte exactness as the sole commit gate. Next run a fixed module-context logic prompt against a function-local exactness control.
- Linked ideas: None

### HYP-20260902-07: The finished SBK1 decomp can be converted into model-ready same-game teacher packets while mechanically excluding the selected target, normalized duplicates, and optionally its entire translation unit.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Build LOFO and LOTO packets for three frozen logic-first race-player targets using exact sibling functions, assembly-ranked retrieval, and nested matched-source blocks; validate every packet with content digests and contamination guards.
- Evidence: Six packets were built with 29 exact function examples and 36 matched-source blocks. All target-name, target-definition, normalized-duplicate, TU-policy, ownership, digest, and tamper-evident load checks passed.
- Decision: Use binary-only, LOFO, and LOTO as separately labelled equal-budget context arms. Do not report either teacher regime as cold-start or cross-game autonomous decompilation.
- Linked ideas: None

### HYP-20260902-08: Governed finished-decomp context improves logic-first GPT-OSS reconstruction over binary-only context on the frozen race-player pilot.
- Status: Inconclusive
- Tested: 2026-09-02
- Test: Three frozen DEV targets, binary-only versus LOFO versus LOTO, two sequential GPT-OSS 20B calls per arm, identical per-function seeds and 1,800-token caps, rotating arm order, body-only splicing into identical compiling roots, compiler verification, and `logic.quality_key` selection.
- Evidence: Binary-only improved 0/3 functions. LOFO improved 1/3 and reduced mode 53 byte distance from 81 to 65. LOTO improved 1/3 by raising mode 16 memory-effect agreement from 0.704 to 0.731, although byte distance worsened from 209 to 221. No candidate advanced stage or matched exactly; mode 40 had no compiling child. The clean audit found 17 attempts/17 edges, 18 proposals, matching receipt IDs, and zero held-out overlap.
- Decision: Post-hoc direct-source audit narrows this to assembly-shape signal, not semantic improvement. Mode 16's retained LOTO candidate writes incorrect values through mislabeled offsets; mode 53 retains wrong layout, field, and call-argument semantics. Keep LOFO/LOTO experimental, but require a stronger semantic oracle and scaffold adapter before interpreting another rank win.
- Linked ideas: IDEA-20260902-03, IDEA-20260902-02

### HYP-20260902-09: One exact compiler-or-diff feedback call turns first-pass GPT-OSS reference reconstructions into better logic candidates.
- Status: Refuted
- Tested: 2026-09-02
- Test: Follow every first-pass child once with the same arm context plus its exact compiler error or focused instruction diff, while retaining the frozen root and first child under the logic-first rank.
- Evidence: Every retained teacher improvement came from round one. Across nine arm/target trajectories, round two produced no new best: it was duplicate, invalid/noncompiling, or a regression. In particular, mode 16 LOFO repeated its lvalue failures and mode 16 LOTO regressed after its first-round logic gain.
- Decision: Do not spend more calls on this unconstrained single-step feedback adapter. First add deterministic header/type closure and translate finished-source identifiers/fields into the candidate workspace's available scaffold; then retest feedback on compiler-active children.
- Linked ideas: IDEA-20260902-03, IDEA-20260901-04, IDEA-20260901-02

### HYP-20260902-10: An improvement in the current `logic.quality_key` is evidence that a retained candidate is semantically closer to the finished decompilation.
- Status: Refuted
- Tested: 2026-09-02
- Test: Compare the two v5 retained rank winners directly with the finished target C, including calls, call arguments, field identities/offsets, written values, predicates, and state transitions.
- Evidence: Mode 16 LOTO raised address/width memory-effect agreement but still treats position offsets as velocity, doubles position fields, overwrites `unk74` with updated position Y, and tests a state-flag bit instead of `soundDisabled`; byte distance worsened 209 to 221. Mode 53 LOFO reduced byte distance 81 to 65 and corrected the conceptual clamp operand, but its frozen struct layout still places fields at wrong offsets, passes `stateTimer` instead of `unk254`, uses the wrong vertical-acceleration and sine-input fields, and substitutes `unknown1.x` for both `soundDisabled` and `playerIndex`. Mode 40 remains an empty body. All candidates remained `semantic_status=not_tested` and no stage advanced.
- Decision: Rename rank movement as assembly-shape movement. Do not promote or count it as semantic progress until call arguments, address-value dataflow, predicates, and written values agree or differential execution passes. Body-only splicing cannot freeze an unverified scaffold.
- Linked ideas: IDEA-20260902-02, IDEA-20260902-03

### HYP-20260902-11: A dependency-free differential MIPS function runner can reject the audited mode-16 semantic false positive while accepting an equivalent target control.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Execute the normalized target object against the independently formatted raw target assembly and against the frozen v5 leave-one-TU-out candidate over three deterministic boundary-shaped register/memory states. Compare ordered hooked calls and normalized arguments, call-time persistent memory, final non-stack memory, declared returns, and callee-saved ABI.
- Evidence: The raw-target control passed 3/3 cases. Candidate source SHA-256 `d449a4966a2d961e9a00209f64ccf9479dd182b0d91dd8ee43f26f1b19b6dd16` failed 3/3 with no unsupported instructions or ABI violations. Every first divergence was `clampRacePlayerVectorXZSpeed(player+0x40, player)` in the target versus `(player+0x1c, player)` in the candidate; final persistent memory also differed in every case. The candidate averaged 75 dynamic instructions against the target's 78, but instruction count was not a pass gate. Receipt: `eval/results/differential-mode16-pilot-v1.json`.
- Decision: Use differential execution as a separate semantic evidence channel and model-repair observation. Do not yet use it as an automatic promotion gate: the confirmed scope is one integer-only function under side-effect-free opaque call hooks and three synthetic states. Expand exact-callee side effects, instruction support, coverage-guided states, and the control panel first.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-02

### HYP-20260902-12: Feeding the differential debugger's first divergence to GPT-OSS produces measurable semantic repair progress on the audited mode-16 candidate within four bounded rounds.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Starting from attempt 22551, run four sequential `gpt-oss:20b` JSON-edit proposals with target MIPS, current C, and three differential traces. Compile every proposal, rerun all semantic cases, and accept only a strictly better behavior key before using weighted object score as a tie-break.
- Evidence: Round 1 changed the clamp vector from `player+0x1c` to `player` and was rejected. Round 2 named `player+0x40` correctly but failed compilation through illegal `void *` arithmetic. Round 3 emitted the valid `(s32 *)((char *)player + 0x40)` argument, compiled, raised the summed matching call-argument prefix from 10 to 16, and moved weighted progress 90.558 to 90.606. Round 4 misdiagnosed the remaining store dataflow, compiled at 88.635, and was rejected. The best still passed 0/3 complete cases, left the write prefix and 31-byte aggregate final-memory difference unchanged, and was not exact. Four calls charged 3,085 tokens. Receipt and database audit show five attempts, four proposals, four linked children, and five lineage edges.
- Decision: Differential feedback is a usable steering signal but concrete address/value examples are insufficient for full repair. Add register/value provenance to divergent writes and let semantically promising noncompiling children enter a compiler-fix branch; do not claim a semantic solution or generalization yet.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-01, IDEA-20260902-02

### HYP-20260902-13: Adding the complete external-call traceback improves the final verified semantic outcome over first-divergence-only feedback within four GPT-OSS repair rounds.
- Status: Refuted for this one-function replay
- Tested: 2026-09-02
- Test: Replay the frozen mode-16 repair from the same source with the same three semantic cases, four seeds, model, sampling settings, compiler, and selection key. Change the feedback adapter to include all external calls, marking equal call/state with `=`, equal call with divergent call-time memory with `~`, and differing callees or arguments with `!`.
- Evidence: The trace run reached the valid `player+0x40` clamp repair in round 2 rather than round 3 and exposed the later callback user-ID mismatch. It nevertheless ended at the same behavior key `[0, 3, 16, 3, -31, -9, 90.606]`, passed 0/3 complete cases, and was not byte exact. Its additional player-index repair used offset `0x14`, while the target argument is loaded from `player+0x0`. The run charged 3,964 tokens versus 3,085 (+879, +28.5%). Interpretation is qualified by two harness failures: round 1 held the correct clamp edit but the parser rejected its over-400-character hypothesis, and round 3 exhausted the 1,400-token output cap without emitting JSON. Receipt: `eval/results/differential-repair-mode16-gptoss-trace-v2.json`.
- Decision: Keep call traceback as diagnostic context, but do not claim a semantic-performance gain. Before a broader A/B, salvage valid edits independently of verbose hypothesis prose and provide a compact slice with value provenance rather than an unbudgeted full trace.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-01, IDEA-20260902-02

### HYP-20260902-14: Dynamic value provenance, causal slices, and a monotonic semantic-prefix gate let GPT-OSS recover the audited mode-16 behavior without target C.
- Status: Confirmed for one development function under five finite cases
- Tested: 2026-09-02
- Test: Starting from the frozen 0/3 mode-16 candidate, give `gpt-oss:20b` concrete executed register/memory provenance and call traces, separate long diagnosis from bounded patch emission, compile every child, and accept only semantic-prefix-preserving improvements. Add two discriminator cases for the final callback predicate and rerun from frozen receipts.
- Evidence: The trajectory repaired the clamp pointer, vertical-velocity source, three position updates, redundant writes, callback user ID, and callback predicate. A fixture audit caught overlapping writes at `0x7c`/`0x7e`; after correction the apparent 5/5 result became 3/5, and GPT-OSS repaired the real predicate to reach 5/5. At the final semantic checkpoint all 27 calls/arguments, call-time memory checkpoints, final persistent memory, and dynamic instruction counts agree. Score moved from 90.558 to 95.865. Receipts: `differential-repair-mode16-gptoss-causal-v3.json` through `v13-focused.json`.
- Decision: Keep the causal two-phase controller and semantic-prefix gate. Do not generalize beyond this function or treat finite tests as proof; next require a frozen multi-function replay, side-effectful callees, and coverage-guided inputs.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-05, IDEA-20260902-02

### HYP-20260902-15: Once logic passes, unrestricted long-context GPT-OSS reasoning is sufficient to close the remaining byte-exact compiler residual.
- Status: Refuted for the final mode-16 load-scheduling residual
- Tested: 2026-09-02
- Test: Transition the verified 5/5 candidate into exactness mode; expose full assembly or residual-only slices, 8,000/4,000/3,500-token diagnosis budgets, generic signedness and moved-load principles, source-offset-to-macro correlation, and bounded patch retries. Preserve all semantic checkpoints and accept only exact-score progress.
- Evidence: GPT-OSS independently repaired target `lb` versus candidate `lbu` and target `lh` versus candidate `lhu`, improving 95.865 to 97.788 and then 99.712 while retaining 5/5. It then stalled across full-context, residual-only, adaptive-budget, and source-correlated runs. The remaining 416-byte function differs in six byte positions across three instruction words: `lw v0,0x44(s0)` is scheduled two loads too late. Post-hoc finished-source audit identifies the missing `s32 yVel` local value web; GPT-OSS did not synthesize it. Receipts: `v14-exact.json`, `v15-principles.json`, `v16-exact-sliced.json`, `v17-exact-adaptive.json`, and `v18-source-correlated.json`.
- Decision: Reasoning time is not a substitute for source-shape candidate generation. Retain phase-specific slicing and exact residual principles, but extract the local-value-web hypothesis into a mechanically enumerated, oracle-verified family and test transfer before making it permanent.
- Linked ideas: IDEA-20260902-05, IDEA-20260901-03, IDEA-20260829-04

### HYP-20260902-16: Deterministic boundary-guided path exploration can close target and candidate instruction/conditional-edge coverage for the mode-16 development function without target C.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Enumerate static target branches, mutate executed memory inputs/registers and seeds, retain only coverage-increasing cases, freeze the discovered cases, then replay the 99.712 candidate.
- Evidence: The original five cases required 487 explored states to discover two retained discriminators. The frozen seven-case panel covers 104/104 reachable instructions and 18/18 conditional edges on both target and candidate; candidate semantic comparison passed 7/7. Receipt eval/results/differential-coverage-mode16-v3-frozen-panel.json; full Windows suite 727 passed, 9 skipped (including the WSL-backed coverage assertion).
- Decision: Require per-function target and candidate coverage receipts before describing semantic validation as comprehensive. Scope this confirmation to one integer-only function under opaque deterministic callees; branch coverage is not exhaustive value-state equivalence.
- Linked ideas: None

### HYP-20260902-17: A callgraph-leaf-first differential census can classify the first current blocker across a frozen connected multi-function DEV panel without exposing target C.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Recompiled the frozen roots for eight connected functions, ordered them leaves-to-callers from binary call edges, explored target and candidate paths with deterministic boundary mutations, compared semantic traces, and recorded each first stopping stage in an auditable receipt.
- Evidence: All 8 roots compiled and none was byte exact. Three leaves reached complete target and candidate instruction/conditional-edge coverage and passed 5/5, 6/6, and 7/7 differential cases; updateRacePlayerLeanAngle reached a concrete wrong-write semantic divergence; mode 16 reached a concrete provisional call-argument divergence; modes 37, 40, and 53 stopped honestly at target coverage. Receipt eval/results/dag-pipeline-census-v2.json audits clean with 8 attempts, 8 parents, 8 lineage edges, and no held-out overlap.
- Decision: Use this DAG census as the routing front end: send passing leaves to byte polish, concrete failures to semantic repair, and coverage gaps to harness/input work. Do not promote parent semantic passes until real callee side effects or recursive exact-callee execution are available.
- Linked ideas: None

### HYP-20260902-18: The mode-16 causal differential-repair controller can be generalized into a callgraph-ordered worker that retains verified semantic-prefix progress on another function without manual per-round intervention.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Parameterize the repair controller by function, frozen cases, call arities, and return registers; dispatch all eight nodes from the audited DAG census; give each node up to four 8,000-token diagnosis rounds and 2,000-token patch emissions; accept only prefix-preserving behavior-key improvements.
- Evidence: The 38.1-minute wave processed 8/8 nodes with zero orchestration errors and charged 185,832 local generation tokens. Mode 40 accepted three checkpoints, extending matching call/checkpoint prefixes 0 to 4 to 8 to 12 and score 0.0 to 6.359 to 13.495 to 15.291. Mode 16 independently accepted two checkpoints, extending its call prefix 24 to 38 and write prefix 9 to 16 while score moved 89.644 to 89.788. No function became newly semantic-complete or byte exact; six nodes retained their roots. Of 26 rounds, 16 emitted invalid patches, nine compiled, and five were accepted. Receipt eval/results/differential-wavefront-v2-long.json audits clean: 18 attempts, 18 lineage edges, no held-out overlap.
- Decision: Keep the reusable DAG dispatcher and monotonic semantic gate. Do not equate prefix gains with solved functions. Next replace fragile exact-text JSON patches with structured source-span/layout actions, resume from accepted children, and close coverage/callee-model debt.
- Linked ideas: None

### HYP-20260902-19: A tolerant, controller-resolved source-span interface can remove most structured edit-transport failures on the hard differential-wavefront functions, exposing whether the remaining bottleneck is patch content.
- Status: Confirmed
- Tested: 2026-09-02
- Test: Resume all eight prior best attempts with numbered one-span edits, audit emitted schema variants, harden the normalizer, then rerun Modes 16, 53, and 37 for two rounds each from the retained v3 roots.
- Evidence: V3 completed 8/8 with clean lineage but accepted 0 improvements; 15/24 final rounds were invalid. Offline replay of 55 saved patch attempts raised applicable edits from 9 to 15. After accepting bounded nonoverlapping spans, numeric strings, aliases, arrays, and line lists, v4 reduced invalid final rounds on the hard three from 9/9 to 1/6. Mode 16 produced 2/2 compiling candidates, but no function improved or became semantically complete/exact; Modes 53 and 37 produced noncompiling function rewrites.
- Decision: Keep controller-resolved locations and tolerant normalization. The next bottlenecks are typed layout/call construction, compiler recovery, memory-delta slicing, deterministic compiler-shape search, and real upward callee execution; do not spend more reasoning budget on the same generic patch prompt.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-05

### HYP-20260903-20: Forced checkpoint resynchronization can expose later independent semantic mismatches while leaving ordinary unintervened execution as the only pass authority.
- Status: Confirmed
- Tested: 2026-09-03
- Test: Add explanation-only call and aligned-write interventions to the MIPS differential runner; after each first divergence, restart from the original input with accumulated target checkpoint replacements and collect up to three observations. Validate on synthetic two-call and two-write faults, then preflight the five retained multi-function failures.
- Evidence: Both synthetic candidates remained failed under ordinary comparison while their resynchronized reports exposed two independent faults and reached counterfactual alignment. The five-function zero-token preflight exposed 21 Lean, 7 Mode 40, 12 Mode 16, 39 Mode 53, and 39 Mode 37 observations. Five of seven Mode 16 cases reached the end after checkpoint replacement; unsupported missing/extra calls and writes remained explicit. Call-entry memory evidence now names differing bytes and their last writers. Twenty focused differential tests pass.
- Decision: Keep resynchronization as diagnostic infrastructure. Never use an intervened run for acceptance, semantic-pass counts, or promotion. Continue to stop explicitly at missing/extra control-flow events until a sound control-flow alignment mechanism exists.
- Linked ideas: IDEA-20260902-04, IDEA-20260902-05

### HYP-20260903-21: Giving GPT-OSS all independently resynchronized mismatch clusters at once is sufficient to close the five retained semantic failures.
- Status: Refuted for the current source-blind, boundary-resynchronized packet
- Tested: 2026-09-03
- Test: Starting from the retained v3 roots, run Lean and Modes 40, 16, 53, and 37 for three rounds each with clustered cross-case evidence containing up to three forced-resynchronization stages, 8,000-token high-thinking diagnosis, 2,000-token bounded patch emission, compile repair, monotonic prefix checks, and ordinary unintervened differential verification.
- Evidence: The clean 1,557-second wave charged 131,526 local tokens over 15 rounds, with seven compiling candidates, four invalid patch rounds, two accepted partial changes, 0/5 newly semantic-passing functions, and zero exacts. Lean reduced aggregate differing persistent bytes 35 to 23 but stayed 0/7. Mode 16 changed only dynamic instruction-count distance 30 to 20, left all observable-prefix metrics and 12 diagnostic observations unchanged, stayed 0/7, and fell 89.788% to 87.933%. Modes 40, 53, and 37 retained their roots. Modes 53 and 37 turned precise wrong-call-argument clusters into unsupported whole-struct rewrites; Mode 53 regressed a prefix or failed compilation and every Mode 37 patch failed compilation. Audit: 19 attempts, 19 lineage edges, correct function ownership, and no held-out overlap. Receipt: `eval/results/differential-wavefront-v5-resynchronized-failures.json`.
- Decision: Do not reject multi-observable differential reasoning. The replay did not supply the crucial mapping from a candidate instruction/load to the exact C expression and its compiler-realized layout, and boundary intervention did not prove that later observations were causally independent. Replace string provenance with source-linked dataflow graphs, canonicalize malformed scaffolds before repair, and cluster sinks by common source ancestors. Do not spend more reasoning tokens on the same source-blind generic-layout packet.
- Linked ideas: IDEA-20260902-03, IDEA-20260902-04, IDEA-20260902-05

### HYP-20260903-22: Canonicalizing an internally false access scaffold before semantic repair can turn Mode53's many differential faults into a local exactness residual.
- Status: Confirmed for one development function; transfer untested
- Tested: 2026-09-03
- Test: Keep the recovered Mode53 control flow, replace its invented partial `RacePlayer` struct with explicit byte-offset lvalues grounded in target memory operations and project-header field facts, correct the trace-proven lean/callback fields, compile with the identical IDO oracle, then classify and search the remaining residual without reading the finished target body.
- Evidence: The frozen root scored 94.634%. Its local struct compiled `updateState` at `0x78` rather than target `0x302`, velocity Y at `0x30` rather than `0x44`, and similarly displaced nearly every later field. The verified-offset candidate scored 99.695% with 40 sequence differences, all confined to six instructions that were identical in opcode, constant, address, and order under a consistent temporary-register rename. Swapping the two semantically independent, disjoint updates at `0x7c` and `0x304` produced verifier-confirmed 100.000%, zero differences, exact=yes. The source hash is `6c9199ee63fe084b0c9ce869cc7c19fa66ac6f00deabc6fa575c035c5aaca2a1`; the exact source is archived by the target workspace and recorded in SQLite with strategy `source-linked-layout-and-order-experiment`. An end-to-end controller replay from attempt 22725 (99.695%) compiled three guarded order variants, selected attempt 22728 at 100.000%, terminated before model generation, and charged zero LLM tokens; receipt `eval/results/mode53-deterministic-pipeline-replay.json`.
- Decision: Run a source-layout audit before spending repair tokens. When a partial struct's claimed and compiler-realized offsets disagree, treat the whole scaffold as one causal fault and rebind accesses before interpreting downstream divergences separately. When the remaining residual is register-renaming-only, enumerate proven-disjoint adjacent statement order before calling an LLM. Do not claim cross-function generalization until the frozen recipe improves unseen functions.
- Linked ideas: IDEA-20260903-01, IDEA-20260902-04, IDEA-20260829-04

### HYP-20260903-23: Adding a partial-struct layout audit to the existing generic source-span repair loop is sufficient to continue closing the unresolved leaf wavefront.
- Status: Refuted for the current four-function continuation
- Tested: 2026-09-03
- Test: Resume Lean, Mode40, Mode16, and Mode37 from their v5 retained attempts for three rounds each with prompt v14, forced-resynchronized differential evidence, the MIPS-o32 partial-struct audit, compiler repair, semantic-prefix selection, and deterministic exactness search. Exclude Mode53 because the byte oracle had already closed it.
- Evidence: The 2,681-second v6 wave charged 100,353 local tokens over 12 rounds. It produced 6 compiling candidate rounds, 5 invalid patch rounds, 2 accepted narrow improvements, 0 semantic closures, and 0 exacts. Lean moved 51.719% to 51.797% but remained 0/7: GPT-OSS repeatedly chose nominal `unk2F6`, which the broken struct emits at `0x2F0`, instead of rebinding the layout; later writes remained wrong. Mode37's audit exposed 11 contradictions and the model named field-ordering as the cause, but its whole-struct/field repairs were noncompiling or regressed the first call; the 59.656% root remained 0/13. Mode40 stayed 15.291% and 0/5: its only compiling edit replaced the correct first call and was rejected, while two rounds emitted unusable patch shapes. Mode16 correctly changed the callback user-ID offset `0x34` to `0x0`, extending the matching call prefix 38 to 41 and score 87.933% to 87.981%, but remained 0/7; two later rounds emitted invalid patches. Audit: 13 attempts, 13 lineage edges, all parents present, correct function ownership, no held-out overlap. Receipt: `eval/results/differential-wavefront-v6-source-linked-current.json`.
- Decision: Keep the layout detector and semantic gate. Detection transferred, but prose plus generic line spans is not an adequate action representation for scaffold rebinding or missing-CFG synthesis. Implement controller-owned typed actions separately: `rebind_access_layer` for source lvalue/offset/width/signedness maps, and `insert_cfg_block` for missing call/control-flow regions. Do not spend another equal-budget wave on prompt-only wording changes.
- Linked ideas: IDEA-20260903-01, IDEA-20260902-04, IDEA-20260902-05

### HYP-20260912-01: IDO's register choice can be predicted or validated from target assembly with the uopt save model and lowest-index-first colouring.
- Status: Refuted
- Tested: 2026-09-12
- Test: `eval/uopt_replay.py` replays greedy colouring over every target dump (1,279 scorable functions, 23,332 non-ABI webs) in four priority orders -- model save, oracle (actual colour), construction order, seeded random -- and checks, order-independently, whether IDO's actual register is free given every neighbour's actual register. Webs built three ways: linear redefinition splitting (the model's own), reaching definitions over `solver.cfg`, and Chow-Hennessy block-granular live ranges. A preference order was derived from pairwise choices on half the functions and tested on the other half.
- Evidence: Linear webs are fragments (51.7% single-occurrence). CFG webs fix that (0%) and are sound: IDO's choice is free in 100.0% of cases and every call-crossing value is in s0-s8. But model order reproduces 17.1% of registers against 15.3% random and 24.6% even for the oracle order, and every oracle miss chose a LOWER register than IDO. IDO takes the lowest free register only 25.4% of the time; 9,196 of ~10k held-out non-crossing webs see 12+ free registers. Block-granular interference is refuted by its own soundness test (IDO's choice free only 40.5%). The best derived order reaches 26.7% held-out; spec order 26.2%, round-robin 27.7%, uniform 8.6%.
- Decision: Stop building allocator predictions from post-allocation assembly: the constraints that drove IDO's choice are not visible there, at any of three reconstructions. This also retires the "79% concordant" claim in commit e763f40, already recorded as 50-58% in `solver/liveness.py`. The viable source of uopt's view is its own output: `cc -K` keeps uopt's optimised u-code (`.O`) per candidate compile, which carries its register decisions -- decode that before any further allocation modelling.
- Linked ideas: None

### HYP-20260912-02: Larger local models or a larger context unlock matches on context-blocked and large functions.
- Status: Refuted on the tested cohorts
- Tested: 2026-09-12
- Test: qwen3:32b (dense) at 32K on the top of an 81+ instruction queue; gpt-oss:20b at a fixed 64K (`SOLVER_FIXED_CONTEXT`) in campaign shape on 17 functions confirmed by a GPU-free probe to be refused by the 32K guard today. See `eval/results/bigmodel-large-20260912/README.md`.
- Evidence: qwen3:32b ran at 3.3 tok/s with 13.7 GB offloaded; the memory brake stopped it after one function, whose 5 compiled proposals all equalled the parent score. gpt-oss:20b at 64K: 0 exact, 1 score gain in 17 functions and 96 calls; 51% of proposals invalid (29 of 49 ambiguous `old` anchors); 58% of distinct compiled children codegen-identical to the parent. 64K cost 50 MB of VRAM, and the refused prompts were really 20.7-22.7K tokens: the bytes/2 estimate over-counts by at least 24%. The single gain was a mechanical pointer-stride -> array-index rewrite.
- Decision: Neither parameters nor context is the binding constraint. Fix edit addressing (recover ambiguous anchors) and report codegen-identical children to the model; make pointer-stride rewriting deterministic. Running the campaign at 64K is free but needs the amendment protocol.
- Linked ideas: None

### HYP-20260913-01: Enumerable meaning-preserving source mutations, ranked by a register-signature gradient rather than byte score, close register-allocation-only residuals without a model.
- Status: Partial on the preregistered run; confirmed as development evidence afterwards (not held out)
- Tested: 2026-09-13
- Test:
  - `eval.regalloc_probe search` ran on the 78 pending functions whose only residual fault class was register allocation (cohort frozen at checkpoint 14495, `eval/results/regalloc-20260913/`).
  - The preregistered run (search-1) used local types, commutative swaps, declaration order, inlined temporaries and statement order. Its threshold was at least 15 exact.
- Evidence:
  - search-1: 6 exact, 5 counted (one was found by hand during development). That is the partial band.
  - Commutative swaps were 95% inert (IDO canonicalises operand order), and the existing statement-order family made 88% of variants worse.
  - Probing unmatched functions by hand showed the residuals are mostly m2c artefacts, not allocation priorities: field-caching locals, split struct copies, flattened loops, pre-increments split into temporaries, guards placed after a load.
  - Generators written for those shapes reached 55 of 78 exact in search-4 (5,019 compiles, about 0.3 s each). These 55 were developed on the same cohort.
- Decision:
  - Classify register-allocation residuals by signature and attack m2c artefact shapes with deterministic generators before modelling uopt.
  - Validate transfer on the untouched `regalloc_plus_le2` cohort (145 functions) before claiming generality.
  - Do not use commutative operand order as a primary lever. Reserve `.O` u-code decoding for the true colour-priority residue.
- Linked ideas: HYP-20260912-01
- Follow-up (2026-09-13): the remaining register-only failures were closed by hand-probing each one with `regalloc_probe probe`.
  - Every finding became a generator with a fire test.
  - Final state: 74 of 78 object-exact, re-verified from saved sources.
  - 3 are byte-identical but blocked by target-object artefacts (register-symbol relocations, TU padding nops) and need ROM verification.
  - 1 is a stack-layout residual.
  - New failure modes: rotated loops, compound assignments, typed-table indexing, m2c double scaling on typed externs (a behaviour bug), negation folded into constants, priority inversion from caching a load in a local.
  - Transfer to the untouched 145-function cohort was measured with the generator set frozen before these hand closures: `eval/results/regalloc-20260913/transfer-1`.
