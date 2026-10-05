# Executing `docs/deepseek-next-experiment-20260921.md` — defects, measurements, and what is left

> **CONTINUED.** The follow-up half of this round is in
> `eval/results/dev-set-20260921/RESULT.md`: a safety defect in `opaque_variant` (3 fires, 3 candidates
> destroyed → 0 and 0), a wiring defect that made the registry catalog look empty, a residual reading that
> named register allocation, and — from that reading — **one function closed to exact and verified**
> (`__MusIntProcessWobble`, 97.045 → 100.0, header-assisted, 1 of 17 states closed and 8 of 17 improved).
> Machine receipt: `eval/results/dev-set-20260921/ROUND2.json`.

Round of 2026‑09‑21. Every number here has a receipt in this directory or in
`eval/results/intake-20260921/` and `eval/results/dev-set-20260921/`. No model was called, no paid API is
enabled, and nothing was promoted.

---

## 1. Defects reproduced against the current code, and what happened to each

The audit's own probe script was copied here as `audit_probes.py` with **one** change (its output file
name), so the before (`eval/results/codex-audit-20260921/reproductions.json`) and the after
(`audit-probes-after.json`) are the same program reading the same code paths.

Command: `python eval/results/audit-fixes-20260921/audit_probes.py`

| audit finding | before | after | receipt |
|---|---|---|---|
| `rounds: 2` ignored: 7 stages, one generation, round 1 twice | `stage=frozen`… `round: 1`, `S0`/`S1` hardcoded | **14 stages, two rounds**, `generation S2`, `parent S1`, `round 3`; resume returns the same state | `audit-probes-after.json:two_rounds_and_resume` |
| Generation-agnostic receipt paths | round 2 skipped every stage as "resumed" and republished round 1's verdict | receipts are `stages/r{round}/{stage}.json` + provenance; a receipt from another round/config is **rejected**, not reused | `tests/test_rsi_coordinator.py` |
| Parent pointer never advanced | `parent: null` forever | `parent` written on accept, and the accepted candidate becomes the active generation | same |
| Spec derived from setup results | 13 declared / 12 usable / 1 error row → **`promote`** | **`ineligible`**; the gate is handed the FROZEN spec, and an error row is a row with no draw count (R5 fails by the existing rule) | `incomplete_panel_gate`; `tests/test_rsi_coordinator.py::test_a_declared_panel_with_a_setup_failure_cannot_promote` |
| Research exclusion applied to the evaluator | `eligible_panel([t1,t2], held_out={t1,t2})` → kept **0 of 2** | research keeps 0; **evaluation keeps 2** — the isolated evaluator may execute the frozen split | `test_panel_selection`, `test_panel_selection_evaluation_purpose` |
| `Caps(compiles=10)` reachable to 16 spent | `spent.compiles = 16`, `remaining = -6.0` | **refused at `reserve two=8`**: "would be committed to 16.000 against a cap of 10"; `remaining()` gains `reserved`/`available`; only `record_overrun(stage, reason=…)` may cross a cap | `reproduce_budget.py` (exit 0), `budget-reproduction.json` |
| `verify()` cannot verify the shapes the freezer emits | `IsADirectoryError` on a directory model, `KeyError: 'path'` on an inline prompt | three-state report (`verified`/`violated`/`unverified`); a directory+file-list model verifies; an inline digest is **named unverifiable**, never folded into a pass; `compositions` is now checked; hash modes are honoured | `reproduce_manifest.py` (exit 0), `reproduce_manifest.json` |
| Truncated diagnostics reported as complete | `errors[:40]` and `diagnostics[-16000:]` were silent | `error_count`, `errors_truncated`, `diagnostics_truncated` on every report; the probe labels a class set read off a window | `tests/test_frontend_diagnostics.py` (5 new tests) |
| Parent/child observation boundary | rejected child's diff/verdict under the parent's hash | fixed and re-verified: `reproduce_boundary.py` prints DEFECT NOT PRESENT and exits 0 | `reproduce_boundary.py` |

Two further defects were found while fixing these, both by the manifest work:

