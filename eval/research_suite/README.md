# Research experiment tests

This opt-in suite tests the adaptations in [the research map](../../docs/research-map-20260928.md). It writes fresh experiment directories and never dispatches a campaign, promotes a match, or changes the KB. Native runs use IDO, the resolved project compiler recipe, the project frontend policy, and `byte_certificate.certify`.

**Baseline correction (September 28 review):** `production` now enables existing stepping-stone roots and optimizer-key reuse; `production_diverse` adds the existing family-diverse beam. Both call `solver.regalloc_search.search` with the current main-tree helper's 2% object-certificate audit and record their configuration. They are not a replay of the frozen checkpoint 33362 code, whose reuse checking differs. Earlier smoke receipts used the deficient unkeyed/disabled baseline; retain them as historical wiring checks only. See [the review response](../../docs/research-suite-review-20260928.md).

## What is wired

| Problem / research connection | Runnable experiment | Comparison or receipt |
|---|---|---|
| Search prunes useful intermediates; MAP-Elites / STOKE | `beam`, `archive`, `explore`, `archive_explore` mechanism arms | Simplified shared mechanism engine; full-listing gradients; source-distinct paths; no claim of improvement over the campaign |
| Existing solver baseline and family diversity | `production`, `production_diverse` | Existing register search, enabling roots, optimizer key, certificate rechecks, 2% audit; default comparison |
| Worse-scoring sources may offer better follow-ups; evolvability search | `mutation_count`, `evolvability`, and their `_diverse` variants | Policies inside the same register-search engine; bounded mutation previews, charged child probes, source-distinct alternatives, logged selection probabilities |
| Mutation vocabulary versus selection policy | `production`, `evolvability`, `production_coalesce`, `evolvability_coalesce` | Paired 2 x 2 comparison inside register search; scalar-local coalescing is the only vocabulary toggle |
| Extra cases overturn base-panel passes; CEGIS | `panels` command | Fixed versus retained cases, target-first admission, panel versions, replayable inputs, exposed-candidate denominator |
| Optimizer-key collisions; EMI-inspired stress | `key-audit` command | Every selected variant really compiles; equal keys compared with object certificates; missing/unavailable comparisons separated |
| Hunk-derived heuristic bias | `reconstruct` command | Strict full-listing reconstruction; old/new vectors, target/candidate/metric hashes |
| Two edits needed before recompiling; egg-inspired composition | `pure` versus `compose` arms; `proposals` command | One-step versus depth-two source construction in a deliberately small unsigned-expression grammar |
| Byte-offset loads hide useful types; Retypd / BinSub | `types` mechanism arm; `proposals` command | Cited scoped constraints and compiled hypotheses; does not yet compare against the existing binary-types route |
| Generation/repair allocation; Echo-inspired scheduling | `staged` versus `interleaved` arms | Identical frozen proposal pool; screen-then-expand versus expand each proposal; recorded generation cost |
| Breadth versus depth; Hyperband-inspired allocation | `brackets` arm | Static deep/wide budget split; all restart/root compiles charged |
| Selection bias in historical logs; doubly robust evaluation research | Paired runs and decision receipts | Record eligible choices/probabilities; report function/cluster counts; no unsupported off-policy estimator |

These are small mechanism experiments, not full implementations of the papers. The archive is a bounded pending set using residual descriptors and source-distinct ties. Its recorded descriptor is `residual-first-with-leftover-source-ties`: distinct residuals can consume every slot and exclude same-object stepping stones. This is one design variant, not an established choice. Before a real archive/exploration benchmark, implement the options in the existing register search and preregister competing descriptors/source quotas. Exploration does not inherit STOKE's sampling guarantees. Brackets do not perform successive halving. Scheduling replays supplied proposals; it does not test a live model's response to new feedback. Composition and type construction deliberately decline most C.

## Run the checks

Mechanism and command tests work on Windows or WSL:

```powershell
python -m pytest -q tests/test_regalloc_evolvability.py tests/test_regalloc_search.py tests/test_research_search.py tests/test_research_counterexamples.py tests/test_research_proposals.py tests/test_research_suite.py tests/test_research_runner.py tests/test_research_cli.py
```

Run native compilation in WSL with a **new output directory outside `/mnt`**. From the repository directory in WSL:

```bash
/home/grant/decomp/sbk1/.venv/bin/python -m eval.research_suite smoke \
  --repo /home/grant/decomp/sbk1 \
  --output /home/grant/decomp/experiments/research-smoke-my-run
```

The smoke command constructs synthetic target objects, exercises all registered arms, checks positive/negative/failure controls, tests layout and `__LINE__` keys, and compiles emitted composition/type proposals. Synthetic answers are deliberately supplied positive controls, never evidence of discovery or campaign improvement. Native runs should be scheduled around competing campaign/pilot work.

