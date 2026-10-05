# Research mapped to gameDecomp's current problems

Research date: 2026-09-28. This is a literature assessment and a proposed experiment sequence, not an implementation or deployment. Paper mechanisms are identified separately from proposed adaptations. None of the cited papers establishes a matching-decompilation improvement on this project's IDO 5.3/MIPS workload.

The strongest near-term hypothesis is that a small archive of useful source states, combined with bounded exploration through worse-ranked intermediates, will outperform further tuning of a single assembly-distance ranking. That is a hypothesis to test, not a finding from our campaign.

## What the current project evidence says

| Problem | Local evidence | Research implication |
| --- | --- | --- |
| A distance vector is guidance rather than ground truth | [Follow-up audit](claude-review-followup-20260928.md): hunk/full vectors differ on 59/861 sampled endpoints; direction differs on 2/446 comparable edges. Full alignment remains heuristic. | Repair the measurement first, then evaluate search policies prospectively. |
| Useful intermediate source states can be discarded | [Register search](../solver/regalloc_search.py) admits improving/tied children; worse-gradient children do not enter the ordinary beam. Family diversity already exists. | Test preservation of different future possibilities, beyond immediate rank. |
| A better ranking cannot recover an absent candidate | [Compiler-effect experiment](../eval/results/compiler-effects-20260926/RESULT.md): its full evaluated one-edit pool contained no exact candidate, and the predictor failed its promotion check. | Test composition and reachability, not just reranking that pool. |
| Object equality and search-state equality are different | [Mutation generation](../solver/regalloc_mutations.py) depends on source form and source-bound compiler evidence. | Cache compile results separately from the source states that can be expanded. |
| Additional execution cases must affect subsequent decisions | [Exposure audit](../eval/results/claude-review-20260928/investigation-exposure.json) found no completed added comparison against a base-passing candidate in the saved autonomous canaries. | Add counterexamples to future evaluation, then measure their actual discriminatory value. |
| Historical winners received unequal opportunities | `truth_vs_score.py` measures observed exact descendants under the historical search policy. | Unexpanded paths are missing outcomes, not observed failures. |
| Type recovery still uses assisted context | [Type constraints](../solver/type_constraints.py) measures header layouts and builds bounded type plans. | Binary constraint inference is a separate route toward less assistance. |

Code changes are concurrent with this review. At inspection, main `regalloc_search.py` and `eval/agentrepair.py` already contained object comparisons, random audit accounting, and a restart without reuse after conclusive key violations. The frozen `eval/results/resume-pipeline-20260908/code/` copies differed. The earlier review's implementation findings should not be read as an unchanged description of the development checkout. No current capability totals are inferred from dated status prose.

## 1. Preserve stepping stones: MAP-Elites and STOKE

