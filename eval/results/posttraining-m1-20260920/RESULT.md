# Post-training M1 — result (2026-09-20)

**Outcome: a reproducible NEGATIVE / BLOCKED result, with the blocking prerequisite measured
rather than asserted.** The pipeline is built, wired, and verified end to end against the real
compiler and a real checkpoint. It could not be carried to a trained M1, because the supply of
verified repair examples that survives this project's own holdout rules is too small to train
on. That ceiling is measured below, in units the project already uses.

This file reports four things separately, because conflating them is how this project has
previously reported engineering progress as capability: pipeline engineering, data supply,
model capability, and remaining blockers.

---

## 1. The review's two priority defects: reproduced, fixed, verified

### 1.1 Incorrect refinement lineage — FIXED

`Factory.run_function` kept one prompt for every round (the asm/m2c leaf prompt from
`make_context`) while moving `parent` to whichever candidate scored best, and
`WorkspaceScorer.score` labelled every draw `relation="refine"`. Independent best-of-N samples
were therefore recorded as refinements of a candidate the model was never shown.

What changed, in `eval/trajectory_factory.py`:

- Round 0 is **independent draws with no candidate parent**. `Proposal.lineage` is `root`.
- Rounds after `repair_from_round` ask the project's **repair** prompt
  (`render_repair_prompt` → `solver.refine.DIFF_PROMPT` / `COMPILE_FAIL_PROMPT`), built from
  the parent's exact C and that parent's own compiler outcome, and the attempt is recorded with
  the parent's receipt id and the exact feedback text on the edge.
- The child's recorded `action` is what was requested: `independent`, `repair`, or the
  branch the parent's own outcome implies (`fix-diff` / `fix-compile`).
- A repair whose parent has no compiled candidate **stops with `no repair state`** rather than
  silently re-asking the leaf prompt.
- `repair_from_round` makes "no repair pass" structural rather than a consequence of `rounds=1`.

**Independent verification on production data**, not only in unit tests:

```
python -m eval.collect_repairs --verify --scratch-db <scratch.sqlite> --out x --plan
{"repair_attempts": 52, "edges": 52, "ok": true}
```

That audit asserts, per stored repair: a parent receipt exists; the parent's exact source
appears verbatim in the child's prompt; the parent's own diff (or stderr) appears as the
feedback; and an `attempt_edges` row exists. It is committed as `verify_lineage()`.

Two further lineage defects were found **only because the audit ran on real data**:

1. **`ServeGenerator` attached no parent to repair draws.** 8 repair calls had prompts that
   correctly quoted the parent's C and its 67.97 score and were stored with
   `parent_attempt_id` NULL and no edge. The factory now attaches the state itself and the
   generator is no longer trusted for it.
2. **A fixed seed made best-of-N into best-of-1.** Every call passed the same explicit `seed`,
   and the service treats an explicit seed as FIXED-SEED sampling. 68 "independent" draws were
   17 distinct generations repeated four times; all 12 repair draws collapsed onto one output
   per function, and for that seed the repair output was the candidate *byte-for-byte*. The
   first pilot's headline "0/12 repairs improved" was a seeding artifact. Seeds are now derived
   per `(function, role, draw index)`, which is reproducible and independent at once.

### 1.2 Missing generation receipts — FIXED

`OllamaGenerator.sample` returned `list[str]` and `WorkspaceScorer.score` passed no prompt,
model, raw response, sampling or timing to `record_attempt`, so the write path supported all of
it and no caller used it.

What changed: `solver/llm.GenerationReceipt` is now a **data structure**, not a convention, with
one terminal `status` per call (`ok`, `refusal`, `no-extract`, `empty`, `error`, `timeout`).
`record_attempt` folds it into the attempt row. Every field is present in every stored row.

Measured on the collection pilot — `attempts` rows, 172 of 172:

| field | rows present |
|---|---|
| `prompt_context` | 172 / 172 |
| `raw_response` | 172 / 172 |
| `model` | 172 / 172 |
| `sampling.generation` (digest, sampling, tokens, wall_ms, extract status, stop reason) | 172 / 172 |