## Freeze development tasks and compare policies

After the pilot finishes, select unresolved **development** candidates using a frozen `eval.triage` census of small residuals, recording generator coverage and assistance strata before outcomes. The previous hard register-dominant cohort is not the default benchmark. Use candidate snapshots, binary-derived evidence, and explicitly labeled header context. Do not select reference C bodies or held-out answers as roots/proposals. Input paths below are relative to `tasks.json`:

```json
{
  "tasks": [{
    "id": "candidate_001",
    "function": "myFunction",
    "source": "candidates/myFunction.c",
    "target_object": "objects/myFunction.o",
    "compile_target": "build/src/engine/viewport_manager.o",
    "context": "candidate-headers",
    "assistance": "header_assisted",
    "cluster": "viewport_manager",
    "provenance": {"attempt_id": 123, "target_origin": "binary extraction receipt path or hash"},
    "proposals": []
  }],
  "settings": {
    "arms": ["production", "production_diverse"],
    "baseline": "production",
    "seeds": [0, 1, 2],
    "budget": 128,
    "search": {"beam": 3, "depth": 4, "archive_size": 12, "explore_rate": 0.2}
  }
}
```

Replace the example function/target recipe with the candidate's actual identities. The object must expose the requested isolated function at `.text` offset zero. This validates the function/object binding, not the object's provenance from the original ROM or the supplied function-to-TU mapping. Keep the extraction receipt in `provenance`.

```bash
PY=/home/grant/decomp/sbk1/.venv/bin/python
$PY -m eval.research_suite freeze --config /path/to/tasks.json \
  --repo /home/grant/decomp/sbk1 --output /home/grant/decomp/experiments/frozen-roots
$PY -m eval.research_suite run --bundle /home/grant/decomp/experiments/frozen-roots \
  --repo /home/grant/decomp/sbk1 --output /home/grant/decomp/experiments/four-arm-run
```

Freezing copies candidates, targets, `.h`/`.inc` context, and recorded proposals; it hashes the compiler tools, project headers, recipes, and participating implementation. Runs validate the bundle and environment before and after each arm. Changes require a newly frozen bundle. The suite does not copy reference translation-unit C.

Each task/seed/arm has a fresh compiler and output directory. `attempts.jsonl` and `attempt-*/` retain source, object, complete listing, frontend report, certificate, failures, parent hashes, and timing. `keys.jsonl` counts successful and failed key requests; `resolutions.jsonl` records reused sources and the source they reuse, separately from real compiles. `result.json` records the path, effective policy, reuse/audit counters and budget; `results.json` and `summary.json` aggregate runs. No compile-result cache is shared across arms.

The production arms preserve the solver's budget convention: **real compiles + 0.14 × key calls**, including baselines, failures and expansion/audit rechecks. `budget_spent` and `budget_overshoot` expose the solver's boundary behavior: it checks before an action, so a final action can cross the nominal threshold. Native real-compile calls also retain the hard integer cap. Mechanism arms do not use keys; do not call cross-engine results an isolated policy comparison. The 0.14 factor is an inherited cost model, not a fresh timing measurement for this adapter. Wall time includes setup and post-arm validation; contention and verifier overhead prevent treating it as equal CPU cost.

Only a source-bound object-section certificate **and** frontend pass can produce a native exact result. Exact covers the certificate's allocated sections and relocations under the same link environment; it is not a whole-ROM certificate. Normalized listings only guide search.

Assistance labels are `synthetic`, `header_assisted`, `recovered`, `declared_unassisted`, and `unknown`. A label is supplied provenance, not an independent capability audit. Summaries group by root assistance and separately count exact functions by the winner's inherited assistance. Repeated seeds contribute paired trials, not additional independent functions. Distinct function sets use `(cluster, function)`; choose stable clusters across repeated tasks. Secondary gradient outcomes compare the best **actually compiled** complete-listing gradients lexicographically; missing or unequal starting gradients do not enter that denominator. `shared_production_engine` identifies comparisons using the corrected common engine.

For allocation tests, add proposals such as:

```json
{"id":"proposal_1", "source":"candidates/proposal_1.c", "assistance":"header_assisted", "generation_seconds":2.5}
```

Missing proposal assistance becomes `unknown` (except explicitly synthetic tasks); it never silently becomes unassisted. Then run `--arms staged interleaved --seeds 0 1 2` and compare with:

```bash
$PY -m eval.research_suite summarize --input /path/to/run/results.json \
  --baseline staged --output /path/to/new-allocation-summary.json
```

