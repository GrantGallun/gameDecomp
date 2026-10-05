# Refinement dataset from the campaign, September 27

Step 2 of the local-training route: turn the campaign's logged search into supervised data, checked
against first principles before trusting any default. Read-only against the running campaign
(`mode=ro`); no solver, KB, campaign or build-path change.

## Result

`python3 -m eval.repair_dataset --kb ~/decomp/runs/resume-pipeline-20260908/campaign.sqlite
--out ~/decomp/experiments/refinement-data-20260927/v4 --build-tree ~/decomp/sbk1`

| | old defaults (`baseline-audit.json`) | new (`audit.json`) |
|---|---:|---:|
| records | 3,824 | **9,178** |
| functions / TUs | 515 / 68 | **895** / 69 |
| records with an exact child | — | **306** (train 197, dev 8, test 101) |
| train split | 1,733 | **4,492** over 503 functions |
| largest single-function share | — | 1.7% |
| clean (no reference assistance, see below) | unchecked | **206** (train 32) |

Manifest digest `5bfb14dd…1ce583`, JSONL sha256 `8341596…8d5b` (345 MB, WSL side only). Every record
has target assembly and a compiler recipe. 8,069 records come from registered deterministic
mechanisms, 643 from gpt-oss:20b, 466 are reconstructed pairs.

On 09-20 the same route was blocked at 1 surviving edge of 265 (`posttraining-m1-20260920`). Two
things changed: the campaign logged 14,147 improving compiled edges since, and the sealed sets now
hold 2,712 of them (19%) instead of 89% of supply.

## What first principles changed in the exporter

Each default was tested against what it is for, and three failed.

1. **The 0.5 improvement margin removed 55% of real improvements.** It exists to absorb scorer noise.
   Parent and child are scored in the same session (14,145 of 14,147 improving edges were created under
   an hour apart), so a strict increase is already verified. The margin dropped 7,784 edges, **91 with an
   exact child**: fixing one instruction in a 200-instruction function moves the score by 0.5. Observed
   edges now use `EDGE_IMPROVEMENT_EPSILON = 0`; reconstructed pairs keep 0.5, where it guards pairing
   of unrelated attempts. `--edge-epsilon` restores the old cut.
2. **Finished functions were excluded as "not repair supply".** True for a pool of states to repair,
   false for training: the route that reached a match is the best-labelled data there is. It is now OFF
   by default (`--exclude-finished` restores it) and records carry `meta.function_finished` (1,168).
3. **The deterministic registry predated the campaign.** 93% of improving edges were `unverified`. Added
   after reading each generator: `repair-d1..6` and `repair-plateau-v1` (`solver/repair.py`),
   `regalloc-search-probe`, the `agentrepair-*` zero-model strategies, `modelrepair-normalize:*` and
   `operand-repair:*`.

Also fixed: `write_dataset` wrote JSONL with `write_text`, which on Windows turns `\n` into `\r\n`
after the digest was computed, so a Windows-built manifest described a file that did not exist.

Two filters were considered and **not** added, because the data showed nothing for them to do: intake
and context-projection edges have 0 improving compiled edges, and 0 improving children are
reference-recovered answers. A filter that never fires is dead weight.

## Contamination: the corpus is header-assisted

Records carry `meta.assistance` and `meta.clean`, measured by identifier **use**, not by `#include`.
The first version checked includes and was wrong: `common.h`, which every draft includes, pulls in
`game/math/geometry.h`, so 313 of 327 records with no `game/` include still used a game-only
identifier. Also found by checking a zero: the first use-scan stripped preprocessor lines with `re.S`,
so `#.*$` swallowed each file after its first include and reported 0 uses.

| tag (of 9,178 records) | count | means |
|---|---:|---|
| `child_game_header_prototypes` | 8,889 (97%) | calls a function only `include/game/**` declares (own name excluded) |
| `child_game_header_types` | 5,931 (65%) | uses a type only `include/game/**` defines |
| `child_reference_types` | 1,410 (15%) | uses a type only the target's `src/*.c` defines |
| `parent_reference_seed_lineage` | 23 | a reference-recovered/historical seed is an ancestor |

The prototype tag is an upper bound. Callee names come from the symbol map, which every attempt uses.
Their parameter and return types come from the header whenever it is in effect.

**Consequence for training.** Records carry no declarations (`input.declarations` is null, and has
been since 09-20). A model trained on them must memorise the reconstructed headers' API to reproduce
the children. It would do well on SBK1, where those headers exist, and nothing it learned that way
transfers to a game without them. Numbers from such a model belong in the header-assisted tier, never
SOLVED.

## What the logged search says about the ±0s

All from `campaign.sqlite`, scripts and outputs in `analysis/`.