Also fixed and counted: refusals and errors **consume the call budget**; the wall-clock budget
is enforced **inside** each function, not only between them; checkpoints fire after every
scored proposal so interruption loses at most one candidate; and `improving_children` is
counted separately from `improving_rounds`.

### 1.3 Tests actually run

```
python -m pytest -q tests/test_factory_lineage_receipts.py tests/test_trajectory_factory.py \
  tests/test_repair_training_path.py tests/test_posttraining_experiment.py \
  tests/test_resource_limits.py tests/test_repair_dataset.py
```

`56 passed` in the four new/extended experiment files (`test_factory_lineage_receipts`,
`test_repair_training_path`, `test_posttraining_experiment`, `test_resource_limits`), plus the
updated `test_trajectory_factory.py` and the dataset exporter's `37 passed`.

Every lineage and receipt test is written to **fail on the original behaviour**, and the ones
for the production bugs say which measurement they pin. The pre-existing repository suite
fails only on three environmental tests unrelated to this work: `test_project64_trace.py`
requires absolute Windows paths (run under WSL), `test_corpus_manifests.py` needs a dumped ROM
that is not present, and `test_benchmark_campaign_parallel.py` needs `numpy`, which is not in
this venv.

---

## 2. Data supply: the blocking prerequisite, measured

### 2.1 What the existing knowledge base can supply

Audited read-only (`eval/repair_dataset.py`, `eval/results/posttraining-m1-20260920/dataset/`):

| stage | count |
|---|---|
| `attempt_edges` rows | 8,132 |
| both endpoints compiled | 5,974 |
| **score-improving** (`child > parent + 0.5`) | **265** |
| … on functions with no exact attempt | 242 |
| … **not in a sealed eval set** | 27 |
| … not in `cluster`/`panel` | 24 |
| … not a library TU, not already finished | **1** |

Improving functions by fate: **14 sealed, 9 already finished, 3 eligible.**

That last number is the whole finding. The project's holdout discipline — which this experiment
must preserve, and did — consumes 89% of the improvement supply, because the sealed sets were
deliberately built from the functions the pipeline had already worked on. The audit counts
every exclusion rather than summarising them, and it also found that
`completion-campaign-dev-seeds-v1.json` uses a bare `{"<function>": <attempt_id>}` map that no
key-based scan reaches; those 22 edges are now excluded and counted separately.

### 2.2 What fresh generation supplies

A bounded pilot through the fixed factory, against the local 7B checkpoint, real oracle, full
receipts: **172 model calls across 29 functions in 28.5 minutes** (`400` calls / `5100` s
budget, stopped on items exhausted).

| measurement | value |
|---|---|
| model calls | 172 |
| compiled | 80 (**46.5%**) |
| byte-exact | **0** |
| improving children | 4 (**2.33 per 100 calls**) |
| repair calls | 56 |
| **repair calls that improved on their parent** | **0** |
| repair calls that scored *worse* | 16 |
| repair calls identical in score | 25 |
| repair compile rate | 48/52 = **92.3%** |
| independent compile rate | 32/108 = 29.6% |

The shape of that is worth stating plainly, because it is a real finding about the model and
not about the harness: **the repair prompt makes the model produce code that compiles far more
often (92% vs 30%) and improves far less often (0 vs 4).** It behaves like an editor that
preserves the candidate's shape — mostly reproducing it, sometimes breaking it — rather than one
that searches for a better shape. That matches the earlier measured result in `ROADMAP.md`
("sequential refinement FALSIFIED: 0 gained by iterating") on a different model and a different
prompt, and it is the same conclusion arrived at independently.

`repair_prompts` are not the problem: the stored prompts quote the parent's C verbatim and its
own diff and score, verified per row.

### 2.3 Why that is not enough to train on

The fresh export (`eval/export_fresh_repairs.py`) pairs each function's best compiled candidate
against its other compiled candidates, labelling the relation honestly as `observed-same-run`
(the model did not produce the child *from* the parent unless it was a repair draw, which is a
separate and smaller tier):

```
{"candidates": 160, "functions": 27, "records": 37,
 "records_by_provenance": {"observed-same-run": 33, "observed-repair": 4},
 "distinct_functions": 9, "largest_function_share": 0.1622}
```

Pooled with the knowledge base's train split this is **97 examples**. After the sequence-length
filter the trainer can actually fit on a shared 16 GB card, **9 examples survive**.