* **`stage_frozen` declared the wrong verifier.** `verifier={"path": eval/tool_agent.py,
  **hash_artifact(solver/workspace.py)}` spread `path` *after* the declared one, so the manifest named
  `workspace.py` as the verifier and `tool_agent.py` appeared nowhere. Now two explicit identities
  (`loop`, `oracle`), neither overwriting the other.
* **An absent component could not be distinguished from a lost one.** `{"exists": False}` was emitted for
  both "no notes file yet, which is normal at S0" and "a file that was there has vanished", so every S0
  either raised a false alarm or passed unverifiably. The emitter now records `absent_because`, and the
  rule is: absent + stated intent → checked; absent + no intent → **violated**; appeared since freeze →
  violated. `tests/test_rsi_foundations.py` passes unchanged, which is the evidence the rule is the one
  the project already pinned.

## 2. The corrected intake table, three levels, one frozen frame

200 states, identical membership and **zero draft-hash drift** between the first and last receipt below.
`eval/results/intake-20260921/_levels.py` recomputes it from the recorded rows; the two earlier receipts
carry the frontend level in `diagnostic_trace[-1]`, the new one carries it as a field on every row.

| receipt | what changed | IDO compiled | IDO **+ frontend** | byte-exact |
|---|---|---|---|---|
| `wide-intake-traced.json` | baseline sequence | 20 | 16 | 2 |
| `wide-intake-undeclared.json` | + `undeclared_identifiers` | 27 | **16** | 2 |
| `wide-intake-orfix.json` | + `or_address` | 29 | 18 | 2 |
| `wide-intake-acceptance2.json` | + `source_type_declarations` fixes | **32** | **19** | **2** |

**The audit's central numerical claim is confirmed and sharpened**: the `undeclared_identifiers` step
moved IDO 20 → 27 while the frontend level did not move at all (16 → 16). Those seven candidates compile
while clang still rejects them, so they are progress and not repair. The steps added after the audit
(`or_address`, `source_type_declarations`) moved **both** levels.

Per-tier and per-action breakdown, plus the residual class census on the final candidate, are in
`wide-intake-acceptance2.json`. One acceptance set is now reported, and `harness_clean: true`.

Per size tier (IDO compiled / **+frontend** / exact of n):

| tier | n | IDO | +frontend | exact |
|---|---|---|---|---|
| tiny (<20) | 0 | 0 | 0 | 0 |
| small (<60) | 2 | 1 | 1 | 1 |
| medium (<150) | 34 | 14 | 11 | 1 |
| large (<300) | 57 | 11 | 4 | 0 |
| huge (≥300) | 107 | 6 | 3 | 0 |

The lever is still concentrated in `medium`: 11 of the 19 frontend-passing candidates are there, and the
frontend level falls away faster than the IDO level as size grows (large 11 IDO → 4 frontend, huge 6 → 3).

## 3. Phase B — the development set

`eval/dev_set_export.py`, run in WSL:

```
python -m eval.dev_set_export --frame eval/results/intake-20260921/wide-intake-acceptance2.json \
  --kb ~/decomp/kb-sbk1.sqlite --out eval/results/dev-set-20260921/dev-set.json \
  --sources eval/results/dev-set-20260921/sources
```

* **17 selected** (compiles + frontend passes + not exact), 183 excluded with a named reason, **0
  unrecoverable**. The audit's 14 became 17 because `or_address` and `source_type_declarations` added
  three frontend-passing candidates after those receipts were written.
* **Exact-byte provenance, no replay.** Every entry's source is recovered from the attempt log by the
  `sequence.final_sha256` the probe recorded, written to disk, re-read, and re-hashed. A row whose bytes
  cannot be produced is reported as `unrecoverable` and fails the export (exit 1) — a fresh m2c draft is
  never substituted.
* **Fresh results, not recorded ones.** All 17 were recompiled: **17 of 17 reproduce the recorded score
  exactly** (`dev-set.json`, `entries[].fresh`), which is the strongest available check that the recovered
  bytes are the measured bytes.
