# Verifier soundness: what was unsound, what changed, and how to reproduce each fire test

Written 2026-09-20 by the verifier-soundness workstream. Applies to
`eval/results/local-posttraining-20260920/`. This document does **not** modify or restate
`RESULT.md`; where the two overlap, `RESULT.md` is the experiment receipt and this is the
verifier defect record.

Environment for every command below: WSL2 Ubuntu, `/home/grant/decomp/train-venv/bin/python`,
run from `/mnt/c/Code/gameDecomp`. No GPU is used by anything in this document.

The helper scripts referenced in §3 (`vs_build_prefix.py`, `vs_real_panel.py`) live in the
gitignored `.cache/recon/` scratch directory and do nothing the checked-in tests do not do; every
claim in this document is reproducible from `tests/test_verifier_soundness.py` and
`tests/test_frozen_manifest.py` alone, and each helper is described where it is used so it can be
rewritten from this page if the scratch directory is gone.

---

## 1. What was unsound

### 1.1 The exactness oracle ignored relocations (the confirmed defect)

`eval/repair_dataset_synth.py::code_image` extracts the `.text` section. `.text` carries no
relocations, so two functions that differ only in **which external symbol they reference** are
byte-identical in it. Both arms of the dataset/evaluation used `.text` equality as the `exact`
verdict.

Reproduced with the game's real IDO recipe (`build/src/race/ui/race_ui_effects.o`):

| source (same function name, different callee) | `.text` bytes | relocation at 0x8 |
|---|---|---|
| `extern int external_a(int); int f(int x){return external_a(x);}` | `27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000` | `R_MIPS_26 external_a` |
| the same with `external_b` | **identical** | `R_MIPS_26 external_b` |

`.text` equality said **exact**; `solver.byte_certificate.certify` says `exact=False`,
`status=object_sections_differ`. The certificate was right. A candidate that calls the wrong
function could therefore be counted and recorded as a verified repair.

### 1.2 The freeze bound ids, not content

`load_frozen_tasks` checked ids, counts and `frozen_at`. The audit replaced one task's
`input.candidate` with that task's own hidden answer **after** the freeze — turning a held-out
repair task into a copy of its solution — and the loader accepted it. A manifest that names ids
and nothing else is a table of contents, not a freeze.

### 1.3 The promotion gate could promote an incomplete run

`decide` compared the two arms' id sets to each other. A direct probe with **12 matching ids, 0
baseline draws, 2 adapter draws and one apparent gain** returned `promote`; `--split train`
reached the same code path. Two arms that agree with each other about a panel neither of them
covers are two incomplete runs that happen to have the same shape.

### 1.4 The recorded run could not be replayed

A successful draw kept `source_sha256` and nothing else, so the claimed repairs could not be
re-derived through a corrected verifier, and the object/certificate that produced the verdict were
left in a `mkdtemp` directory.

---

## 2. What the fix is

**The exactness rule, in one sentence:** a candidate is exact **iff**
`solver.byte_certificate.certify` finds the two objects' allocated sections *and their relocation
expressions* equivalent under the same link environment — `.text` equality is recorded as a
diagnostic (`text_identical`, `score`) and is never the basis of an `exact` verdict anywhere.

The certificate's own scope is carried through verbatim and was neither widened nor weakened:
allocated `.text`/`.data`/`.bss` sections and relocation expressions, same link environment,
excluding debug and ABI metadata and the final link layout, `whole_rom_verified` always `False`.
It is precisely because the certificate never reads **non-allocated** sections that it can be used
where whole-object comparison could not be: the same C compiled into two differently-named
directories produced **1416- and 1476-byte objects whose first difference is at byte 35** (the
embedded source path and the random `asm_processor` temp filename), and `certify` reports `exact`
— correctly, because the code and its relocations are the same. No extra hashing, path
normalisation or metadata scrubbing was needed; the existing certificate already handled it.