The standalone summarizer computes descriptive counts from input JSON and labels that input unverified; it does not re-certify artifacts. Native `run` constructs its own results from actual compiler receipts. Both default to baseline `production`; set `settings.baseline` or `summarize --baseline` explicitly for other studies. Compare scheduling arms to one another: giving recorded proposals to an allocation arm changes candidate access relative to register search.

## Mutation opportunities and evolvability

All production-engine arms preserve enabling roots, optimizer-key resolution,
source-bound certificate rechecks, audits, lineage, beam capacity and depth. The
`_diverse` suffix retains the existing preference for distinct mutation families.
The best observed result is kept separately from the frontier; worse-gradient
sources can be expanded by the two new selection policies.

- `mutation_count`: rank the count of distinct, not-yet-expanded successor sources
  found in a bounded generator prefix. Duplicate source proposals and already-seen
  sources do not increase the count. This is a branching-count control, not a
  claim that distinct source strings represent useful novelty.
- `evolvability`: sample up to `mutation_probes` children per potential parent,
  round-robin, uniformly without replacement within its preview. Rank first by
  the fraction beating the common incumbent gradient frozen before that sampling
  round, then distinct changed normalized listings per sample, mutation-family
  breadth, preview count, and current gradient. Failures count in the sampling
  denominator. This is a lexicographic heuristic; it is not a calibrated match
  probability or an estimate of distance to original source.
- Selection reserves `explore_rate` probability for a uniform draw among eligible
  parents at each beam slot. Choices are without replacement; `_diverse` conditions
  eligibility on families not yet selected while those remain available. All
  conditional selection probabilities and probe proposal probabilities are logged.

Defaults: `mutation_preview=64`, `mutation_probes=2`, `explore_rate=0.2`;
the run seed also sets `selection_seed`, independently of the audit RNG. Counts
are lower bounds if a preview is capped, **not proof of exhaustion**. Unknown-tail
parents remain eligible, and actual expansion resumes the original generator
including its unpreviewed tail. Experimental actual expansions have a separate
finite raw-proposal guard of `max(32, mutation_preview + 1, budget * 8)` per parent; reaching it is logged
as `generation_cap`. This guard is another declared policy difference to the
production baseline and should be checked when interpreting comparisons.

Previewing a keyed candidate first requires its own real compile and certificate
check, charged to the same budget. Every child probe uses the normal compiler/key
path, can discover an exact only through the normal oracle, and consumes the same
budget as any other evaluation. Its result is retained for subsequent expansion;
it is not compiled again merely because it was a probe. Shared optimizer keys and
equal listings do not merge source states. Some sampled observations can be key
reuse predictions; they retain `keyed` provenance and require the usual real
check before expansion. Listing diversity remains a heuristic, not byte identity.

For a paired study, freeze these settings with the actual development tasks:

```json
{
  "arms": ["production", "production_diverse", "mutation_count", "mutation_count_diverse", "evolvability", "evolvability_diverse"],
  "baseline": "production",
  "seeds": [0, 1, 2],
  "budget": 128,
  "search": {"beam": 3, "depth": 4, "mutation_preview": 64, "mutation_probes": 2, "explore_rate": 0.2}
}
```

Put this object under `settings` in the task configuration and freeze a **new**
bundle: older implementation hashes intentionally fail validation. Then use the
existing `run` command. For the family-diverse comparison, additionally summarize
with `--baseline production_diverse`. Include the probe/recheck costs and any
preview/generation caps in analysis. The compile budget is shared, not supplemented
by an uncharged lookahead allowance. Narrow raw-count exploration with
`explore_rate=0` is a separate preregistered ablation, not the default arm.

`result.json` records all policy parameters, seeds, decisions, probe flags,
sample denominators and cap receipts. Synthetic graph tests establish mechanics
(dead ends, worse intermediates, equal-object enablers, budgets and restart
accounting), not a measured IDO yield gain. These experimental observations do not
automatically become training data or campaign configuration.