* **Assistance tiers are in the artifact**: 6 `binary-only`, 10 `header-assisted`, 1
  `reference-source-assisted` (`updateFallingActionProjectileLanded`, whose sequence needed a declaration
  recovered from the target's own `src/`). Every entry is `training_eligible: false` with the reason
  recorded, and the payload's `regime` says it is development data and never a sealed test set.

## 4. Phase B — bounded branching versus the fixed policy

`eval/bounded_search.py`. Depth 2, beam 3, **40 compiles per state** (the allowance is explicit and
enforced; every compile is counted). The incumbent is the fixed order's own candidate, so a branch has to
beat the policy, not the draft.

| run | branches leave from | states | compiles used | exact found | improved on the fixed order |
|---|---|---|---|---|---|
| `search-d2-b3.json` | the frozen m2c draft | 17 | 39 | 0 | **0** |
| `search-policy.json` | the fixed order's own candidate | 17 | **3** | 0 | **0** |

Two findings, and the second is the one that explains the first:

1. **No alternative sequence beats the fixed order.** Branching from the same draft the policy started
   from, across 17 states and a 9-action catalog at depth 2, every state's best branch *is* the policy's
   candidate (`sub_outcome: no-improvement`, 17/17).
2. **The finished candidates are fixed points of the catalog.** Branching from the policy's own output,
   **14 of 17 states admit no move at all**, and the whole panel cost 3 compiles. The only action that can
   still act is `opaque_variant`, on 3 states — and on all 3 it **destroys the candidate**: score → 0.0,
   `compiled: false`, 1544 / 4526 / 4481 chars. It is the same `} void;` defect the previous round
   documented, seen from the other side.

**Classification, in the brief's terms:** `unresolved capability/search problem` (17/17) — explicitly
*not* proof of impossibility, and *not* an infrastructure result. No state reached `exact`, so there is no
policy-learning and no composition-learning opportunity to report from this panel, and **there is no
executable solution path within this catalog at depth ≤ 2**.

Cost, complete: 42 compiles for both search arms together, 32.2 s + 16.8 s of wall clock, 17 context
builds per arm. **Coverage is 17 of 17 selected states in both arms; nothing was skipped, and no state is
missing from either receipt.**

## 5. Assistance tiers, and what they do not license

| tier | states in the panel | what it means |
|---|---|---|
| `binary-only` | 6 | the winning sequence used only binary-derived repairs |
| `header-assisted` | 10 | `header_variant`/`globals_variant` supplied a declaration from `include/**` |
| `reference-source-assisted` | 1 | `source_type_declarations` copied a declaration from the target's own `src/*.c` |

A match leaning on either of the last two is never `SOLVED` and never binary-only evidence. None of the 17
is a match at all — they compile and pass the frontend — so the tiers here describe the *input* to a
future repair rather than any result.

## 6. The researcher (Phase C): what exists, and what does not

* A researcher-authored intervention **exists as a note**: the round recorded in
  `eval/results/narrow-rsi-20260921/` produced a confirmed synthetic finding and one proposed memory note.
* **It did not transfer.** The paired comparison's certified delta was 0, and the intervention's own
  activity was reported (`retrieval.decisions_with_a_note_retrieved`) rather than assumed.
* **No S2 round was launched.** The brief allows S1 to research S2 only after a real transferable S0→S1
  gain, and there is none.
* The receipts in that directory are **mixed**, and are now documented rather than rewritten:
  `eval/results/narrow-rsi-20260921/PROVENANCE.md` records that the ROOT run's stage events and ledger
  live in `superseded-r3/`, that three different S0 identities exist (differing only in an evaluator hash
  and a relative-vs-absolute path), and that `REPORT.md` cites a fingerprint from one and a budget from
  another. The negative result itself is unaffected: the ROOT's `evaluate`/`decide` pair is internally
  consistent.
* Nothing in this round counts as an autonomous discovery by the measured local researcher. The
  coordinator fixes are framework work and are labelled as such.

## 7. What is left, concretely

1. **`opaque_variant` must not be able to destroy a compiling candidate.** Measured: 3 of 3 fires on
   finished candidates produce uncompilable C. This is a safety property, not a score question, and it is
   the smallest remaining change that can only help.
2. **The catalog is exhausted at the finish line.** 14 of 17 finished candidates admit no action at all.
   The remaining distance is codegen (branch shape, instruction selection), which no current action owns.
   The honest next experiment is to take the highest-scoring panel member (`__MusIntProcessWobble`, 97.045)
   and the nearest large one (`updateRacePlayerAirborneLaunch`, 94.822) and read their diffs by hand to
   name the codegen shape, before writing any pass for it.
3. **Inline prompt/schema digests are unverifiable by construction.** The emitter should embed the prompt
   text and the tool-schema JSON so the digest is recomputable; this must not be done to the existing
   historical S0 manifests.
4. **Charging is per stage, not per invocation.** The ledger supports per-operation accounting with
   running totals, but `rsi_loop` and `paired_transfer` still report once, after the work. An interruption
   before that report is uncharged until the caller re-reports.

## 8. Receipts and commands

```
python eval/results/audit-fixes-20260921/audit_probes.py           # the audit's probes, before/after
python eval/results/audit-fixes-20260921/reproduce_budget.py       # 10-cap/16-spend, interruption
python eval/results/audit-fixes-20260921/reproduce_manifest.py     # every shape the freezer emits
python eval/results/audit-fixes-20260921/reproduce_boundary.py     # parent/child state transition
python eval/results/intake-20260921/_levels.py                     # the three-level table
bash .cache/recon/wide_acceptance2.sh                              # the 200-state measurement (WSL)
bash .cache/recon/dev_set_export.sh                                # the development set (WSL)
bash .cache/recon/bounded_search.sh 0 2 3 40 search-d2-b3 draft    # search from the draft (WSL)
bash .cache/recon/bounded_search.sh 0 2 3 24 search-policy policy  # search from the policy (WSL)
python -m pytest tests/test_rsi_coordinator.py tests/test_bounded_search.py tests/test_dev_set_export.py \
  tests/test_budget_ledger_enforcement.py tests/test_generation_manifest_shapes.py \
  tests/test_frontend_diagnostics.py tests/test_intake_frame.py tests/test_rsi_foundations.py \
  tests/test_tool_boundary.py tests/test_tool_action_dataset.py tests/test_rsi_interventions.py \
  tests/test_end_to_end_transition.py tests/test_m2c_or_address.py tests/test_source_type_declarations.py \
  tests/test_m2c_placeholders.py tests/test_m2c_negative_offset.py -q
  -> 202 passed, 2 skipped, 1 xfailed, 2 xpassed
```

`tests/test_end_to_end_transition.py` is the "one small, deterministic end-to-end transition" the brief
asks for before model work: it drives the real `run_episode`, resolves the action through the real
`eval.tool_registry.ACTIONS` table, calls the registered runner (not a stub) on a real placeholder draft,
and asserts that the adopted child becomes the context, that a rejected child leaves the context's
candidate **and diff** untouched while its own verdict stays under its own identity
(`step.detail["attempted"]`, `attempted_sha256`), and that the transcript serialises. Its first version
used `arg0[?]` as the placeholder form; the resolver correctly declined it (`no-change`,
`placeholders: []`), which the assertion caught — the test now uses the form m2c actually writes
(`? sp1C;`).

Code changed: `eval/rsi_loop.py`, `eval/rsi_transfer.py`, `eval/budget_ledger.py`,
`eval/generation_manifest.py`, `eval/intake_probe.py`, `eval/intake_runners.py`,
`solver/frontend_diagnostics.py`; new: `eval/dev_set_export.py`, `eval/bounded_search.py`.
Tests added: `tests/test_rsi_coordinator.py` (17), `tests/test_bounded_search.py` (8),
`tests/test_dev_set_export.py` (9), `tests/test_end_to_end_transition.py` (4),
`tests/test_budget_ledger_enforcement.py` (10), `tests/test_generation_manifest_shapes.py` (15), plus 5 in
`tests/test_frontend_diagnostics.py`.

`ROUND.json` in this directory is the round's single machine-readable receipt: it re-reads the measurement,
the development set and both search arms from their own files and adds nothing of its own.
