Model capability tracking and training audit

Reviewed October 3, 2026. Source: the local Claude conversation titled “Model capability tracking and information requirements,” session `9807fc6e-f45d-4a0d-afda-5d8ff79fe131`, through its 27,462-task export. I read the discussion, inspected the implementation, recomputed aggregates from WSL receipts, checked selected cited papers, and ran small synthetic compiler experiments. This is an audit and a proposed next experiment; no training, campaign promotion, or production fixes were performed.

The direction is sound: measure which information unlocks which repairs, then train on transferable gaps. The current evidence does not yet show that the model learned compiler reasoning. Several measurement and dataset problems should be resolved before spending a substantial GPU budget.

**What the saved results support**

- The initial six-level experiment contains 306 rows. Its reported match counts reproduce: 11, 9, 4, 14, 22, and 32 out of 51. These are results under its masked-listing checker, not independently recertified object matches.
- The final single-edit system reports 51/51 development and 41/42 held-out. Those flags also reproduce, subject to the same checker limitation. These are repairs of planted defects in already solved functions, not 41 newly decompiled game functions.
- The multi-edit held-out aggregates reproduce: baseline 24/54, learned order 31/54, escalation with baseline order 32/54, and escalation with learned order 30/54. All alternatives lose at least one baseline success. Rejecting them under the stated retention rule was appropriate.
- The exported dataset exists and its SHA-256 agrees with its manifest: 27,462 rows, split into 14,964 train, 612 exam, and 11,886 check. Split-group identifiers do not overlap. This check does not establish absence of semantic duplicates or pretraining contamination.
- Preserving erased edits, trying deterministic tools before attributing a deficit to the model, keeping new generators opt-in, and adding tests that exercise their motivating cases were useful decisions.

**1. High priority: the strongest induction example had no successful probes**

The conversation presents `empty_arm`, seed 1, as evidence that six distinct compiler experiments improved predictions from 11/22 to 19/22. The saved log instead contains eight probe events, all `does-not-compile`; six distinct attempted pairs are not six successful experiments. The first request supplies empty bodies. The commutative-operands seed-1 run also has eight failed probe events; its first pair uses `arg0` and fields absent from the supplied probe environment.

Across the ten runs, 17 of 43 logged probe events failed to compile, including repeated failures. The 19/22 prediction result is real as a recorded score, but it cannot establish learning from successful compiler comparisons. Error feedback can itself be informative; here it does not supply the claimed same/different observations.

The aggregate 101/144 versus 43/72 is descriptive, not a causal test of probing. The predictions reuse a small set of related validation pairs. As a diagnostic of label imbalance, the per-topic majority labels score 112/144; on empty-arm alone, always predicting “same” scores 18/22. Those majority labels were inspected after the experiment and are not a preregistered deployable policy. They show why raw accuracy needs class-balance controls.

Required correction: validate tool arguments, return useful compiler diagnostics, count attempted/distinct/successfully compiled/informative probes separately, and compare against a rule-writing arm that receives no probe results. A model should receive credit for correct predictions and useful experiments, not merely for issuing a probe action. See [rule_induction.py](../eval/rule_induction.py).

**2. High priority: masked assembly equality is being called exactness**

The ladder, full-system experiment, and search-priority trials decide exactness using `mine.mask`. It replaces every `%hi(symbol)` and `%lo(symbol)` expression with an anonymous relocation placeholder. A fresh synthetic IDO experiment reproduced the consequence: `return ga;` and `return gb;` pass that comparison, while the object certificate rejects them because their referenced globals differ.

This proves the checker can accept a wrong program. It does not prove how many reported experiment wins are wrong; that requires recertifying their retained candidates. Report the existing numbers as masked-listing matches until that audit is complete.

The public planter uses a different, unmasked normalized listing. Our changed-global example was correctly distinguished by that normalizer, so the two paths must not be conflated. However, the public rows still carry no object certificate and compare only a function's listing. Their same/different labels describe that projection, not arbitrary semantic equivalence or a complete linked binary. Any exact-repair label needs its own certificate and explicit scope.

See [system.py](../eval/results/edit-capability-20261002/system.py), [trails.py](../eval/results/edit-capability-20261002/trails.py), and the [synthetic compiler receipt](../eval/results/model-capability-audit-20261003/compiler-probes.json). The certificate covers allocated object sections and relocation expressions under the same link environment; it does not certify a whole ROM.

**3. High priority: some training questions omit information that determines the answer**

The public planter compiles complete translation units, but the logic prompts contain only function definitions and a reduced compiler description. They omit surrounding typedefs, layouts, macros, declarations, and other build context. The explain prompts also truncate instruction differences after 40 changed rows; 1,592 training prompts contain that truncation marker.