**Flat steps are on most paths to a match; down steps almost never are.**

| one-step outcome | edges | has an exact descendant |
|---|---:|---:|
| up | 13,778 | 1.62% |
| flat | 106,975 | **2.91%** |
| down | 93,438 | **0.01%** (10) |
| did not compile | 8,202 | 0.12% |

Of 1,151 exact matches with lineage, 641 have a flat step on their best-parent path and **13** have a
score drop. Caveat: the beam rarely expands a down child, so this says the current search does not use
drops, not that drops cannot help. `solver/repair.py` and TRAINING.md both say a useful intermediate may
score worse; here that happened in 13 of 1,151 matches.

**Where the compiles go.** 169,227 deterministic one-step outcomes: 54% down, 34% flat, 7.6% up, 4% did
not compile. 88,439 compiles (52%) went to parents where no child of that family improved. Where one did,
the first improvement arrived at median rank 3 of 16.

**Action kind alone is not a usable prior.** A per-kind lookup built on train TUs and scored on test TUs
(`analysis/action_prior.out`) saves 18.5% of compiles. But it loses 19 of 244 exact-reaching steps,
mostly in kinds that **never** helped on train. Rare kinds are where finishing moves live. The signal
has to come from state and action together, which is what a trained model adds over a table. Use a
prior to **order** the search, never to skip moves.

## Next decisions, in order

1. **Put declarations in the input.** For each record, include the declarations (header text) of the
   game/ types and prototypes that parent or child uses. The parent was compiled with those headers in
   scope, so this is its compile context, not an invention; it must still be tagged as taken from the
   current tree. The model then learns to *use given declarations* rather than memorise them. A clean
   run can later supply binary-derived declarations (`solver/binary_type_draft`) in the same slot.
2. **Choose the label policy.** `eval/train_source_repair.py` trains only on exact children, a rule from
   the synthetic curriculum where every task has one. The game data has 306 exact-child records and 8,872
   verified improvements. Improvements match how the model is used (one refinement step per call); exact
   children are the strongest signal. Recommendation: train on improvements, weighted with
   `eval.repair_states.balanced_weights`, and report exact-child performance separately.
3. **Write the schema adapter.** `eval.repair_prompts.load_task_examples` reads the synthetic task
   schema. The adapter is small; its test should assert it fires on a real v4 record.
4. **Measure compiles to a match, not loss.** On test-split functions, compare the trained adapter,
   untuned gpt-oss:20b and the deterministic search under equal compile budgets. The sealed sets remain
   the only basis for reported numbers.
5. **Separately: a move-ordering model.** The 169k one-step outcomes, including flat and down, are
   labelled data for ranking deterministic rewrites by (state, action). That targets the ±0 compiles
   directly and needs no C generation. Its offline metric is computable now from logged siblings: the
   rank of the first improving child under the learned order vs the logged order.

## Receipts

- `audit.json`: full v4 filter funnel, policy, distributions, assistance counts.
  `baseline-audit.json`: the same export under the old defaults.
- `analysis/*.py` + `*.out`: `edge_audit` (deltas, finished, creation gaps), `fanout` (outcomes,
  first-improvement rank), `paths_to_exact` (step signs, descendant rates), `action_prior` (TU-split
  lookup-table test), `relations`, `header_use` (include vs use, the clean-tag check).
- Tests: `tests/test_repair_dataset.py`, 47 passing. They cover the strict edge threshold,
  finished-function default and opt-in, registry exactness, unknown filter names refused, the Windows
  digest fix, reference types, seed lineage through a grandparent, game/ use via transitive include,
  self-definition not counted, and an unchecked build tree never reading as clean.
- The dataset: `~/decomp/experiments/refinement-data-20260927/v4/` (WSL; not copied, 345 MB).

## Addendum: what the model has actually contributed (before deciding what to train)

Question from the operator: we do not yet know the best applications of the LLM, so should we
train at all? The campaign's own records answer part of it. Scripts: `analysis/llm_census.py`,
`analysis/model_enabler.py`, `analysis/model_strategies.py`, `analysis/model_wins.py`.

**First, a measurement error caught.** 63,421 attempts carry `model='gpt-oss:20b'`, but 55,425 of
them are `agentrepair-*-reverify` rows. Those recompile an existing source and store no model output;
they are labelled with the run's configured model. The census's first version counted them and
claimed 344 functions reachable only through a model step. Every such "model step" was a flat
reverify. Model **authorship** = a stored raw response: 7,996 attempts, all `modelrepair-d1..3`
plus one handoff.

| | model-free | model-authored |
|---|---:|---:|
| attempts | 201,345 | 7,996 (4%) |
| compiled | 84.2% | 77.8% |
| improving edges per attempt | 6.6% | **11.9%** |
| exact per 1k attempts | 6.7 | **12.4** |

