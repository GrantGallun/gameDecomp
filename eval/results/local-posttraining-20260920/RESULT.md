# Compiler-verified post-training — acceptance receipt (2026-09-20)

> ## ⚠ THE HEADLINE IS SUPERSEDED — the panel is mechanically invertible
>
> **Added 2026-09-20, after this receipt was written.** The `4 → 19` result below is **not a
> capability measurement** and must not be cited as one. A deterministic pass with **zero model
> calls** solves **27 of the 27** frozen test tasks — a strict superset of the adapter's 19 — by
> inverting the curriculum's own registered mutations (`eval/deterministic_repair.py`). Across the
> whole corpus it solves 52 of 56.
>
> | arm | certified matches | model calls |
> |---|---|---|
> | baseline model | 4 / 27 | 54 |
> | trained adapter | 19 / 27 | 54 |
> | **deterministic inverse + enumeration** | **27 / 27** | **0** |
>
> Every adapter success is inside the free pass's reach; the model solves nothing the pass cannot.
> The held-out split happens to contain **no instance** of the one mutation that resists inversion
> (`drop-switch-default`, 4 tasks, all in train), so the frozen panel is entirely solvable by fixed
> inversion. What `4 → 19` measures is the **invertibility of the generated curriculum**, not repair.
>
> Sections 3–5 below remain accurate as a record of the run: the certificate verdicts, the frozen
> panel, the training receipt and the equal-budget protocol are all sound, and the infrastructure
> built here is what produced the corrected result. The **interpretation** is what changed.
>
> Full account: `eval/results/multichild-20260920/RESULTS.md`. Two further defects found by trying to
> invert the curriculum: `split-initialiser` is **semantically destructive** (it re-binds an
> assignment's target and leaves the original variable uninitialised), and the mutation catalogue's
> `drop-switch-default` is not invertible from the candidate because the removed arm's constant is
> gone.

> ## STATUS: AUDITED — THREE BOUNDARY DEFECTS AND ONE PROVENANCE DEFECT, ALL CLOSED; RESULT RE-MEASURED
>
> An independent audit (2026-09-20, after this receipt was first written) found a **soundness defect
> in the exactness oracle behind the headline result**, plus two further boundary defects, and I
> found a provenance defect of my own while reconciling it. **All four are now fixed, and the
> headline number was re-measured through the corrected oracle rather than argued.** The audit
> narrative is kept below verbatim as the record of what was wrong; the resolution and the outcome
> follow it.
>
> | # | defect | status | where it is closed |
> |---|---|---|---|
> | 1 | the evaluator decided exactness from `.text` equality, which excludes relocations, so a candidate calling the wrong function could count as a verified repair | **fixed** — the verdict is now `solver.byte_certificate.certify` everywhere | `tests/test_verifier_soundness.py` (the two-callee case is a fire test that RUNS by default), `VERIFIER-FIX.md` |
> | 2 | `load_frozen_tasks` checked ids, counts and `frozen_at` but not record CONTENT, so a task's candidate could be swapped for its own hidden answer after freezing | **fixed** — schema 3 binds the dataset bytes, every record, and the rendered prompt | `eval/frozen_manifest.py`, `tests/test_frozen_manifest.py` |
> | 3 | the promotion gate could return `promote` on an incomplete panel (12 matching ids, **zero** baseline draws, 2 adapter draws), and `--split train` reached the same gate | **fixed** — the gate now requires a complete equal-budget panel from a `test` split | `eval/posttraining_gate.py`, `tests/test_posttraining_safety.py` |
> | 4 | the receipt quoted an adapter digest (`04e3c523…`) that never matched the adapter which produced the numbers — **mine**, and unambiguous | **verified** — `evaluation.json` recorded the digest from the trainer's marker, and `ADAPTER-IDENTITY.md` recomputes it from the files with the trainer's own `dir_digest`: all six hashed files are byte-identical to the published ones | `evaluation-certified/ADAPTER-IDENTITY.md` |
>
> **Defect 1, as the audit reproduced it** (retained because it is the reason the oracle had to
> change). Two functions differing only in their callee —
> ```c
> extern int external_a(int);
> int syn_audit(int x) { return external_a(x); }
> ```
> versus the same with `external_b` — compile to **byte-identical `.text`**:
> `27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000`, with a differing
> relocation at offset `0x8` (`R_MIPS_26 external_a` vs `external_b`). The old oracle said
> **exact**; `solver.byte_certificate.certify()` says **exact=False**. The certificate is right.
>
> **Defect 4, stated plainly.** Retraining is **not** bit-identical: four runs from the same seed
> gave last-losses 0.04238 / 0.04289 / 0.04262 / 0.04251 and four different weight digests, so the
> digest quoted in an earlier draft of this receipt never matched the adapter that produced the
> numbers. The certified run's adapter is now pinned by recomputation, not by a marker:
> `evaluation-certified/ADAPTER-IDENTITY.md` finds the directory whose `dir_digest` reproduces the
> recorded `4dab8f34…`, and shows that **every file it hashed is byte-identical to the published
> file of the same name** (weights `adapter_model.safetensors`, 80,792,096 bytes, sha256
> `7be4f7a0…`). The first look at this *did* report a mismatch, because the published directory has
> since gained a leftover `staging/` copy plus the receipt and marker files beside the adapter — the
> digest covers names and sizes, so any added file changes it. That is why the check locates the
> hashed directory and then requires byte-identity, instead of comparing one string. A future run
> should record the digest from the loaded files, so no recomputation is needed.
>
> **The dataset was re-frozen under content hashing, and the rebuild is the same panel.** A
> schema-2 manifest carries no per-record content hashes, so the hardened loader refuses it
> outright; the panel the certified run used is archived byte-for-byte in
> `evaluation-certified/frozen-panel/`, and the dataset was rebuilt as
> **schema 3, `manifest_sha256 2db43b7981957b1e…`, `dataset_sha256 13c727109da9f6cb…`, 56 records,
> 29 train / 27 test**. `evaluation-certified/PANEL-IDENTITY.md` compares the two panels leaf by
> leaf, per task id: every experiment-determining field is identical (solver-visible assembly,
> candidate and feedback; the hidden answer; the target code image and instruction count; the
> certificate verdict and scope; the leakage report; the compiler recipe; the split). The only
> differences are `parent.object_sha256`, `target.object_sha256` and `provenance.built_at` — IDO
> objects embed the source path and a random `asm_processor` temp name, so the same C does not
> compile to the same `.o` bytes. Those three fields appear **zero** times in
> `evaluate_source_repair.py`, `frozen_manifest.py`, `train_source_repair.py` and
> `posttraining_gate.py`, and the evaluator recompiles the target from the answer rather than
> trusting a stored digest. So the certified measurement binds to the rebuilt panel by content —
> and the loader was checked in both directions: the rebuilt panel **loads** (train 29, test 27,
> compiler identity matched with no notes) and the archived schema-2 panel is still **refused**.
>
> **There are three panels, and the adapter was trained on a different one than it was evaluated
> on — which is fine, but only because it is checked.** The trainer's receipt records the task file
> it trained on, and that is a **third** build again: `dataset-txtoracle-ARCHIVED/tasks.jsonl`
> (`a393dc45…`), predating the certificate. `evaluation-certified/TRAINING-PANEL.md` compares it
> with the current panel against all 56 records: **task ids identical, `child.exact` disagrees in 0
> records**, and **0 of the 29 train records differ in any label, input, answer or unclassified
> field**. So the adapter did not learn from a single mislabelled or different example. The only
> differences are *how verification was recorded* — the `.text`-era build described its method in
> prose and carried no certificate block, the certificate build records a structured one — and the
> non-reproducible object digests. Rebuilding under the certificate **relabelled nothing**.
>
> **RESOLVED 2026-09-20 — the oracle is fixed and the result was RE-MEASURED under it.** The
> certificate is now the verdict everywhere, the dataset was rebuilt and re-verified, and the
> evaluation was re-run. Three outcomes, all recorded rather than asserted:
>
> 1. **The dataset was sound.** Rebuilt under `byte_certificate.certify`, still **56/56 verified
>    repairs**, zero leakage, same 29/27 family-disjoint split. The oracle defect never corrupted
>    the data.
> 2. **The result survives, unchanged.** Re-evaluated through the certificate: **baseline 4/27,
>    adapter 19/27**, 54 calls per arm, gained 15, lost 0.
> 3. **The defect caused no false positives here, and that is now measured, not argued.** Across
>    all 108 draws, the number where `.text` matched but the certificate **rejected** is **0**.
>    The reason is structural: the generator emits exactly one external symbol name (`syn_ext`)
>    and only two families call anything, so a model cannot summon a second callee name that
>    compiles. The oracle was genuinely unsound in general — the audit's reproduction is real and
>    is pinned by a fire test — but it did not inflate this particular number.
>
> The verified re-measurement is `evaluation-certified/`. Its `evaluation.json` records manifest
> `6f142cdca0d62bc6…`, the **schema-2** panel it actually ran on — that panel is archived in
> `evaluation-certified/frozen-panel/`. The same panel re-frozen with content hashes is
> `2db43b7981957b1e…`, and `PANEL-IDENTITY.md` shows the two are the same experiment. §4–§5 below
> still quote the original `.text`-era run; its numbers are reproduced by the certified run, and the
> certified artifacts are the ones to cite.
>
> **Outcome: one bounded LoRA adapter was trained on a compiler-verified repair dataset and
> promoted by a preregistered held-out rule, and the promotion was re-confirmed under a corrected
> object/relocation certificate. It remains a result on a SYNTHETIC curriculum with the scope
> limits in §4 and §6 — not established matching-decompilation capability.**

**Original summary (retained): one bounded LoRA adapter was trained on a compiler-verified repair
dataset and promoted by a preregistered held-out rule. The headline is deliberately qualified,
because the composition of the gain is more informative than its size (section 4).**

---

## 1. What was built

The stage implements the loop the handoff asks for: problem generation → attempted C repair →
IDO/object verification → lineage-correct training data → local adapter training →
equal-budget held-out evaluation → conditional promotion.

| Stage | Module | Receipt |
|---|---|---|
| Trajectory lineage audit | `eval/trajectory_audit.py` | `eval/results/local-posttraining-20260920/AUDIT.md` |
| Repair candidate generation | `eval/repair_mutations.py` | catalogue measured in `AUDIT.md` §mutations |
| Dataset build + freeze + leakage | `eval/repair_dataset_synth.py` | `dataset/build_receipt.json`, `dataset/FROZEN_SPLIT.json` |
| Prompt construction (one site) | `eval/repair_prompts.py` (`synthetic_repair_prompt`) | — |
| Adapter training | `eval/train_source_repair.py` | `/home/grant/decomp/posttraining-20260920/adapter-smoke/training_receipt.json` |
| Evaluation | `eval/evaluate_source_repair.py` | `evaluation-certified/evaluation.json` |
| Frozen-panel boundary | `eval/frozen_manifest.py` | `dataset/FROZEN_SPLIT.json` (schema 3) |
| Dashboard training action | `eval/training_control.py` | `/home/grant/decomp/local-research/training-receipt.json` |
| Verifier fix, in full | — | `VERIFIER-FIX.md` (what was unsound, the fire test per defect, what was **not** verified) |
| Promotion rule | `eval/posttraining_gate.py` | inside `evaluation.json` under `gate` |

### The task, and why it is a real repair task

`tools/synthetic_corpus.py` generates C deterministically per (family, seed) and compiles it with
the game's own IDO recipe. That gives a pairing the game-function route cannot:

* the generator's C is the **hidden answer**;
* its compiled `.text` is the **target**;
* a **damaged copy** of that C is the candidate the solver must repair;
* the answer is a *verified* repair, because it is compiled a second time and its `.text` is
  compared against the target's.

The solver is shown exactly three things — target assembly, the candidate C, and the compiler's
own feedback about that candidate. `leakage_check` runs on every record before it is written and
refuses a task whose answer appears anywhere the solver was not handed it.

### Measured mutation catalogue

`probe_mutations` compiled 88 generated functions (11 families × 8 seeds) and every mutant of
each. Every mutation in the catalogue must satisfy three properties, and all three are measured
rather than assumed: it compiles, it changes the code, and it keeps the function's identity.

| mutation | fault axis | mutants built | compiled | changed the code |
|---|---|---|---|---|
| `narrow-locals` | immediate | 24 | 24 | 24 |
| `split-initialiser` | regalloc | 16 | 16 | 16 |
| `subtract-to-narrow` | immediate | 11 | 11 | 11 |
| `drop-switch-default` | structural | 4 | 4 | 4 |
| `while-form` | structural | 1 | 1 | 1 |
| `divide-to-shift`, `invert-comparison` | — | **0 — reported, not hidden** | — | — |

56 mutants became tasks; **9 further mutants were rejected as code-identical to the target**
(section 5, defect 1), and **0 failed to compile**. `while-form` fires once because it applies to
one family and its regex requires the exact `for (i = …; …; …)` shape that only some seeds emit.

The two that never fire are retained with a docstring saying the current generator emits neither
division nor a comparison-ternary, so the catalogue does not claim coverage it lacks.

---

## 2. Dataset manifest and lineage checks

`dataset/FROZEN_SPLIT.json` — **schema 3**, manifest sha256
`2db43b7981957b1e573db97114b7e05738b2ca94ef183c2e20ecacd938f90fab`, `dataset_sha256`
`13c727109da9f6cb1e493aca5bdacfcf764c69589434654147e44abbdf76b728` over 56 records, written
**before** any training and refusing to overwrite itself.

Schema 3 is the post-audit shape: it binds the dataset's bytes, every record's content (whole
record, solver-visible input, answer) and the panel's shape, so a candidate swapped for its own
hidden answer after the freeze is refused. `eval/frozen_manifest.py` re-checks all of it, plus the
**rendered prompt** for leakage, before the model loads. Verified in both directions with
`.cache/recon/check_frozen.sh`: the rebuilt panel loads (train 29, test 27, compiler identity
matched with no notes), and the pre-hashing schema-2 panel is refused.

| | |
|---|---|
| tasks | **56** (29 train / 27 test) |
| verified repairs | **56 / 56** (`child.exact`, from `byte_certificate.certify` over sections AND relocations) |
| split rule | by whole template family — `if_chain`, `saved_regs`, `switch_dense` train; `loop_for`, `stack_spill`, `switch_sparse` test |
| assembly overlap between splits | **0** |
| leakage failures | **0** |
| mutants that failed to compile | 0 |
| mutants identical to the target | 9 (rejected — see section 5) |

**A rebuild is the same panel but not the same bytes.** IDO objects embed the source path and a
random `asm_processor` temp filename, so `parent.object_sha256`, `target.object_sha256` and
`provenance.built_at` differ between two builds of identical source, and the manifest digest changes
with them. Everything that decides the experiment is identical — `PANEL-IDENTITY.md` checks this
leaf by leaf, per task id — and none of those three fields is read by the evaluator, the trainer or
the gate. So a future reader who rebuilds and sees a new manifest sha has not found drift.

### Trajectory audit (the real game data)

`AUDIT.md` answers the handoff's question 1 across `kb-sbk1.sqlite` and the three fresh batches.
The finding is not the one the prompt anticipated:

* **Zero genuine** "recorded parent differs from the candidate in the prompt" defects. Of 8,132
  parented rows, 475 have a stored prompt; 259 contain the parent verbatim and 216 render it with
  the KB's own ` 12 | ` line-number gutter — the parent *is* the candidate shown.
* The real gap is **coverage**: 7,657 parented rows (94.2%) store no prompt at all, so their
  lineage is **unverifiable, not verified-good**. The KB yields **139** usable verified repair
  pairs from 8,132 edges.
* The fresh collection data passes every check at 100%: 160/160 rows usable, full hash and
  compiler-identity coverage, 52/52 parents verifiably the candidate shown, zero role violations.
* `prefix-archive` retains 12 repair-role rows with no parent — the pre-fix defect, now confined
  to an archive.

**Consequence for this stage:** the historical KB contributes **no** training pairs here. The
dataset is synthetic because that is where verified repairs exist.

---

## 3. Training receipt

`adapter-smoke/training_receipt.json`, bounded by both step count and wall clock.

| | |
|---|---|
| base | `/home/grant/decomp/models/qwen2.5-coder-7b`, dir digest `d5923d61d1772458…` |
| adapter | `~/decomp/posttraining-20260920/adapter-smoke` — run #4, marker digest `4dab8f34…`, **verified against the files** in `evaluation-certified/ADAPTER-IDENTITY.md`: the weights are `adapter_model.safetensors`, 80,792,096 bytes, sha256 `7be4f7a02d477be31e87549b19208c478a81017620154599c92327ea162d5271`, and all six hashed files are byte-identical to the published ones |
| examples | 29 / 29 used, 0 dropped for length, 0 skipped unverified |
| tokens | 50,170 total (45,213 prompt / 4,957 completion) |
| LoRA | r=8, α=16, 20,185,088 trainable params |
| steps | **7** optimizer steps (batch 1 × grad-accum 4, length-bucketed) |
| loss | **0.13286 → 0.04238** (run #1; the four retrains gave last-loss 0.04238 / 0.04289 / 0.04262 / 0.04251) |
| weight change | **392 / 392 LoRA tensors changed** |
| peak GPU | **10.02 GB** (cap 10.5 GB) |
| elapsed | 46.3 s |
| stop reason | `training loop exhausted` |

Real weight changes, not a wiring check: 392 of 392 tensors differ from their initialisation.

---

## 4. Equal-budget evaluation

`evaluation-certified/evaluation.json`, on the **27 frozen test tasks**, 2 draws per task per arm,
both arms in **one loaded model** with the adapter toggled. 54 model calls per arm, identical
prompts, identical sampler, identical oracle. Manifest `6f142cdca0d62bc6…` — the schema-2 panel it
ran on, archived in `evaluation-certified/frozen-panel/` and shown by `PANEL-IDENTITY.md` to be the
same experiment as the current schema-3 panel.

**Primary outcome: object-exact tasks**, where exactness is `solver.byte_certificate.certify`'s
verdict over allocated sections **and relocation expressions**. The `.text` image and `score` are
diagnostics; the table below is identical under either definition for this run, and the number of
draws where `.text` matched but the certificate rejected is **0** (see the header).

> One wording caveat when reading the artifact directly: `evaluation.json` still carries
> `primary_outcome: "object-exact tasks (.text equality via the game's IDO recipe)"`. That is the
> **old** label, written by the pre-audit evaluator. The verdicts it counted were recomputed through
> the certificate — that is what the re-measurement was — and the 0-disagreement count is the
> evidence that the label is now the only stale thing about it. Do not quote the label as the
> method.

| | baseline | adapter |
|---|---|---|
| object-exact tasks | **4** | **19** |
| best-of-2 exact rate | 14.8% | **70.4%** |
| tasks compiling | 11 | **27** |
| draws compiling | 14 / 54 | **51 / 54** |
| mean best score | 24.5 | 86.2 |
| exact per 1000 calls | 74.1 | **351.9** |
| wall clock | 136.6 s | 206.6 s |

Gate: **promote** — gained ≥1 ✓ (15 gained), lost none ✓, panel large enough ✓, no arm missing
tasks ✓. `lost_by_adapter` is empty; 4 tasks were closed by both arms.

> Two wording caveats when reading the artifact directly, both like the `primary_outcome` one above.
> The recorded `gate.conditions` keys are `R1_gained_at_least_one`, `R2_lost_none`,
> `R3_panel_large_enough`, `R4_no_arm_missing_tasks` — the **pre-rename** names. The gate now emits
> `R4_panel_fully_covered` and `R5_budget_executed`, because the rule it enforces is coverage of the
> declared panel plus actual draws equal to budget, not arm-vs-arm task equality. Only the labels
> moved.
>
> **And the decision does not depend on the older gate.** `.cache/recon/regate_recorded.py` re-derives
> it with the **current** gate, on the same recorded rows, against a spec built from the **schema-3**
> manifest: verdict **`promote`**, with all six conditions true — `R0_spec_eligible`,
> `R1_gained_at_least_one`, `R2_lost_none`, `R3_panel_large_enough`, `R4_panel_fully_covered`,
> `R5_budget_executed`. So the promotion survives the rule that the audit showed the run-time gate
> did not enforce, and it survives being re-bound to the re-frozen panel.

### The composition of the gain — read this before quoting the table

The large number is real and it is **not** mainly "the adapter repairs better". The baseline's
failures are dominated by **syntax**, not by semantic distance:

* baseline: **40 of 54 draws did not compile**, with diagnostics like
  `cfe: Error: line 3: Syntax Error — static inline u32 mult_by_five(u8 value)`. `static inline`
  is C99; the project's prompt forbids it and IDO rejects it.
* adapter: **3 of 54 draws did not compile**.
* Among the 11 tasks the baseline *did* compile, its median best score was **81.25** and its max
  was **100.0** — the baseline was not lost, it was **unable to emit C89 that IDO accepts**. 16 of
  27 tasks it never compiled at all, every draw scoring 0.

So the adapter's dominant learned behaviour is **emitting C89 that IDO accepts**, plus the repair
edit. That is a genuine and useful capability shift — the baseline cannot do it — but it means
this curriculum does not strongly exercise *semantic* repair, because the correct answer is
largely reconstructible from the candidate that is already in context.

Per-family detail is more informative than the aggregate:

| mutation | adapter exact / tasks | note |
|---|---|---|
| `narrow-locals` | **14 / 16** | the strongest family |
| `subtract-to-narrow` | 2 / 2 | |
| `split-initialiser` | **3 / 8** | the regalloc family is where the adapter still fails |
| `while-form` | 0 / 1 | |

so the gain is concentrated on the *width/immediate* axis and largely absent on *regalloc*.
(The first version of this table said 15/16 and 1/8; both were wrong, and an independent audit
caught the inconsistency against §"Memorisation or generalisation?" below. Both tables were then
re-derived from the certified artifact — `.cache/recon/derive_families.py` — and agree:
baseline 4, adapter 19, gate gained 15 lost 0.)

> **Caveat added after the audit, and now discharged:** every number in this subsection originally
> came from the unsound `.text`-only oracle described at the top of this file. The analysis was
> **re-derived from `evaluation-certified/evaluation.json`** after the fix, and every count below
> is unchanged — which is expected, since the number of draws where `.text` matched but the
> certificate rejected is 0. The counts are now certificate counts.

### Memorisation or generalisation? (measurable from the frozen split itself)

The split is by whole **family**, so a mutation present in both splits was trained on one
family's instances and tested on a different family's. The adapter therefore cannot have
memorised the test instance — only the mutation. That gives a real generalisation probe from
the artifacts already on disk, no further compute:

| mutation | train instances | test instances | adapter exact on test |
|---|---|---|---|
| `narrow-locals` | 8 (`saved_regs`, `switch_dense`) | 16 (`loop_for`, `stack_spill`) | **14 / 16** |
| `subtract-to-narrow` | 9 (`if_chain`, `switch_dense`) | 2 (`switch_sparse`) | **2 / 2** |
| `split-initialiser` | 8 (`saved_regs`) | 8 (`stack_spill`) | 3 / 8 |
| `while-form` | **0** | 1 (`loop_for`) | 0 / 1 |

What this establishes, and what it does not:

* **Not pure memorisation.** Every exact match the adapter gained is on a mutation it had seen,
  but the instances are family-disjoint from training. It generalised across families for
  `narrow-locals` and `subtract-to-narrow`.
* **Not general repair.** `split-initialiser` is the decisive case: trained on 8 instances in
  `saved_regs`, tested on 8 in `stack_spill`, **3/8 exact**. It fails a mutation it was trained
  on when the surrounding function family changes, so the skill it learned is family-coupled,
  not a general regalloc repair.
* `while-form` has exactly one test instance and zero training instances, so its 0/1 is not
  evidence in either direction and is not reported as one.

The remaining unresolved confound is the one section 4 names: the baseline's failures are mostly
**C89 syntax errors**, so part of the gain is "emits compilable C89" rather than "repairs
better". Separating those two needs the candidate-distance ablation in section 7, item 1.

**Not claimed.** This is not evidence of game-function capability: the tasks are synthetic
generated functions, so exactness is an **object certificate** — allocated sections and relocation
expressions in a standalone translation unit — not a ROM-backed function match. Transfer to real
decompilation is **untested**. No recursive improvement is claimed: one promoted generation is one
step, not a loop.

---

## 5. Tests

Run from **WSL**, where the repo and the IDO toolchain live:

```
bash .cache/recon/test_posttraining.sh      # 129 tests, includes the real-compiler fire tests
```

which is the six suites below. The dashboard control suite drives `wsl.exe`/`powershell`, so it must
be run from **Windows** `python -m pytest` (a WSL-side run cannot see those processes):

```
python -m pytest -q tests/test_training_control.py     # 41 tests
```

| suite | tests | what it pins |
|---|---|---|
| `test_verifier_soundness.py` | 22 | the verdict comes from the certificate, not `.text`; the two-callee case is a **fire test that runs by default**; every draw keeps its source and artifacts |
| `test_frozen_manifest.py` | 20 | the freeze binds dataset bytes, per-record content, splits and the rendered prompt; a swapped candidate is refused |
| `test_posttraining_safety.py` | 23 | publish/interrupt gates and the promotion rule |
| `test_repair_dataset_synth.py` | 21 | dataset build, leakage suite, certificate verdicts |
| `test_trajectory_audit.py` | 35 | KB lineage audit |
| `test_resource_limits.py` | 8 | GPU/CPU/nice caps and the pause file |
| **total** | **129** | all green |

The four properties the handoff names, and the defects that were found by *running* rather than by review:

| required proof | where |
|---|---|
| an interrupted run cannot publish an adapter | `test_posttraining_safety.py`: staged weights are not published; both signal reasons are rejected; a published adapter blocks a rerun |
| a failed gate keeps the baseline | `test_a_failed_gate_keeps_the_baseline`, `test_a_small_panel_is_inconclusive_and_never_a_promotion`, `test_tasks_only_one_arm_ran_make_the_result_inconclusive`, `test_the_gate_never_reads_a_similarity_score` |
| held-out answers cannot enter the model input | `test_frozen_manifest.py` (leakage is checked on the **rendered prompt**, before the model loads) and the `test_repair_dataset_synth.py` leakage suite (fires on full source, on a source echoed into *feedback*, on a hash-only leak, and stays silent on shared declarations) |
| a run that trained nothing must not publish | `test_the_publish_decision_rejects_a_run_that_changed_no_weight` |
| failed branches are retained, with the text needed to learn from them | `test_a_failed_draw_keeps_the_candidate_text_not_just_its_hash` |
| the dashboard's training action really launches and really stops | `test_training_control.py::test_launching_through_the_real_path_reaches_running_and_stops_cleanly` (WSL worker, reaches `running`, Stop empties the process group) |

### Failure text retention: the certified run, and the earlier one

The certified run retains, **for every one of its 108 draws**, the candidate C (`source`), the head
of the model's raw response, the compile status, the score and the compiler `stderr`. Counted from
`evaluation-certified/evaluation.json`, not asserted:

| arm | draws | `source` | `raw_response_head` | certificate | non-empty `stderr` |
|---|---|---|---|---|---|
| baseline | 54 | **54** | 54 | 14 (the draws that compiled) | 40 |
| adapter | 54 | **54** | 54 | 51 | 3 |

So the baseline's 40 compile errors and the adapter's 3 are readable, and the 8 adapter failures
and 23 baseline failures on tasks can be read rather than merely counted. A certificate is present
exactly when the candidate compiled, which is why baseline shows 14 and not 54.

**What is *not* retained for this run, stated so it is not assumed:** the per-draw artifact files
(candidate C, model output, compiled object, certificate JSON written beside the run) and the
compiled `.o` files. The evaluator writes those now — pinned by
`test_every_draw_references_its_object_and_certificate_artifacts` — but this run was written
**before** that change landed, so `evaluation-certified/` contains only the JSON and JSONL outputs.
Everything needed to *read* the failures is in the JSON; only the object-level artifacts would have
to be regenerated, and they are regenerated by re-running, since the tasks are frozen.

The earlier `.text`-era artifact did less than this: for a non-exact draw it stored only
`source_sha256` and the diagnostic, so its failures could not be read at all. Cite the certified
artifact's failure detail, not the old one's.

Four defects this work found, each now pinned by a test:

1. **Whole-object comparison is not reproducible.** IDO embeds the source path and a random
   `asm_processor` temp filename: the same C compiled twice produced 1480 and 1484-byte objects
   differing at byte 35. Every one of the first 65 tasks reported `recompile-mismatch`. Fixed by
   comparing the `.text` section, which also exposed **9 mutants that were code-identical no-ops**
   and would have been trained on as if they were repairs.
2. **A run that trained nothing published an adapter** — `steps_run: 0`, `tensors_changed: 0` —
   because "the loop ended without an error" had been mistaken for "training happened".
3. **The publish gate then rejected successful runs**: it tested `stop_reason` before assigning
   the fallback, so a clean full-dataset run published nothing.
4. **`published` meant unloadable.** Weights were staged in `staging/` and the marker written
   beside it, so `PeftModel.from_pretrained(published_path)` raised `Can't find
   'adapter_config.json'`. Separately, `set_adapter(False)` is **not** "disable the adapter" —
   PEFT looks up an adapter literally named `False`, which would have compared the adapter
   against itself and reported a null result as a measurement.

The evaluation also **refused to run** against an unpublished adapter, which is the guard working
as intended rather than an obstacle.

---

## 6. Exact launch / resume commands

All state lives on the WSL filesystem. Set the resource caps first — the defaults leave half the
card and half the cores free. Run everything from **WSL**; `python` does not exist there, only
`python3` and the training venv, so name the interpreter explicitly.

```bash
# 0. CHECK THE FROZEN PANEL BEFORE SPENDING ANY GPU.
#    This is the gate the audit was missing: it re-verifies the dataset bytes, every record's
#    content, the splits, the compiler identity and the RENDERED PROMPT for leakage, and it exits
#    non-zero on a schema-2 manifest. It also confirms the archived pre-hashing panel is still
#    refused, so "it loads" means something.
bash .cache/recon/check_frozen.sh

# 1. build the dataset (deterministic). It REFUSES to overwrite a frozen manifest, deliberately:
#    a split that can be rewritten after a result is not a frozen split. To rebuild, archive the
#    panel first and delete FROZEN_SPLIT.json on purpose:
#      cp -r eval/results/local-posttraining-20260920/dataset <archive>/
#      rm eval/results/local-posttraining-20260920/dataset/FROZEN_SPLIT.json
bash .cache/recon/build_dataset.sh

# 2. train the adapter (bounded by BOTH limits; publishes only on a clean, weight-changing run)
SOLVER_GPU_MEMORY_GB=10.5 SOLVER_CPU_THREADS=2 SOLVER_NICE=15 \
  ~/decomp/train-venv/bin/python -m eval.train_source_repair \
  --base ~/decomp/models/qwen2.5-coder-7b \
  --tasks eval/results/local-posttraining-20260920/dataset/tasks.jsonl --split train \
  --out ~/decomp/posttraining-20260920/adapter-smoke \
  --max-seq-len 2304 --max-steps 40 --max-seconds 1200 --block-size 4

# 3. evaluate baseline vs adapter on the frozen split at equal budget
SOLVER_GPU_MEMORY_GB=10.5 \
  ~/decomp/train-venv/bin/python -m eval.evaluate_source_repair \
  --manifest eval/results/local-posttraining-20260920/dataset/FROZEN_SPLIT.json \
  --dataset  eval/results/local-posttraining-20260920/dataset/tasks.jsonl \
  --adapter  ~/decomp/posttraining-20260920/adapter-smoke \
  --out eval/results/local-posttraining-20260920/evaluation \
  --draws 2 --max-seconds-per-arm 1800
```

Then re-derive the identity and consistency claims from the artifacts, rather than restating them.
This runs every check in one go and exits non-zero if any fails:

```powershell
.\.cache\recon\verify_all.ps1
```

which is: the 129 WSL tests; the frozen panel loads and the pre-hashing panel is refused; the
rebuilt panel is the same experiment as the certified one (`PANEL-IDENTITY.md`); the recorded adapter
digest is true of the adapter on disk (`ADAPTER-IDENTITY.md`); the per-family numbers re-derive from
the artifact; **the adapter was trained on the same tasks and labels the receipt cites
(`TRAINING-PANEL.md`)**; **the promotion survives the current gate bound to the schema-3 panel**; the
modules import and the re-export surface is intact; and the 41 Windows-side control tests.

Individually:

```bash
python .cache/recon/compare_panels.py            # -> PANEL-IDENTITY.md
bash   .cache/recon/verify_adapter_identity.sh   # -> ADAPTER-IDENTITY.md
python .cache/recon/derive_families.py           # the per-family table
python .cache/recon/check_training_panel.py      # -> TRAINING-PANEL.md
python .cache/recon/regate_recorded.py           # the gate, re-applied
```

**Resume / stop.** There is no automatic retry and no budget reset. A published adapter makes
step 2 refuse, by design. To stop a run in flight, kill the process group; the training loop
checks its signal flag between steps and publishes nothing:

```bash
pkill -TERM -f 'eval.train_source_repair'
pkill -TERM -f 'eval.evaluate_source_repair'
```

**Through the dashboard (the panel's own action).** The progress app exposes the same launch, and
its Stop signals the whole process group so the trainer's compiler children die with it:

```bash
~/decomp/train-venv/bin/python -m eval.progress_app --port 8000     # then use the Research panel
python -m pytest -q tests/test_training_control.py                  # from WINDOWS: drives wsl.exe
```

Two things about that path are worth knowing, because both were broken and both are now fixed and
tested: the worker's working directory must be **this checkout** (`python -m eval.train_source_repair`
is importable from nowhere else — the game repo has no `eval` package, no `PYTHONPATH` and no
`.pth`), and the worker must set `running` when the trainer starts. If the panel sits at "starting"
or reports the trainer as offering no flags, that is this pair of defects, not a trainer problem.

**Reclaim the machine.** The WSL VM holds GPU and RAM after jobs exit; `wsl --shutdown` releases
both (measured this session: free RAM 9.3 GB → 16.7 GB, VRAM → 1 GB).

```powershell
wsl.exe --shutdown
```

---

## 7. What would make this stronger

Stated as the next measurements, not as claims:

1. **Separate the compile-success gain from the repair gain.** Re-run the evaluation against a
   candidate that is *further* from the answer (a multi-edit or m2c-drafted candidate), where the
   correct repair is not reconstructible from context. If the adapter still gains there, the gain
   is semantic; if it does not, the current result is a C89-conformance result and should be
   reported as one.
2. **Transfer to real game functions.** The synthetic curriculum has a verified-answer property
   the game data does not; the trajectory audit shows only 139 verifiable pairs exist in the KB.
   The honest test is whether an adapter trained synthetically moves any held-out game function,
   which needs the game-function oracle path, not this one.
3. **A second generation.** Recursive improvement requires successive promoted generations
   beating predecessors on *fresh* held-out tasks at controlled compute. One promotion is not
   that, and this receipt does not claim it.
4. **Re-run the evaluation against the schema-3 panel, for a self-contained artifact.** The
   certified `evaluation.json` predates the hardened evaluator, so it records the schema-2
   manifest it ran on and the adapter digest it read from the marker, and it wrote no per-draw
   artifact files. All three are adequate here — the panel is shown to be the same experiment, the
   digest is verified against the files, and the JSON carries every draw's source — but a fresh
   run would produce an artifact that needs none of those footnotes. It costs one GPU pass over
   the same 27 frozen tasks; it is an ergonomics and auditability improvement, not a correctness
   one, which is why it was not spent when the machine was needed for the verification above.