This is not just a stylistic concern. Under the actual IDO recipe, I compiled the same visible pair:

```c
int probe(T x) { return x / 2; }
int probe(T x) { return x >> 1; }
```

With `typedef int T`, the objects differ. With `typedef unsigned int T`, they are certificate-equal. A prompt that hides `T` cannot uniquely determine the label. More training cannot recover evidence the input does not supply.

Required correction: include a minimal, provenance-approved context closure and the actual recipe identity, or explicitly allow a request for missing evidence. Preserve complete observations in the record even if the model initially receives a compact view. Do not call failure on an underdetermined prompt a reasoning deficit. See [logic_tasks.py](../eval/logic_tasks.py) and [repair_prompts.py](../eval/repair_prompts.py).

**4. High priority: training admission needs stricter split and identity checks**

`logic_tasks.split_of` treats every non-`dev` split as train, except an explicitly held-out repository. I reproduced `test`, `heldout`, `check`, and missing split labels all becoming `train`. The current generated file has disjoint split-group IDs; this is a fail-open importer defect, not a claim that this export already contains SBK held-out answers.

The 27,462 rows have only 27,434 unique task IDs. The multi-edit input contains 1,023 rows but 1,009 unique IDs, with each repeated input generating two task types. There are 28 repeated prompts across the exported splits and no observed prompt with conflicting completions. These duplicates still inflate example weights and undermine ID-based joins. IDs should bind the actual sources, complete recipe/context, and task type; import should reject duplicates and unknown splits.

The existing source-task loader returns zero examples for this export, and the other repair loader expects an incompatible record shape. A dedicated logic-task grader/loader remains necessary, consistent with Claude's final message listing the grader as future work. This is a generated corpus, not a completed or validated training pipeline. Its exam contains 612 tasks from only 72 functions and nine source-file groups; those are correlated observations, not 612 independent demonstrations of generalization.

The `--weights` option changes per-class caps, not example loss weights. Raising a cap for an already scarce class does nothing. That may be a valid sampling policy, but it should not be presented as generally increasing the training weight of a hole.

**5. High priority: the coverage gate can approve a much more expensive or incomplete run**

`coverage.compare` evaluates cost only on cases both arms solve. A two-case reproduction accepts “same coverage, cheaper” when baseline total cost is 11 and candidate total cost is 10,009: the candidate saves one compile on the solved case and wastes 10,000 on the unsolved case. It also accepts when that unsolved case has no candidate row at all.

Required correction: require complete, valid observations for promotion and measure total panel cost, including unsuccessful attempts, under declared equal budget ceilings. Shared-success cost is useful as a secondary diagnostic. The experiment's `compiles` field is also a search-child count: the search scores its root before initializing that counter, and source attribution can perform additional compiler work. Call it a logical candidate budget until actual compiler invocations are separately metered.

See [coverage.py](../eval/coverage.py) and the [aggregate/reproduction receipt](../eval/results/model-capability-audit-20261003/receipt.json).

**6. The pruning/cache analogy needs a coverage qualification**

With a sound certificate, a bad cheap proposal cannot become an accepted wrong match. It can still waste the remaining budget or cause search to miss a solution. The current escalation stops widening as soon as a tier yields any improving child, which can lead into a dead end while a deferred branch contains the solution. The observed lost cases make this distinction practical.

Running the untouched baseline from the original state after a failed cheap search preserves the baseline's successes only if it retains the full baseline budget and environment. The post-hoc cascade's extra cost must be included. A claim of guaranteed retention at the same total budget needs separate evidence.

I would retain deferred queues and resume them when downstream progress stalls, then compare this with a complete baseline fallback. An empirical same-object observation is also insufficient to merge arbitrary source search states: different source forms can expose different later edits.

**What I would change in the scientific argument**

The information ladder is worth keeping, but success at one sample is not a capability boundary and failure at six small cases does not establish inability. The comparison “real line only: 15” versus “perfect line plus class: 32” changes two inputs, so it does not isolate the benefit of classification. Cross actual/oracle localization, actual/oracle classification, assembly presentation, and output format on the same cases with repeated samples.

The new “explain” task produces an edit script. That can teach useful inverse mappings, but it is not a verified causal explanation. The compiler verifies particular outputs and transformations; it does not certify a universal prose rule from a few examples. Likewise, an erased statement is not automatically a dead store: preprocessing, undefined behavior, and other optimization effects need separate diagnosis.

Keep compiler rules in an auditable catalog, but do not prohibit learning their regularities in weights. Condition the learned priors on compiler evidence and test transfer. “The rules are compiler-specific” is a reason to test scope, not a reason to avoid learning them.