| requirement | where | what changed |
|---|---|---|
| oracle = the certificate | `eval/repair_dataset_synth.py` (`certify_exact`, `build_task`, `build`), `eval/evaluate_source_repair.py` (`evaluate_arm`) | `code_image` retained as a diagnostic only; `exact` comes from `certify` over the target and candidate objects |
| frozen-manifest boundary | `eval/frozen_manifest.py` (new; re-exported by `eval/evaluate_source_repair.py`), `eval/repair_dataset_synth.py::freeze_manifest` | manifest schema 3: `record_hashes` (whole record / solver-visible input / answer), `dataset_sha256`, `dataset_lines`; the loader verifies the manifest's own digest, the dataset file digest, per-record content, duplicate ids, two-split ids, counts vs id lists, dataset-vs-panel membership, record-level split agreement, and the compiler identity (`command_sha256`, compiler, target) against the recipe **actually resolved for the run**; the leakage check now also runs on the FINAL RENDERED PROMPT for every selected task, before any GPU work |
| promotion gate | `eval/posttraining_gate.py` | the gate now takes an `EvaluationSpec` (frozen panel, split, per-arm draw budget, kind); R0 eligibility, R4 coverage of the declared panel (not arm-vs-arm equality), R5 actual draws executed == declared budget; new `ineligible` verdict; a caller that passes two arms and no specification cannot promote |
| replay logging | `eval/evaluate_source_repair.py` | every draw — exact ones included — keeps the extracted C, the full raw model output, the compiled object and the certificate, copied beside the evaluation with a digest each; rows record `draws`/`draws_generated`, the prompt digest and the compiler recipe digest; the payload records the model, adapter and compiler identities and the certificate's scope |

**The existing frozen manifest is stale by design.** Schema 2 cannot bind the records it names, so
the loader refuses it with an explicit *"THIS MANIFEST PREDATES CONTENT HASHING … Re-freeze it"*
error rather than accepting it or failing obscurely. It was archived to
`dataset-txtoracle-ARCHIVED/` and the dataset rebuilt as schema 3.

### 2.1 `record_hashes` bind a specific BUILD, not just the experiment

This one sentence prevents a future misreading, so it is stated plainly here and repeated in
`eval/frozen_manifest.py`'s module docstring:

> **A rebuilt panel has a different `manifest_sha256` even when nothing experimental changed.**
> IDO objects embed the absolute source path and a random `asm_processor` temp filename, so the
> same C compiles to different object bytes; `parent.object_sha256`, `target.object_sha256` and
> `provenance.built_at` therefore differ in **every** record of a rebuilt panel, and `record_hashes`
> (which digest the whole record) and the manifest digest change with them. None of those three
> fields is read by `eval.evaluate_source_repair`, `eval.frozen_manifest`,
> `eval.train_source_repair` or `eval.posttraining_gate`, and the evaluator recompiles the target
> from the answer rather than trusting a stored digest. **Do not treat a changed manifest digest
> after a rebuild as drift in the freeze.** To decide whether a rebuilt panel is the same
> experiment, compare the solver-visible `input.*`, the answers and the compiler recipe — which is
> what `evaluation-certified/PANEL-IDENTITY.md` does for the rebuilt post-training panel. Measured
> there: all 56 ids, all solver-visible inputs, all answers, the target assembly digests, the
> certificate summaries, the leakage reports and the recipe are identical; only
> `parent.object_sha256` / `target.object_sha256` / `provenance.built_at` differ, in all 56.

An earlier `record_hashes` design would have had this backwards: a digest over the *code* rather
than over the *record* would have been stable across rebuilds but unable to detect the audit's
swap, because the swap changes `input.candidate` and nothing else the code digest covers.

---

## 3. Fire tests, and the exact command for each

```
cd /mnt/c/Code/gameDecomp
PY=/home/grant/decomp/train-venv/bin/python
```