**Sources.** Jean-Baptiste Mouret and Jeff Clune, [Illuminating search spaces by mapping elites](https://arxiv.org/abs/1504.04909), 2015, especially section 3 and the stepping-stone analysis in section 6.1. Eric Schkufza, Rahul Sharma, and Alex Aiken, [Stochastic Superoptimization](https://theory.stanford.edu/~aiken/publications/papers/asplos13.pdf), ASPLOS 2013, sections 3.1–3.2, 4.3, and 4.7.

**Research mechanism.** MAP-Elites retains good solutions within different descriptor cells instead of retaining only the global best; mutation can move between cells. STOKE explores program space stochastically and sometimes accepts a higher-cost intermediate. STOKE also discusses failures of its own incremental cost signal. Neither paper says that a lower local score means a path cannot succeed.

**Proposed adaptation.** Keep an archive capped at 8–16 source states, described by a small combination of residual structure and available edit capabilities. Keep source identity and lineage even when compiled outcomes coincide. Reserve a fixed, small exploration allowance for worse-gradient children; continue to preserve the best verified incumbent. Source-only capability previews can guide admission, but compiler-dependent attribution must be regenerated for the child. A name change alone should not fill the archive with apparent novelty.

**Test.** Compare current beam, archive-only, worse-step exploration-only, and their combination on identical frozen roots and compile budgets. Record certified matches, cost, and winning paths that actually traverse discarded intermediates. Count archive occupancy only as a diagnostic. Start with existing generators so a gain can be attributed to search rather than a larger mutation vocabulary.

**Limit.** STOKE optimizes loop-free x86 programs for performance, whereas we seek C producing a fixed binary. Our asymmetric, evidence-gated generator does not automatically satisfy its sampling assumptions. This is an algorithmic adaptation, not a transfer of its guarantees.

## 2. Make failures accumulate into a stronger test set: CEGIS

**Source.** Armando Solar-Lezama, Liviu Tancau, Rastislav Bodík, Vijay Saraswat, and Sanjit Seshia, [Combinatorial Sketching for Finite Programs](https://people.eecs.berkeley.edu/~sseshia/pubdir/asplos06-final.pdf), ASPLOS 2006, section 5.4.

**Research mechanism.** Counterexample-guided inductive synthesis alternates candidate construction with verification. A failed verification supplies an input that becomes a constraint on later candidates.

**Proposed adaptation.** The project already has much of the plumbing in `execution_experiment.py`. Retain every admitted, target-completed counterexample, scope it to target/ABI/environment identity, and replay it on later candidates. A demonstrated failure must prevent promotion to a semantic-pass champion. When a case is added, refresh affected evaluations or make their case-set version explicit; do not compare stale and expanded panels as if they were the same test. Keep target faults and unsupported states inconclusive.

**Test.** Cap additional input proposals per investigation, compare fixed-panel and accumulating-panel policies, and measure base-pass candidates subsequently falsified, repeated known failures, execution cost, and final certified matches.

**Limit.** The paper's verifier is formal and bounded. Our admitted executions are finite observations; this adaptation does not make passing candidates semantically proven or byte-exact.

## 3. Attack the optimizer-key assumption with controlled variants: EMI

**Source.** Vu Le, Mehrdad Afshari, and Zhendong Su, [Compiler Validation via Equivalence Modulo Inputs](https://www.cs.ucdavis.edu/~su/publications/emi.pdf), PLDI 2014, sections 3.1–3.2.

**Research mechanism.** EMI constructs variants whose behavior agrees on selected inputs, then looks for compiler inconsistencies. Its guarantee concerns those inputs, not identical object bytes.

**Proposed adaptation.** Borrow the systematic variant-generation and falsification method. For real retained candidate sources, vary blank lines, statement grouping, explicit line directives where admitted, and macro locations. Compute keys, compile original-layout sources under the same recipe, and compare every equal-key pair using the project's certificate. Preserve collision reproducers with source, toolchain, flags, hashes, and certificate scope. Keep deliberate stress tests separate from random operational audits: they answer different questions.

**Test.** Measure collisions and inconclusive comparisons by recipe and variant family. A confirmed collision rejects that key-equivalence assumption; a clean sample only bounds observed risk. Track raw object hashes separately because debug metadata can differ while certificate-covered sections agree.

**Current fit.** This extends the development checkout's new auditing rather than duplicating it. It targets precisely the line normalization uncertainty identified in the review.

**Related boundary.** Nuno P. Lopes et al., [Alive2: Bounded Translation Validation for LLVM](https://users.cs.utah.edu/~regehr/alive2-pldi21.pdf), PLDI 2021, is a useful model of per-transformation refinement checking, including undefined behavior and bounded loops. It is LLVM-specific, so it is not a ready-made IDO/MIPS validator. Building a new semantics stack is lower priority than exploiting the object oracle already available.

## 4. Separate policy benefit from historical exposure: counterfactual evaluation

**Source.** Miroslav Dudík, John Langford, and Lihong Li, [Doubly Robust Policy Evaluation and Learning](https://icml.cc/2011/papers/554_icmlpaper.pdf), ICML 2011.

**Research mechanism.** Doubly robust evaluation combines a reward prediction with a correction based on the probability that the logging policy selected the observed action. It requires coverage of the actions being evaluated; it cannot identify outcomes for actions assigned zero probability.

**Proposed adaptation.** Stop interpreting historical exact-descendant frequency as the causal effect of a ranking policy. Going forward, log eligible actions, decision context, chosen action, selection probability, cost, and outcome. A small randomized exploration allowance gives suppressed-but-eligible choices measurable exposure. For full search trajectories, the one-step contextual-bandit estimator is insufficient without sequential treatment.

**Test now.** Because our compiler is rerunnable, a paired prospective policy comparison is simpler than retrofitting an estimator onto deterministic history. Use isolated state for each arm. Old logs can nominate hypotheses and supply roots; missing branches remain unknown.

**Statistical companion.** Giorgio Corani et al., [Statistical comparison of classifiers through Bayesian hierarchical modelling](https://arxiv.org/abs/1609.08905), Machine Learning 2017, motivates accounting for repeated, correlated evaluations. Use paired function/lineage or translation-unit clusters, not thousands of descendant attempts as independent observations. Its classifier cross-validation covariance model should not be copied unchanged.

## 5. Infer constraints before choosing C layouts: Retypd and BinSub

**Sources.** Matthew Noonan, Alexey Loginov, and David Cok, [Polymorphic Type Inference for Machine Code](https://arxiv.org/abs/1603.05495), PLDI 2016. Ian Smith, [BinSub: The Simple Essence of Polymorphic Type Inference for Machine Code](https://arxiv.org/html/2409.01841v1), 2024 preprint, especially sections 3.1–3.3.

**Research mechanism.** These systems recover types using constraints that support subtyping and polymorphism; BinSub uses algebraic subtyping and explicitly depends on recovered dataflow/IR quality.

**Proposed adaptation.** Start from immutable load/store widths, call boundaries, offsets, and conservative value flow. Propagate compatible read/write and caller/callee constraints before selecting a particular C declaration. Emit several retractable layout hypotheses when evidence is ambiguous. Preserve padding and unknown names. This would complement the current header-assisted layout enumerator; its purpose is to reduce dependence on supplied game types.

**Test.** Use a small development cohort of connected functions with unresolved pointer/member types, withholding reference game headers and bodies from inference. Evaluate access coverage, unsupported assumptions, newly compiling candidates, and certified matches separately. Use reference types only after inference for evaluation.

**Limit.** A compatible type is not necessarily the original type or a type that induces the required IDO allocation. Better constraint solving cannot repair incorrectly recovered value flow.

## 6. Preserve rewrite alternatives where ordering matters: egg

**Source.** Max Willsey et al., [egg: Fast and Extensible Equality Saturation](https://homes.cs.washington.edu/~cnandi/docs/popl21-cr.pdf), POPL 2021, sections 2.2 and 4.3.

**Research mechanism.** An e-graph retains equivalent expressions while applying rewrites, delaying selection until extraction. This avoids losing alternatives through an early rewrite choice.

**Proposed adaptation.** First demonstrate a missing two-rewrite path in a pure, precisely typed local expression. Preserve its intermediate alternatives and compile the final C spellings. A bounded enumerator may suffice before introducing an e-graph library.

**Test.** Freeze 5–10 motivating and transfer residuals; compare existing generation with bounded composition under equal compile budgets. Require an emitted useful candidate and improved certified yield, not just more equivalent expressions.

**Limit.** C side effects, overflow, aliasing, and conversions constrain valid equalities. Exact IDO output depends on nonlocal allocation and scheduling, so ordinary local extraction costs are inadequate. Lower priority than the small search archive; no whole-pipeline e-graph rewrite is justified yet.

## 7. Closest direct comparison: Echo

**Source.** Jun Bi et al., [Echo: Learning-based Matching Decompilation using Trusted Back Translation](https://arxiv.org/html/2609.18706v1), September 16, 2026 preprint; sections 2, 5, 6.4, and 7.4–7.5.

**Research mechanism.** Echo combines compilation feedback, rule-based mutation, specialized neural repair, and reasoning-based repair. It evaluates x86 with GCC/LLVM. Its exactness criterion is normalized, address-invariant assembly equality, and its mutation search greedily accepts distance improvements.

**Project interpretation.** This is highly relevant related work and supports investigating staged repair. Much of that structure already exists here. It neither resolves our stepping-stone question nor establishes certificate-level equality for our MIPS objects. Its reported match rates must not become our expected gains.

**Test.** Compare interleaving deterministic repair and model repair against our existing allocation, with identical roots, model access, compiler budget, and total model cost. Keep the known IDO recipe fixed. A broader compiler-configuration search is unnecessary where the correct recipe is already established.

## Lower-priority budget research

Lisha Li et al., [Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization](https://www.jmlr.org/papers/v18/16-558.html), JMLR 2018, allocates resources through successive-halving brackets. It suggests testing several breadth/depth allocations rather than one fixed budget policy. However, sparse exact matches and delayed stepping-stone payoffs make early assembly score an uncertain promotion signal. Before using aggressive pruning, finish a randomized subset of early-pruned searches to measure missed late successes. Do not interpret zero early wins as proof that a mutation family is useless.

## Proposed experiment sequence

1. **Establish trustworthy inputs.** Reconstruct compatible historical full listings; freeze metric versions and source/target/toolchain identity. Add targeted key stress tests alongside the existing development audit. These are prerequisites for interpreting policy gains.
2. **Test the archive and exploration separately.** Start with 40 frozen unresolved development functions, stratified by residual class and assistance. Use four search arms, three seeds, and a proposed cap of 128 real compile calls per run. Those numbers are an initial budget proposal, not a power calculation. Report this as a 128-call result; include a 300-call sensitivity comparison before generalizing to the larger search budget or ruling out delayed gains. Charge baseline, keys, audits, failed compiles, and model work in cost reporting; also compare at equal wall/CPU cost because compile counts alone favor expensive key schemes.
3. **Evaluate counterexample accumulation before deployment.** Add admitted cases to versioned candidate evaluation and compare against the fixed panel. Report the number of base-passing candidates actually exposed to a completed additional comparison.
4. **Pursue type constraints or rewrite composition where the residual census supports them.** These address candidate construction. They should not be mixed into the first policy comparison.

The primary outcome is additional distinct functions with verifier-certified exact results at equal cost. Report SOLVED, header-assisted, and recovered results separately, with inherited lineage assistance carried through. Secondary outcomes are cost-to-first-exact, distinct verified outcomes, and counterexamples found. A better average similarity score alone does not pass promotion.

Pair policies on the same roots and isolate their evolving state. Cluster uncertainty by function/lineage, and by translation unit where shared context creates dependence; if shared global learning cannot be isolated, use independent campaign replicas. Tune only on the development partition. Preserve the held-out game's answers and select promotion thresholds before viewing its results. No small pilot can establish general improvement on its own.