Research motivation: [Evolvability Search (2016)](https://doi.org/10.1145/2908812.2908838)
selects for offspring behavioural diversity; [Quality Evolvability ES (2021)](https://arxiv.org/abs/2103.10790)
combines offspring quality and diversity. The discrete C-mutation policies here
are bounded adaptations, not implementations of those papers' robotics methods.

## Coalescing versus selection: the paired factorial

Use `production`, `evolvability`, `production_coalesce`, and
`evolvability_coalesce` with identical frozen tasks, seeds and budget. The two
`_coalesce` arms set `coalesce=True` on the existing search, which threads the
switch through ordinary expansion, lookahead previews and key-violation restarts.
Other settings match the corresponding arm without that suffix. Existing
campaign/default calls retain `coalesce=False`; there is no live amendment.

`solver.scalar_coalesce` proposes up to 12 ordered pairs of leading plain scalar
locals whose visible uses are textually disjoint. It removes the second
declaration and rewrites its owned identifier tokens to the first local. Comments,
literals, member names and other functions are preserved. It declines later
declarations, visible address escapes, loops/jumps/labels, body directives and
uses of source-defined macros. The grammar is intentionally narrower than the
earlier regex probe. Included headers are not expanded, and this is neither a
complete binding/liveness analysis nor a semantic-equivalence proof.

Different integer types are allowed as explicit hypotheses: labels include, for
example, `scalar_coalesce:temp_h0->temp_v0:s32->s16`. This can change truncation or
signedness. Floats only merge when type spellings match. The ordinary frontend
and object certificate gate remains authoritative for every candidate.

For the 2 x 2 comparison, set:

```json
{"arms": ["production", "evolvability", "production_coalesce", "evolvability_coalesce"],
 "baseline": "production", "seeds": [0, 1, 2], "budget": 128,
 "search": {"beam": 3, "depth": 4, "mutation_preview": 64,
            "mutation_probes": 2, "explore_rate": 0.2}}
```

Summarize again with `--baseline evolvability` to measure the family's contribution
under exploration, and with `--baseline production_coalesce` to compare selection
with the larger vocabulary. Report all four cells, including losses, gradient
progress, compile/key/probe/recheck costs and missing pairs. A combined arm winning
does not by itself establish which component helped.

The runnable exposed-case replay and preregistration are in
`eval/results/coalescing-factorial-20260928/`. Its two motivating functions test
the generator and wiring; they are previously examined, header-assisted development
cases. Keep them separate from a fresh unmatched-function census and do not infer
population yield or selection-policy superiority from them.

## Key stress, full-listing metrics, and semantic panels

```bash
$PY -m eval.research_suite key-audit --bundle /path/to/frozen-roots \
  --repo /home/grant/decomp/sbk1 --budget 8 --output /home/grant/decomp/experiments/key-stress
$PY -m eval.research_suite reconstruct --target target.normalized.s --diff candidate.diff \
  --target-sha256 HISTORICAL_TARGET_SHA256 --output reconstructed.json
$PY -m eval.research_suite panels --input panel-input.json --output panel-result.json
$PY -m eval.research_suite proposals --input proposal-input.json --output proposal-result.json
```

Key stress compiles layout variants and supplied recorded proposals. Its checked denominator is **conclusive equal-key object comparisons**. Deliberate stress does not estimate a campaign failure rate and does not replace the existing randomized operational audit. Compiler/recipe/debug variants should be frozen as separate environments; equality is tested within each one.

Reconstruction accepts complete newline-terminated normalized listings/diffs and checks every context/deletion line, range, and count. Empty, truncated, incompatible, and newline-ambiguous diffs are refused. Without the historical target hash, output identity is explicitly `unverified`; matching visible context cannot establish omitted historical regions. Reconstruction recovers heuristic vectors, not object identity, and does not automatically repair historical cohorts or selection bias.

`panel-input.json` contains `function`, `target_assembly`, `candidates` (`id`, `source`, `assembly`), `base_cases`, `extra_cases`, and optional `max_steps`. Cases follow `solver.mips_differential.TestCase`, e.g. `{"name":"value_two", "seed":7, "player_writes":[[0,4,2]]}`. Only target-completed, ABI-valid cases are admitted. Output includes `retained_cases` for the next batch, target/interpreter/panel hashes, failed/inconclusive counts, and `base_pass_extra_checked` / `base_pass_extra_falsified`. Later candidates must replay the same retained list. Supplied source/assembly content hashes are checked when provided; a panel input alone does not establish compiler provenance or certify semantics.

`proposal-input.json` contains `function`, `source`, and optional `accesses`/`flows`. Example access: `{"base":"f:p", "offset":4, "width":4, "signed":false, "kind":"load", "evidence_id":"binary-lw@0x10"}`. Flows use `from`, `to`, `evidence_id`, with names scoped as `function:pointer`. Outputs remain retractable source hypotheses. The native `types` arm reads these constraints from each frozen task and compiles emitted candidates.

## Evidence and next experiment

See [the historical checked results](../results/research-suite-20260928/RESULTS.md) and [the baseline-review corrections](../../docs/research-suite-review-20260928.md). Unit verification establishes the corrected baseline wiring; the historical native smoke predates it. Finish the shared-engine/descriptor/type-comparator design, freeze the cohort and hypotheses, and run after the pilot. No cohort benchmark is launched by this review fix.
