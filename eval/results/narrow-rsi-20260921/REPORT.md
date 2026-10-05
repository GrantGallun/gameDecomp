# Narrow RSI: bounded two-generation loop, implementation and pilot

Specification: `docs/deepseek-narrow-rsi-next.md`. Date: 2026-09-21.
Extends the action-policy work in `eval/results/tool-action-20260921/` and preserves its
observation/state fixes (they are the downstream learner's interface).

**Strongest evidence level reached: L0. L1 was measured and is negative. L2 was not attained — S1 was
built and frozen, and the gate kept S0 because the paired panel showed no gain. L3 was not
implemented.** Each is justified below from receipts, not from intent.

## 1. What runs, and what it delegates to

The coordinator (`eval/rsi_loop.py`) is a resumable state machine, not another decompilation engine:

```
frozen -> research -> verify -> assemble -> candidate-frozen -> evaluate -> decide -> done
```

| stage | delegates to | receipt |
|---|---|---|
| frozen | `eval/generation_manifest.freeze` | `generations/S0.json` |
| research | **`eval.local_research.Research`** — the existing live Ollama+IDO worker, now given a real failure cluster | `stages/research.json` |
| verify | the worker's own notebook + its `verdict()` | `stages/verify.json` |
| assemble | `eval/rsi_interventions.intervention_from_finding` | `notes.jsonl` |
| candidate-frozen | `generation_manifest.freeze` with `parent=S0` | `generations/S1.json` |
| evaluate | `eval/rsi_transfer.paired_transfer` | `stages/evaluate.json` |
| decide | **`eval.posttraining_gate.decide`** — the project's existing R0–R5 gate | `stages/decide.json` |

Supporting modules: `eval/research_demand.py` (failure clusters), `eval/budget_ledger.py` (one
experiment-wide ledger), `eval/rsi_interventions.py` (memory notes + declarative compositions),
`eval/generation_manifest.py` (content-hashed generation manifests), `eval/narrow_rsi.py` (the process
the dashboard starts), `eval/rsi_control.py` + `/api/rsi` (Start/Stop + progress panel).

Nothing in the build path, the KB evidence tier or the production ratchet is touched: `decide` only
READS the match count (`functions_with_an_exact_attempt: 334`), and no adapter is promoted.

## 2. The cycle that ran, with its numbers

`bash`-reproducible with the commands in §7. Final state: `stage=done`, `generation=S0`,
`candidate=null`, `evidence_status=confirmed`, gate `verdict=ineligible`.

| stage | result |
|---|---|
| frozen | S0 manifest, 12 artifact files hashed, fingerprint `687796e7b3b8a31c` |
| research | cluster `b7bd868662d0` = **"cfe: Error: Syntax Error" / no-diff / large: 5,142 attempts over 319 functions**; 6 model calls, 8 compiles, 1 **confirmed** of 2 experiments |
| verify | finding: *"Bitwise AND versus Bitwise OR"*, metric `andi_ops`, relation `less`, confirmed on 3 parameter values |
| assemble | 1 memory note **confirmed** — the applicability test fired against the motivating cluster's own signature `{error_class: "cfe: Error: Syntax Error", residual_kind: "no-diff", size_bucket: "large"}` |
| candidate-frozen | **S1 frozen**, parent S0, fingerprint `cbf16f453ff79b13`, 1 confirmed note; weights and adapter identical, so the manifest names a memory-only change |
| evaluate | baseline vs intervention, 6 functions, 5 actions, both arms 30 decisions, `acceptable_rate 0.8333`, `compile_successes 1`, **certified 0 both**, **`notes_enabled: 1`, retrieval fired on 24 of 30 decisions** |
| decide | `ineligible` — not the frozen test split (dev), 6 of the 12 required tasks, no task gained |
| budget | spent 6/12 model calls, 32/72 compiles, 111 s of 2400 s |

The evaluate row is the one that matters: the intervention was **active** (a note was retrieved into the
observation on 24 of 30 decisions) and changed **nothing measurable** — every delta is exactly 0, with
identical candidates seen, identical compile successes and zero certified matches in both arms. That is
a real negative result about this note on this panel, which the previous run's identical-arms
comparison could not produce.

## 3. Evidence levels, stated honestly

- **L0 — working loop: YES.** One complete cycle ran: real stored failure → deterministic cluster →
  local researcher → controlled probe experiment → confirmed finding → confirmed intervention → frozen
  child generation S1 → paired transfer comparison with the intervention demonstrably active → project
  gate → receipts, events, budget ledger, generation manifests. Resume is idempotent by stage receipt,
  and the stop contract is wired to the dashboard.
- **L1 — autonomous transfer: MEASURED, NEGATIVE.** The two arms differed by exactly the confirmed
  note, retrieval fired on 24 of 30 intervention decisions, and the delta is 0 on every measure:
  certified matches 0/0, `acceptable_rate` 0.8333/0.8333, candidates 9/9, compile successes 1/1. The
  gate independently rules the panel incapable of authorising anything (dev split; 6 tasks where 12 are
  required), so this is not a promotion attempt either way. A null with the mechanism verified active is
  a result; the earlier identical-arms null was not.
- **L2 — two-generation improvement: NOT ATTAINED.** S1 was built and frozen, which is the part of the
  round that can be demonstrated on this budget; the gate kept S0 as active because the fresh paired
  panel showed no gain. The generation pointer correctly did not advance, and there was no second
  research round.
- **L3 — better researcher: NOT IMPLEMENTED.** The hook that makes it meaningful exists — a
  generation's researcher can be given the generation's verified memory through the same manifest —
  but the two-researcher diagnostic was not built or run, and no claim is made about it.

**The single most important substantive result:** the confirmed finding is about *bitwise AND versus
OR*, which has nothing to do with the motivating cluster (`cfe: Syntax Error` in m2c drafts). The
researcher confirmed something easy and true, not something that addresses the failure it was shown.
That is the mechanism to fix next, and it is a finding about the research contract, not about the
model: the probe format asks for a falsifiable code-generation comparison, so the researcher gives one;
nothing in the loop requires the confirmed finding to *bear on the cluster*.

## 4. Five defects found by running it, four of which would have produced a false result

Every one of these was caught by executing the loop or by the test subagent, and each is now fixed and
pinned:

5. **The intervention path was dead code, and the first pilot's L1 null was structural.** The module's
   docstring said "Only `confirm()` flips it to `confirmed`" and `confirm()` existed nowhere in the
   repository; `notes_for()` — the compatible-state retrieval — was written and never called by the
   loop, which instead assigned every note to the policy at once. So no note could ever be confirmed,
   `candidate-frozen` could never freeze a child, the evaluation always filtered to zero notes, and the
   two arms were identical by construction. The run reported `notes_enabled: 0` and a 0-vs-0 delta, and
   I wrote that up as deliberate restraint ("a proposal is not a behaviour change") when it was an
   unimplemented function. Fixed with `confirm_applicability` (which declares that it establishes
   retrieval for a state class, **not** effect) and `NoteRetrievalPolicy` (per-decision retrieval, with
   the retrieval log recorded in the transfer rows). `tests/test_rsi_interventions.py` pins the whole
   path: a proposal is never retrieved, a wrong-scoped note is refused and stays proposed, retrieval is
   per state, and an empty note set retrieves nothing so a null cannot be faked.

1. **The paired transfer closed its database before the arms ran** (`paired_transfer`), and the
   contexts' `compile_fn` closures hold that connection. Five of six functions failed in BOTH arms with
   `Cannot operate on a closed database` — a symmetric failure that looks exactly like a tie. This is
   how a harness bug becomes a published null. Fixed by keeping the connection open for the whole
   comparison; the gate's R4 (both arms ran every declared task) is the check that would have caught it.
2. **A relative `--root` made every probe path relative**, so the compiler subprocess resolved
   `probe.c` against a different working directory: `FileNotFoundError`, first probe, recorded
   `compile_failed`, and the loop reported `evidence_status: refuted` — a capability-shaped null caused
   entirely by a path. Fixed by resolving all derived paths; the research stage then confirmed a finding
   on the same budget.
3. **`research_demand` counted attempts by counting members**, each re-queried from the function's best
   attempt, so one function with 2,160 attempts dominated the queue and its examples repeated. Fixed to
   one member per function with real attempt counts. That immediately exposed a second flaw: ranking by
   raw attempts schedules research into the campaign's *retry policy*, so the scheduler now ranks by
   breadth (distinct functions) first and logs that heuristic rather than presenting it as learned value.
4. **`generation_manifest.verify()` skipped the `training` group**, so a tampered training artifact
   verified clean; and **`cluster_id` used salted `hash()`**, so the same cluster got a different id on
   every run while the queue is written to disk and cited by id. Both fixed, both now tested.

Also from the test subagent: the ledger docstring promised an evaluation-first reservation that did not
exist as code. It does now (`reserve_evaluation`), charging dedicated `evaluation_calls` /
`evaluation_compiles` caps — which is what makes "research cannot spend the panel's capacity" real
rather than prose.

## 5. Budgets and resource caps

One ledger (`budget.jsonl`), opened before any work, replayed on resume so a resumed stage cannot
re-spend: 12 research model calls, 72 research compiles, 60 evaluation calls, 144 evaluation compiles,
2400 s wall, shared across every stage and generation. Reservations precede work; `guard_seconds`
stops stages past the wall cap; `--minutes` from the UI may only LOWER the configured cap. No paid API,
no model download, no weight promotion, no campaign job touched; `nvidia-smi` was not used to reclaim
anything and the GPU cap stayed at the project default.

## 6. What the receipts do NOT establish

- That the memory intervention helps or hurts. It was enabled and active on 24 of 30 decisions and the
  delta was 0 on every measure — so what the receipts establish is "no measurable effect of this note
  on this panel", which is not the same as "no effect", and says nothing about any other note.
- That the researcher cannot address real failures. It was shown one cluster and produced one
  unrelated confirmed finding; the contract, not the capability, is what failed.
- Anything about L2 or L3.
- That the demand clusters describe solver capability: the two largest "do-while loop" clusters are
  partly an artifact of `solver/workspace.bootstrap` rewriting `build.sh` to strip the do-while ban, and
  the largest cluster is dominated by m2c draft syntax errors — infrastructure and intake, not
  generation quality. Attribution belongs to the shared baseline before it belongs to the researcher.
- That 6 functions and one confirmed finding say anything statistical. They do not.

## 7. Exact commands

```bash
# 1. failure clusters from real attempts (WSL; read-only KB; held-out names are excluded and the
#    written artifact is re-checked with whole-name matching)
cd /mnt/c/Code/gameDecomp && PYTHONPATH=$PWD python3 -m eval.research_demand

# 2. the bounded cycle, absolute root (see defect 2 above)
cd /mnt/c/Code/gameDecomp && PYTHONPATH=$PWD SOLVER_GPU_MEMORY_FRACTION=0.75 \
  /home/grant/decomp/train-venv/bin/python -m eval.rsi_loop \
  --root /mnt/c/Code/gameDecomp/eval/results/narrow-rsi-20260921 \
  --config eval/results/narrow-rsi-20260921/config.json

# 3. the same thing as the dashboard starts it (writes rsi.pid, honours STOP)
python -m eval.narrow_rsi --state <root> --stop <root>/STOP --pid <root>/rsi.pid --run-id <uuid> --minutes 20

# 4. one stage only
python -m eval.rsi_loop --root <root> --stage research

# 5. tests
python -m pytest tests/test_rsi_foundations.py tests/test_rsi_dashboard.py \
                 tests/test_tool_boundary.py tests/test_tool_action_dataset.py -q
#    -> 78 passed, 1 xfailed, 2 xpassed
```

Dashboard: `GET /api/rsi`, `POST /api/rsi/control` (`start`/`stop`) on the existing progress app; the
panel shows stage, generation, hypothesis, evidence status, budget bars, coverage and gate reason.
Stop writes `STOP` and signals only the recorded process group.

## 8. Human versus machine authorship, and provenance

The local researcher (qwen2.5-coder:14b via Ollama, digest-pinned per call) authored the hypothesis,
the two probe templates and the prediction; its confirmed status comes from the worker's own
`verdict()` over three compiled parameter values. The framework — clusters, manifests, ledger,
interventions, transfer, gate wiring, dashboard — is machine-generated under this specification. I did
not author a task-specific rule, edit a candidate after seeing a held-out failure, or select among
several packages: exactly **one** package was produced and it was not adopted. No such intervention is
counted, because none occurred.

Artifacts: `generations/S0.json`, `stages/*.json`, `notes.jsonl`, `budget.jsonl`, `events.jsonl`,
`research/state/runs/<id>/` (per-call requests and responses, compile events), `receipt.json`.
Superseded runs r1–r3 are preserved under `superseded-r1/`, `superseded-r2/`, `superseded-r3/` rather
than deleted — r3 is the run whose research stage was defeated by the relative-path defect.

## 9. The next discriminating experiment

Fix the research contract, not the budget. Concretely: require the proposal to name the cluster it
addresses and to predict an effect **on candidates from that cluster** (real stored attempts are
already in the demand queue, and the worker can compile a real candidate instead of a synthetic
probe); score a finding as addressing the cluster only if its measured effect holds on at least one
cluster member and its counterexample search runs on members rather than on unrelated probes. Until
that exists, a confirmed finding carries no information about the failure that motivated it — which is
exactly what this pilot measured, and the cheapest next thing to change.