| # | required proof | test | command |
|---|---|---|---|
| 1 | a changed external **callee** is NOT exact (the confirmed defect, real recipe) | `test_a_changed_callee_is_not_exact` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_a_changed_callee_is_not_exact` |
| 2 | a changed referenced **data/global symbol** is NOT exact | `test_a_changed_referenced_global_is_not_exact` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_a_changed_referenced_global_is_not_exact` |
| 3 | byte-identical recompilation IS exact (positive control) | `test_a_recompiled_identical_source_is_still_exact` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_a_recompiled_identical_source_is_still_exact` |
| 4 | a post-freeze **candidate** swap is rejected | `test_a_post_freeze_candidate_swap_is_rejected` | `$PY -m pytest -q tests/test_frozen_manifest.py::test_a_post_freeze_candidate_swap_is_rejected` |
| 5 | a post-freeze **answer** swap is rejected | `test_a_post_freeze_answer_swap_is_rejected` | `$PY -m pytest -q tests/test_frozen_manifest.py::test_a_post_freeze_answer_swap_is_rejected` |
| 6 | duplicate ids are rejected (freezer **and** loader) | `test_duplicate_ids_are_refused_by_the_freezer_and_by_the_loader`, `test_duplicate_ids_inside_one_split_list_are_rejected` | `$PY -m pytest -q tests/test_frozen_manifest.py -k duplicate` |
| 7 | split drift is rejected | `test_a_task_in_two_splits_is_rejected`, `test_counts_that_disagree_with_the_id_lists_are_rejected`, `test_a_record_whose_split_disagrees_with_its_listing_is_rejected`, `test_a_dataset_task_the_manifest_never_froze_is_rejected` | `$PY -m pytest -q tests/test_frozen_manifest.py -k "split or counts or never_froze"` |
| 8 | a stale manifest is refused **with a clear message** | `test_a_stale_manifest_without_content_hashes_is_refused_with_a_clear_message` | `$PY -m pytest -q tests/test_frozen_manifest.py::test_a_stale_manifest_without_content_hashes_is_refused_with_a_clear_message` |
| 9 | the compiler identity is checked | `test_the_compiler_identity_recorded_in_the_manifest_is_checked` | `$PY -m pytest -q tests/test_frozen_manifest.py -k compiler_identity` |
| 10 | the gate refuses a **train or dev** split | `test_the_gate_refuses_a_train_or_dev_split` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_the_gate_refuses_a_train_or_dev_split` |
| 11 | the gate refuses **incomplete panel coverage** | `test_the_gate_refuses_an_incomplete_panel_even_when_both_arms_agree` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_the_gate_refuses_an_incomplete_panel_even_when_both_arms_agree` |
| 12 | the gate refuses **unequal / zero actual draws** (the audit's probe) | `test_the_audit_probe_cannot_promote`, `test_the_gate_refuses_a_run_that_stopped_early`, `test_the_gate_refuses_a_task_that_recorded_no_draw_count`, `test_the_gate_refuses_draws_that_never_produced_a_model_response` | `$PY -m pytest -q tests/test_verifier_soundness.py -k "audit_probe or stopped_early or no_draw_count or never_produced"` |
| 13 | diagnostic/subset runs are ineligible | `test_a_subset_or_diagnostic_evaluation_is_explicitly_ineligible`, `test_a_specification_that_declares_nothing_cannot_promote` | `$PY -m pytest -q tests/test_verifier_soundness.py -k "ineligible or declares_nothing"` |
| 14 | the leakage check runs on the **final rendered prompt** | `test_the_leakage_check_runs_on_the_final_rendered_prompt`, `test_the_loader_validates_every_task_prompt_before_returning`, `test_the_evaluator_renders_the_prompt_through_a_checked_boundary` | `$PY -m pytest -q tests/test_frozen_manifest.py -k "rendered_prompt or validates_every_task_prompt or checked_boundary"` |
| 15 | the gate can still pass a complete run (positive control) | `test_the_gate_promotes_a_complete_equal_budget_run` | `$PY -m pytest -q tests/test_verifier_soundness.py::test_the_gate_promotes_a_complete_equal_budget_run` |
| 16 | every draw keeps C + raw output + object/certificate references | `test_every_draw_references_its_object_and_certificate_artifacts`, `test_every_draw_keeps_its_source_including_successes` | `$PY -m pytest -q tests/test_verifier_soundness.py -k "every_draw"` |

Whole files:

```
$PY -m pytest -q tests/test_verifier_soundness.py tests/test_frozen_manifest.py \
                tests/test_repair_dataset_synth.py tests/test_posttraining_safety.py