The handoff's own sizing target was *300 distinct verified improvements across ≥30 training
functions and ≥10 TUs*. The measured supply is **37**, and the honest reading is that the
target was set without knowing that the holdout sets had already consumed the supply.

---

## 3. Model capability: what was and was not measured

### 3.1 M0 baseline on the frozen held-out panel

The panel was preregistered before any result existed: **60 held-out functions from
`eval/sets/*.json` `heldout` keys**, every one unsolved and never attempted in the knowledge
base, stratified by size, frozen with `panel_sha256`
`f3d23a4657134d6e6147ecb019ba944e50d937af302a29ec6df02407ace3fa6c`. The runner re-hashes the
panel and **refuses to run if it does not match**.

| | |
|---|---|
| panel functions | **60** (31 small, 17 medium, 9 large, 3 huge) |
| eligible (bootstrappable) | 59 — `gspF3DLX_fifoTextStart` is SDK macro assembly |
| model calls | 118 (2 independent draws × 59) |
| **new byte-exact functions** | **1** (`updateControllerPakFileDeleteFreeSpaceInfo`, 48 bytes, solved on draw 1) |
| best-of-2 exactness | 1/59 = **1.69%** |
| pass@1 | 1/118 = **0.85%** |
| compiled | 17 / 118 = **14.4%** |
| refusals | **0** |
| mean best score | 13.5 |
| wall clock | 636.5 s ≈ **10.6 min** for 118 draws (median 4.6 s/draw) |
| resource cap | **7.0 GB** of a 15.9 GB card, 2 of 4 threads, nice 15 |

The exact match is a **new** solve, not pre-existing state: the panel was built from functions
with no exact attempt and no attempt at all.

The near-miss distribution is the interesting half, because it says where the model is and is
not usable:

| function | best score | size |
|---|---|---|
| `handleRaceTypeSelectFlow` | 99.52 | 88 |
| `clearCurrentGameTaskCallback` | 99.25 | 84 |
| `makeFixedRotationX` | 96.15 | 108 |
| `drawCharacterSelectCourseTitleCursor` | 81.04 | 100 |
| `startMainMenuModePreviewRaceFlow` | 65.42 | 52 |
| `setMainMenuSceneModelRotation` | 61.52 | 96 |
| `loadMainMenuSceneModelAssets` | 58.18 | 48 |

Three functions sit within one instruction of exact inside 2 draws, which is consistent with
`TRAINING.md`'s Tier-1 claim (best-of-N plus the verifier is training-equivalent capability for
free) and with the published ~1.2% single-shot baseline this panel is measured against: 0.85%
pass@1 here is the same order, on a target chosen to be obscure.

Three limitations the run itself exposes, all reported rather than smoothed:

- **9 of 59 prompts exceed the 12,288-token context** (18,479 to 68,317 tokens) — every one a
  `large` or `huge` function. Those are structural errors, not model failures, and they are the
  direct evidence for blocker 2 below.
- **30 functions ran, compiled nothing and raised nothing**: the model answered in prose or
  with an unterminated block. That is an extraction-shaped failure with its own cure
  (`solver.llm.extract_c` was applied; the answers simply were not C).
- Raising the context to 16,384 to reach the larger functions **destabilised the CUDA context**
  at function ~40 and invalidated 14 later functions. The 12,288 run is the one reported, and
  is the one with no such cascade.

The full machine-readable result is `evaluation/M0/evaluation.json` with per-draw receipts in
`evaluation/M0/draws.jsonl`.

### 3.2 M1: not trained, and why

No M1 was produced. Training was attempted four times and each failure is environmental and
fixable, but the binding constraint is upstream of them:

- 97 pooled examples, one epoch, plain AdamW, LoRA r=16/α=32, batch 1 × grad-accum 4.
- 4-bit QLoRA base (`--load-in-4bit`) so a 7B checkpoint fits a shared card.
- The sequence-length filter is what kills it: `--max-seq-len 6144` keeps **19 of 97**
  examples; `--max-seq-len 4096 --keep-longest 3600` keeps **9**. The rest are 6–11 k tokens,
  because a repair prompt contains the target assembly, the candidate C and the diff.
