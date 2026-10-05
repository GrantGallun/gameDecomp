# AUDIT — source-repair dataset (sbk1, seed 20260920)

Exported from `/home/grant/decomp/kb-sbk1.sqlite` (opened `mode=ro`, `PRAGMA query_only=1`;
the KB was not written to). Artifacts in this directory:

| file | sha256 | notes |
|---|---|---|
| `repair_dataset.jsonl` | `d40be8458a1a3eeb938335fd5432bcdaf5881efba25a724d8c8a3a4c07e99bec` | 201 records, 4,313,876 bytes |
| `manifest.json` | digest `b53320a00fe234bb683a4f7db3f3952df3c74a63bfe064b225ae6ab6ccff8311` | embeds the full audit + split digest; stable across re-runs (the digest excludes its own `created_at`) |
| `audit.json` | — | the same audit report, standalone |
| split digest | `eae609ce73678ebede04b94067a8c5574c14929f9a71c163983f0552b184209d` | over the per-function (function → split) assignment |

Re-export (exactly this, from the repo root in WSL; ~5 s, 800 MB peak RSS):

```bash
/home/grant/decomp/sbk1/.venv/bin/python -m eval.repair_dataset \
  --kb /home/grant/decomp/kb-sbk1.sqlite \
  --out eval/results/posttraining-m1-20260920/dataset \
  --audit eval/results/posttraining-m1-20260920/dataset/audit.json \
  --seed 20260920 \
  --workspace-root /home/grant/decomp/sbk1/nonmatchings \
  --sets-dir eval/sets \
  --reconstructed-per-function 20
```

## 1. What is in it

| provenance | train | dev | test | total |
|---|---|---|---|---|
| `observed-repair` | 0 | 0 | 1 | **1** |
| `deterministic-repair` | 0 | 0 | 0 | **0** |
| `reconstructed-lineage` | 60 | 40 | 100 | **200** |
| **total** | **60** | **40** | **101** | **201** |

* 10 distinct functions, 8 distinct translation units.
* `input.target_asm` present on **201 / 201** records (0 marked unavailable). The workspace has
  `nonmatchings/<func>/target.s` for **2067 / 2067** functions, so assembly availability is not a
  limiting factor here. `input.flags` (from `nonmatchings/<func>/.compiler-*.json`) present on
  201 / 201.
* `input.declarations` is **null on every record** (0 available) — see limitation 3.
* `meta.mechanism_confidence`: `unverified` 201, `model-records` 0, `deterministic-registry` 0.
  153 distinct `attempts.strategy` strings appear across 200 reconstructed records.
* Largest per-function share: **10.45 %** (`drawRaceSetupSavePlayerPanels`, 21 records).
* The one `observed-repair` record: `sbk1:drawRaceSetupSavePlayerPanels:30322->30340`,
  85.766 → 86.988, strategy `repair-d1`, TU `build/src/menu/race_setup/race_setup_ui.o`, test split.

## 2. Filter funnel (every exclusion counted)

The KB has 8,132 `attempt_edges` rows. 2,158 have an endpoint that did not compile; of the 5,974
that compiled at both ends, **265 improve** the score by more than 0.5 across 26 functions.

Observed tier, per filter (independent counts, and the additive funnel position):

| filter | improving edges removed | remaining |
|---|---|---|
| (start: both compiled, `child.score > parent.score + 0.5`) | — | 265 |
| `function-has-exact-attempt` (function already finished) | 23 | 242 |
| `sealed-dev-heldout` | 215 | 27 |
| `sealed-cluster-panel` | 3 | 24 |
| `sealed-unrecognized-shape` (`completion-campaign-dev-seeds-v1.json`) | 22 | 2 |
| `library-tu` (`EXCLUDE_TU`) | 1 | 1 |
| `parent-child-different-function` / `empty-source-code` / `duplicate-pair` | 0 | **1** |

Reconstructed tier: 36,561 compiled attempts have no edge row and no parent pointer; 34,300 of them
have a real, strictly-worse compiled predecessor in the same function (2,261 have none) →
21,008 removed by `sealed-dev-heldout`, 5,006 by `function-has-exact-attempt`, 3,764 by
`sealed-cluster-panel`, 1,402 by `library-tu`, 4 by `sealed-unrecognized-shape` → 3,116 → 246
duplicate `(parent_sha256, child_sha256)` pairs collapsed → 2,870 available → the documented
10 %-per-function budget keeps 200 (`reconstructed_dropped_by_budget_total` = 2,670).

`--reconstructed-per-function 0` exports all 2,870 (max function share would be 24 %). The budget
is recorded in `audit.json` with the per-function supply it truncates, so nothing is hidden.

Other audited counts: `attempts.source_sha256` is NULL on 22,310 rows; 30,494 of 30,494 non-NULL
values equal `sha256(source_code)`, so a missing hash is computed from the stored source and
`meta.*_sha256_source` says which. Sealed sets (strict scan of all `eval/sets/*.json`):
dev 450, heldout 134, cluster 42, panel 8, **union 594**; the dev+heldout-only rule covers 580,
and 14 names appear only under `cluster`/`panel` inside their own file.

## 3. The three biggest supply limitations