Of 1,003 exact functions:

- **75 were reached only by the model.** Every one had model-free attempts too (median 13), which failed.
- **About 97 more depend on a model edit upstream of a deterministic finish** (113 exacts, 100 functions;
  nearest model step a median of 5 steps before the match). That edit improved the score in 62 cases
  and was flat in 40.

So roughly 17% of exact functions trace to 4% of attempts.

**Every winning model edit is small.** Median 2 changed lines, p90 6, never more than 20. The harness
caps model patches at four bounded edits. The model has only ever been used as a local editor, so the
data says nothing about restructuring, compiler investigation or execution cases.

**Many of its wins are coverage gaps in existing deterministic machinery.** From
`analysis/model_wins.out`:

- Operand commutation (3 of 14 sampled), which `regalloc-search-probe` has as `commutative`.
- Missing prototypes and function-pointer casts on 100-score, not-certified candidates, which
  `solver/project_headers.py` is meant to cover.
- Pointer arithmetic to array indexing when the stride equals the element size.
- Some genuine judgment: the element stride of an untyped table, dropping a temporary.

Unsolved: 1,110 functions. Model-free search improved 673, the model 286 (98 of them only the model),
and **339 were never improved by anything**.

**Implication.** Training now would mostly teach the model to keep patching gaps in the deterministic
catalog. The better use of those wins is to turn each into a positive test ("why did deterministic
search decline here?") and fix or add the rule: free forever, and the silent-decline rule in
CLAUDE.md. What is left after that is the model's genuine niche. The 339 never-moved functions are
where an untried application (whole-function reconstruction without the edit cap, compiler-phase
investigation) has to prove itself.

## Corrections (same day; both found by re-checking a surprising number)

1. **"Flat steps are on most paths to a match" is withdrawn.** `paths_to_exact.py` and the first
   `flat_split.py` compared instruction diffs as raw text, and every diff begins with a header naming
   the dump file and a timestamp, so no two were ever equal. With headers stripped
   (`analysis/flat_split.out`), 96,120 of 106,975 flat steps are NO-OPS: identical object, mostly
   reverify rows and respellings. They have exact descendants 3.2% of the time because the search
   simply continues past an equivalent state. The 10,855 genuinely NEUTRAL steps (object changed,
   score equal) reach a match **0.06%** of the time, below improving steps (1.6%). The ±0 churn is
   waste after all, apart from what re-verification is for. The v1 figures stay in
   `flat_split.v1-header-bug.out`.
2. **Draft conversion rates were inflated.** `draft_basin.py` and `draft_basin_size.py` counted
   roots that were already exact on arrival (384 functions) as "≥95 roots that converted".
   Corrected (`analysis/draft_basin_corrected.out`), non-exact roots convert as follows:

   | size band | draft ≥95 | 85–95 | <85 |
   |---|---:|---:|---:|
   | ≤30 instructions | 52% | 37% | 24% |
   | 31–80 | 14% | 12% | 6% |
   | 81–200 | 20% | 3% | 0% |
   | >200 | 0% | 0% | 0% |

   Size matters more than draft score.
3. **Residual kind does inform triage at ≥95** (`analysis/draft_shape.out`):

   | residual kind at ≥95 | converts |
   |---|---:|
   | register/order only | 54% |
   | mixed, right shape | 38% |
   | wrong shape | 23% |
   | operands only | 6% |

   Below 95, 443 of 446 drafts are wrong-shaped, so kind cannot discriminate there.

The redraft/branch-point pilot these led to is in `eval/results/redraft-pilot-20260927/`.

## Object truth vs the similarity score (`analysis/truth_vs_score.out`)

`solver/invariants.py` reads per-level distances from a diff alone: calls, control flow, frame,
expressions, operands, registers. It is exact, because regions the diff omits are identical on both
sides (`tests/test_invariants.py`). Over the campaign's edges:

| score change | object-truth change | edges | reached a match |
|---|---|---:|---:|
| up | up | 11,219 | 5.21% |
| up | down | 2,058 | 0.34% |
| down | up | 3,182 | 0.13% |
| down | down | 87,786 | 0.01% |

A score gain that breaks a higher level is mostly false progress: 15× less likely to lead to a
match than one that also improves object truth. That supports rejecting such children. Repairs
move down the expert order: frame → expressions (612 edges), control flow → expressions or frame,
calls → frame, and registers → exact.

What the logs cannot test is accepting a child whose score FELL while its truth improved. The
campaign's score-driven search rarely expanded such children, so their low rate is what that
selection bias would produce either way. The gated pilot arm tests it directly.