Repeatedly reweighting from the exam makes it a development set, as intended for finding holes. SM64 held out from this fine-tune cannot by itself distinguish transfer from base-model recall. Add fresh synthetic compositions and a separate unexposed game/compiler evaluation, with clone grouping across source files and games. Preserve the project's existing held-out exclusions.

**Literature check**

[Echo](https://arxiv.org/abs/2609.18706) supports joint source/configuration search with compiler feedback; its reported improvements do not establish gains for this MIPS pipeline. [Recompile More, Preserve Less](https://arxiv.org/abs/2609.05370) supports the concern that compilation or a finite test suite alone can reward incorrect reconstructions. These were real, relevant citations.

[ReST-EM](https://arxiv.org/html/2312.06585v4) does describe restarting fine-tuning from the base pretrained model each round to reduce drift. That is a reasonable experimental arm, not a universally required training recipe. [Expert Iteration](https://arxiv.org/abs/1705.08439) supports using stronger search to teach a cheaper proposer. Neither makes a locally improving child automatically a good training target for eventual exactness.

[Nova](https://proceedings.iclr.cc/paper_files/paper/2025/file/ef283d62b4bce30854a8d4827f331229-Paper-Conference.pdf) uses representation-learning objectives over functionality and optimization. A SAME/DIFFER text task is an inspired auxiliary task, not a reproduction of Nova. For exact matching, preserve code-generation distinctions while learning semantic invariants; do not collapse the distinction you need to reproduce.

[Doing Experiments and Revising Rules](https://arxiv.org/abs/2402.06025) supports maintaining competing hypotheses and selecting informative experiments, on its own experimental task. [LayerSkip](https://arxiv.org/abs/2404.16710) couples training for early exits with verification by the remaining layers. Neither establishes exponential model speedups or a lossless arbitrary pruning policy here. Measure end-to-end cost and coverage before investing in custom sparse kernels.

**My proposed next experiment**

1. Repair certification, admission, observation completeness, and cost accounting first. Reclassify historical claims without overwriting their original receipts.
2. Give each observed failure one of several actionable descriptions: missing evidence, invalid proposal/tool call, wrong localization, unsupported edit, ineffective probe selection, search-budget exhaustion, or a certified but nonmatching candidate. Allow multiple causes and “unknown.” Store compiler recipe, model identity, evidence supplied, function family, size, assistance, and budget with every result.
3. Build paired context experiments where the same apparent problem requires different answers after changing one relevant fact: signedness, aliasing, call effects, optimization level, or live range. Include irrelevant-context controls. Train the model to request the missing fact when the evidence cannot determine the answer.
4. Mine successful search trajectories into proposal examples with explicit parentage and the actual observations available then. Include useful intermediate steps even when their immediate similarity declines. Compare with a small probe-selection task where candidate probes separate competing hypotheses and the reward is improved prediction on fresh contexts per compile.
5. Run a controlled pilot on the actual intended student model: unchanged baseline, repair-only SFT, logic-only SFT, and mixed training. Match training budgets across trained arms and inference/search budgets across all arms. Evaluate both planted and natural decompiler residuals, including larger functions and interactions between edits. A logic-task accuracy gain without a real-repair coverage gain is auxiliary progress, not a successful decompiler upgrade.
6. Lead with certified coverage at fixed resource budgets, broken down by capability family and assistance tier. Add code-byte coverage, total wall/GPU/compiler cost, and retained-success counts. Cluster uncertainty estimates by source family or translation unit. Run the information ladder again after training to see whether it needs less evidence, makes better use of the same evidence, or merely memorized a benchmark shortcut.

This directly tests “teach it to fish”: can it identify what it does not know, acquire discriminating evidence, and transfer a repair method to an unseen context? A larger corpus alone cannot answer that.

**Verification and scope**

The focused existing suites passed: 53 tests across coverage, logic tasks, search priors, operator edits, and gap edits. The audit reproductions expose gaps those suites do not cover. Eight small synthetic compilations used the real IDO recipe in WSL `/tmp`; their first run was repeated to preserve a complete receipt. No GPU work ran. The aggregate script emits no held-out source or answers. No historical winning game candidate was independently recertified, so this audit does not provide revised certified match totals.

Reproducers: [audit.py](../eval/results/model-capability-audit-20261003/audit.py) and [compiler_probes.py](../eval/results/model-capability-audit-20261003/compiler_probes.py). Receipts include source hashes for the principal audited modules. Only this audit note and its reproducer/receipt directory were added; existing project work was left untouched.