**(1) The sealed evaluation sets cover almost the entire observed supply — 237 of 265 improving
edges (89 %), leaving one record.** `updateRacePlayerLeanAngle` alone contributes 129 improving
edges and is in `hard_v1.json:dev`, `logic_first_connected_dev_v1/v2:cluster` and
`sbk1_v1/v2/v3:dev`. Of the 31 improving edges whose child has real model records, **26 are sealed
(24 `dev`/`heldout`, 1 `cluster`/`panel`, 1 the unwrapped campaign-seed file) and 5 are on finished
functions**; of the 202 edges from the model-free deterministic registry
(`typed-semantic-gradient-beam` 128 + `typed-semantic-gradient` 1, `alloc-order` 37,
`m2c-semantic-seed` 24, `do-restore:*` 3, deterministic statement-order/semantic-principle 9),
**none survives**. Consequence: the real
export contains **zero model-authored records and zero `deterministic-repair` records**; the two
non-reconstructed tiers the schema defines are implemented and tested, but the KB's supply for
them is sealed. The remaining 22 edges belong to `probeControllerPak`, which is named only in
`completion-campaign-dev-seeds-v1.json` (a `{"<function>": <attempt_id>}` map, a shape the four
sealed keys do not reach); excluding it is counted separately (`sealed-unrecognized-shape`).

**(2) Only 10 functions / 8 TUs are eligible at all, so 200 of 201 records are reconstructed pairs
of limited quality.** The milestone's provisional target was 30 training functions and 10 TUs; the
sealed + finished + library filters leave 10 functions in 8 TUs, and every one of them is reached
through the reconstructed tier. Those pairs are real rows with real compiler outcomes, but the
*relation* is inferred, and on inspection it is often not a refinement step: score delta median
1.17 but **21 / 200 exceed 25** (the "parent" is a different, much worse attempt), 61 / 200 have
parent and child in the same `created_at` second (the column has 1-second resolution, so temporal
order carries no information there), the child carries a `run_id` in only 1 / 200 cases, and no
target is byte-exact. The TU-level hash split over 8 TUs cannot hit 70/15/15: it yields
train 3 TUs / 60 records, dev 2 / 40, test 3 / 101.

**(3) No declaration context exists anywhere in the KB, and no mechanism is verifiable for the
records that survive.** `input.declarations` is null on all 201 records because the KB stores no
per-attempt declaration context: `attempts.prompt_context` exists for only 31 of 265 improving
children (agent/JSON-action prompts, not a declarations block), `model_proposals.kind='declarations'`
rows are proposed *edits*, and `<func>.frontend.json` holds a clang syntax-check recipe. Any
declaration list would be fabricated, so it is left null with a stated reason. Relatedly, the one
observed record's mechanism (`repair-d1`) is not on the model-free registry, so it is labelled
`observed-repair` with `mechanism_confidence: "unverified"` rather than guessed as deterministic
(32 of 265 improving edges fall in that bucket).

## 4. Verification actually performed

* `tests/test_repair_dataset.py` — **37 tests pass** (fixture SQLite KBs in `tmp_path`, no GPU,
  no network, no ROM). Each of the 11 filters has a test that it **fires** on its motivating case
  *and* a mutation test (`test_every_filter_is_load_bearing`) that disabling exactly that filter
  changes the exported record set.
* Artifact self-check against the written JSONL (21 assertions, 0 problems): unique ids, unique
  `(parent_sha256, child_sha256)`, JSONL sha matches the manifest, exactly the three tier names,
  function-disjoint and TU-coherent splits, manifest lists every function, and re-checking against
  the KB that no record's function is sealed / library / already exact.
* Full repository suite: **3433 passed, 3 failed, 2 skipped**. The three failures are unrelated to
  this work and environmental (`numpy` not installed in the venv for
  `tests/test_benchmark_campaign_parallel.py`; `tests/test_project64_trace.py` requires absolute
  Windows paths and is run under WSL). Neither test imports the new module.

## 5. Stated limits on these numbers

* `attempts.strategy` is free-form; the model-free registry lists seven strategy families whose
  live modules were read and found to contain no model transport. 32 of 265 improving edges have
  no model records *and* no registry entry, and are labelled `unverified`, not deterministic.
* A reconstructed pair's parent is chosen by rule
  (`best-strictly-worse-compiled-predecessor-same-function`); "this was the state being repaired"
  is not verifiable from the KB. Every such record says so in `meta.lineage_note`.
* The 11 improving edges whose child *is* byte-exact are removed by the finished-function filter
  (the whole function is finished); `kb_edges.improving_edges_with_exact_child` records them.
* `eval/trajectory_factory.py` was modified by another agent during this session. When this task
  started, `sealed_functions` (then at lines 151-165) read only `dev` and `heldout`; it now reads
  all four keys. This exporter never imported it — it scans `eval/sets/*.json` itself — and the
  two agree today (594 names). In this snapshot reading only `dev`+`heldout` loses **0** names,
  because the 14 cluster/panel-only names are covered by other files' `dev`/`heldout` lists; that
  is a property of the current snapshot, not of the rule.
* The KB's `attempts.created_at` is a whole-second integer, and `attempts.iteration` is 0 on 200
  of the 201 records (1 on the observed record), so no finer ordering signal exists for the
  reconstruction rule.