- At 9 examples and a 9.5 GB cap the backward pass still needed 1.39 GiB for the vocabulary
  logits (152,064 × sequence), which is the structural cost of this model at this context.

A model trained on 9 examples is not a treatment arm, and reporting a comparison against it
would be reporting noise. **No M1→capability claim is made.**

### 3.3 The two-arm comparison is wired and unit-tested, not run

`eval/run_posttraining_experiment.py` serves both arms **from one loaded model object** with the
adapter toggled per draw, interleaved per function, with the same prompt, sampler, oracle and
budget, and writes the adapter state into every row. It is exercised by
`tests/test_posttraining_experiment.py` (adapter off for every M0 draw and on for every M1 draw,
identical prompts across arms, preregistered budget equals calls made, a tampered panel is
refused, a missing adapter cannot serve M1, and a tie reports inconclusive rather than success).
It has never been run with a real adapter, because there is not one.

---

## 4. Machine-resource changes (requested mid-run)

The first runs took the whole machine. That is fixed and pinned:

- `eval/resource_limits.py`: GPU memory fraction/GB, CPU threads, niceness, and the vLLM knobs,
  all environment-overridable, with `apply()` called before the first CUDA allocation and
  `describe()` written into every run report so a resource limit cannot be mistaken for a
  capability limit.
- Defaults leave the machine usable: **50% of the card, 2 of 4 threads, nice 15**.
- `.cache/recon/serve_lowfootprint.sh`: inference at **42% reservation and 2 sequences**
  instead of 72% and 6.
- `.cache/recon/eval_m0.sh`: evaluation inside a **7.0 GB** cap — measured, and it completed
  with zero errors.
- `.cache/recon/yield.sh {status|pause|resume|stop}`: `pause` is honoured between rounds and
  between training steps, and stopping is safe because every scored attempt is already stored
  and a resumed run skips finished functions.

---

## 5. Reproduce

```bash
# 1. the lineage/receipt fixes and their tests
python -m pytest -q tests/test_factory_lineage_receipts.py tests/test_trajectory_factory.py \
  tests/test_repair_training_path.py tests/test_posttraining_experiment.py \
  tests/test_resource_limits.py

# 2. audit the existing knowledge base's supply (read-only)
python -m eval.repair_dataset --kb ~/decomp/kb-sbk1.sqlite \
  --out eval/results/posttraining-m1-20260920/dataset \
  --workspace-root ~/decomp/sbk1/nonmatchings --sets-dir eval/sets --seed 20260920

# 3. the fresh collection pilot (needs the inference server)
bash .cache/recon/serve_lowfootprint.sh &          # GPU 42%, 2 seqs, nice 15
bash .cache/recon/collect_pilot.sh

# 4. verify the repair lineage actually recorded, then export
python -m eval.collect_repairs --verify \
  --scratch-db ~/decomp/posttraining-m1-20260920/collect.sqlite --out x --plan
python -m eval.export_fresh_repairs \
  --db ~/decomp/posttraining-m1-20260920/collect.sqlite \
  --out eval/results/posttraining-m1-20260920/dataset-fresh

# 5. M0 on the frozen panel, inside a 7 GB cap
bash .cache/recon/eval_m0.sh

# 6. stop everything / put the machine back
bash .cache/recon/yield.sh stop
```

---

## 6. What would unblock this

In order of leverage, each stated as a prerequisite rather than a plan:

1. **A repair signal worth training on.** 0 improving repairs in 56 attempts at one
   temperature on one model is the number to attack. The prompt is verified correct, so the
   question is now the model and the sampler: a stronger generator, a different temperature
   schedule, or a decomposition small enough that a single edit is the whole task.
2. **Training data that fits the context.** Either shorter inputs (repair a *statement*, not a
   function) or a longer-context budget on a card that is not shared. As it stands the useful
   examples are exactly the ones that do not fit.
3. **A supply decision the operator owns.** The sealed sets hold 89% of the observed
   improvement supply. Relaxing that is a capability claim the operator has to authorise
   knowingly; this experiment did not do it, and reports the cost of not doing it.
4. **DKR as the same-compiler transfer test.** Blocked as before on a per-function scorer for
   a repository with no `build.sh` — unchanged by this work.