```

**These tests fail on the pre-fix code.** The pre-fix sources are untracked in this workspace
(nothing here was ever committed), so the replay restores each pre-fix implementation verbatim
from the audit snapshot into a scratch tree — `.cache/recon/vs_build_prefix.py` puts back the
pre-fix `posttraining_gate.py`, the pre-fix 23-line `load_frozen_tasks`, `.text`-equality
`certify_exact`, and the pre-fix inline prompt construction, and changes nothing else:

```
$PY .cache/recon/vs_build_prefix.py
cd .cache/recon/prefix && $PY -m pytest -q tests/test_verifier_soundness.py tests/test_frozen_manifest.py
#   -> 30 failed, 12 passed   (fixed tree: 42 passed)
```

The headline failures on the pre-fix tree, verbatim:

* `test_a_changed_callee_is_not_exact` — `assert True is False`: `.text` equality certified the
  changed callee as exact.
* `test_a_post_freeze_candidate_swap_is_rejected` — `Failed: DID NOT RAISE SystemExit`: the
  pre-fix loader accepted the swapped candidate.
* `test_the_audit_probe_cannot_promote` — the probe returned `promote`.

The real panel, both directions (`.cache/recon/vs_real_panel.py`):

```
$PY .cache/recon/vs_real_panel.py
#   ACCEPTED dataset: schema=3 frozen=27 selected=27 train=29 total=56
#     manifest_sha256 2db43b7981957b1e…  dataset_sha256 13c727109da9f6cb…  records 56
#     load_notes []   (the compiler identity matched exactly, not merely the command)
#     distinct prompt digests: 27 of 27
#   REFUSED dataset-txtoracle-ARCHIVED: … is manifest schema 2 and carries no per-record
#     content hashes … THIS MANIFEST PREDATES CONTENT HASHING … Re-freeze it …
```

---

## 4. Test counts

| | before | after |
|---|---|---|
| `tests/test_repair_dataset_synth.py` | 21 | 21 |
| `tests/test_posttraining_safety.py` | 23 | 23 |
| `tests/test_verifier_soundness.py` | did not exist | **22** (0 skipped) |
| `tests/test_frozen_manifest.py` | did not exist | **20** |
| run above (4 files) | 44 | **86** |
| plus `tests/test_trajectory_audit.py` (35) | 79 | **121 passed, 0 failed** |
| the 42 new tests on the pre-fix tree | — | **30 failed, 12 passed** |

Two existing fixtures were updated because the formats they froze no longer exist; **no assertion
was weakened**:

* `tests/test_posttraining_safety.py::_rows` gained `"draws": 2` and the five gate call sites pass
  the `EvaluationSpec` the gate now requires — every assertion is unchanged, because the fixtures
  declare exactly the panel and budget the rows already covered.
* `tests/test_posttraining_safety.py::test_the_evaluator_refuses_a_split_the_manifest_did_not_freeze`
  builds a content-bound (schema 3) manifest; its assertion (`"does not contain"`) is unchanged.
* `tests/test_verifier_soundness.py::test_every_draw_keeps_its_source_including_successes` was
  rewritten to assert the *property* (the source is stored outside the failure branch) instead of
  the absence of an `if not row["exact"]:` branch — that branch is required by
  `tests/test_posttraining_safety.py` and now classifies WHY a draw failed
  (`compile-error` / `certificate-unverified` / `relocation-only` / `code-differs`).

---

## 5. What I did NOT verify

* **No GPU, no model inference.** I did not re-run the adapter evaluation. The
  "4 → 19 exactly, `lost=[]`, 54 calls per arm" re-measurement and the dataset rebuild are the
  parent workstream's results, reported to me; I did not reproduce them, and this document does
  not claim them as mine.
* **The pre-fix replay is a reconstruction, not a checkout.** These files are untracked, so the
  "before" run restores each pre-fix implementation verbatim from the audit snapshot into a
  scratch tree. It demonstrates that each guard is load-bearing; it is not a `git stash` of a
  committed revision.
* **No end-to-end evaluator run.** `_write_draw_artifacts`, the per-draw certificate verdict and
  the artifact references are unit-tested and wiring-tested, but no live `evaluate_arm` run has
  executed them here.
* **The recorded run's verdicts were not recomputed.** The exactness fire tests compile fresh
  units with the real recipe; they do not re-certify the artifacts of the September 20 run.
* **Only the five relevant test files were run**, not the whole repository suite (619+ tests at
  the last status snapshot).
* **`record_hashes` bind a specific BUILD, not only the experiment** — see §2.1. Stated there
  rather than here so it reads as a property of the design and not as a caveat.
* **The `train`/`dev` refusal is on the gate and the loader boundary**, not on the dataset
  builder: `freeze_manifest` still writes whatever split assignment it is given (it refuses ids in
  neither `train` nor `test`), and the refusal happens where a promotion could be authorised.
* **No semantic change to the re-exported names after integration.** `eval/evaluate_source_repair`
  re-exports `load_frozen_tasks`, `manifest_content_sha256`, `task_prompt` and the seven
  `verify_*` functions from `eval/frozen_manifest`; those names and their semantics are unchanged
  since that wiring landed. What changed afterwards is documentation only in
  `eval/frozen_manifest.py` (§2.1), plus the gate's *internal* condition key names
  (`R4_panel_fully_covered`, `R5_budget_executed`) so they match the rule text the outcome already
  published, and the test assertions that read them.
