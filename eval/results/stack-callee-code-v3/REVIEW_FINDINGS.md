# External review findings — 2026-08-27

An outside review of this project raised nine concrete defects. **All nine were
verified true against the code.** They are recorded here because the project's
own rule is that a finding is fixed, or whitelisted with a written reason, never
silently tolerated.

## Correctness (fix before running more experiments)

### 1. `run_permuter` can report a false EXACT — CRITICAL

`solver/pipeline.py::run_permuter` parses a score out of a permuter output
*directory name* and sets `exact = True` at >= 100 without the oracle ever
confirming it.

This violates the project's foundational invariant — byte-exact object
comparison is the only source of truth. A false positive is worse than a missed
match, because it silently poisons every number downstream and the ratchet would
happily lock it in.

**Fix:** every permuter result goes back through `workspace.score()`. Trust the
oracle's `Verified exact match`, never a filename.

### 2. Candidates >= 95% bypass workbench diagnosis

`solver/pipeline.py` routes the `permute` band straight to the permuter;
`diagnose.run` is only called on the `retype`/`reshape` branches.

This is the exact failure this project discovered independently: a candidate can
score 99.999% with *zero instruction differences* and still not be byte-exact,
because its relocations name the wrong symbol. The workbench has a verdict for
it (`words-identical` -> `relocation-only`). Routing that candidate to the
permuter applies a tool that only permutes register allocation and can never
touch a symbol reference.

**Fix:** diagnose every compiled, non-exact candidate. Route by verdict; use the
score band only as a fallback when no verdict is available.

### 3. Trajectory logging is corrupted

`solver/refine.py::log_attempt` hardcodes `{"temperature": 0.2}` while the
pipeline samples at 0.7, and the pipeline passes `wall_ms=0` for every attempt.

TRAINING.md's whole premise is that the `attempts` table becomes the refinement
dataset. Rows carrying the wrong sampling parameters and no timing are unusable
for exactly the purpose they exist for. Every run so far has been writing
damaged training data.

**Fix:** pass real sampling parameters and real wall time. Add a run id so
iteration numbers do not restart and collide across invocations.

## Reproducibility

### 4. Experiments are not reproducible

Resume keys only on function name; the output filename encodes model and a
couple of flags but not sample count, temperature, prompt version, solver
revision, or seed. Results from different implementations can silently mix in
one file.

**Fix:** an `experiments` table with a configuration fingerprint — code
revision, eval-set hash, model digest, seed, sampling params, tool versions.
Refuse to resume when the fingerprint differs.

### 5. Not under version control (FIXED)

Six iterations of "change one variable and measure" with no way to reconstruct
which code produced which result. `git init` done; this is the precondition for
finding 4.

## Evaluation realism

### 6. No `huge` tier

`eval/sets.py::TIERS` stops at 120-300 instructions. The binary has 85 functions
above 300, and they are the hardest.

### 7. `do-while` functions are excluded

`eval/feasibility.py` drops them because the harness's `build.sh` rejects any
`do` token. That was right for *measuring the model* and wrong for *decompiling
the game* — two different goals, conflated. Report harness coverage as its own
number rather than quietly shrinking the population.

### 8. Sibling retrieval assumes a completed repository — SERIOUS

`solver/siblings.py` reads matched source from a 100%-finished decomp, giving
the solver a template library that would not exist mid-project. The risk was
documented in that module's docstring and then every pipeline measurement ran
with siblings enabled anyway. **Writing down a caveat is not mitigating it.**
Every number since iteration 3 is inflated by an unknown amount relative to a
live decomp.

**Fix, and the best idea in the review:** replay the reference repo's git
history. Evaluate each function against the project state *immediately before
humans matched it*, so available headers, siblings and layouts reflect what was
genuinely known then. The 4,015-commit history makes this buildable.

## The thesis is unimplemented

### 9. `kb/tms.py` does not exist

`kb/schema.sql` states twice that invariants 3, 4 and 5 are "enforced in
kb/tms.py". That file has never been written. Citation-enforced inference
writes, transitive retraction, `func_deps` population, the ratchet and the
poison test are all conventions in prose, not properties of the code.

This is the part that makes the project novel, and it is the part that does not
exist.

## Epistemic findings

Two process defects, which matter as much as the code ones:

**Over-claiming.** `workbench-verdict-routing` was marked CONFIRMED on a
10 -> 12 exact delta while mean score fell 69.26 -> 64.07. The review computes a
95% interval on that mean delta of roughly -10.42 to +0.04 — inconclusive. The
significance floor that would have caught this arrived five iterations late.

**No reconciliation.** Two iterations later, `tier-means-underpowered-at-n10`
was recorded as REFUTED — a methodological finding that retroactively
invalidates that CONFIRMED status. The bank held both facts and never reconciled
them. A finding that undermines earlier conclusions must trigger a sweep of the
entries it affects.

## Not raised by the review, but true

- **Held-out has never been run.** Zero held-out numbers exist; everything
  reported is dev.
- **No cost accounting.** Tokens or wall time per match is not tracked as a
  metric, despite TRAINING.md's explicit economic argument.
- **The knowledge base barely contributes.** 72,845 evidence rows exist,
  `solver/context.py` uses a thin slice, and the inference tier is empty. "A
  knowledge base with a solver attached" is currently a prompt pipeline with a
  small fact lookup.

## Order of work

1. `git init` — **done**
2. Downgrade over-claimed bank entries; add reconciliation — **done**
3. Fix false-EXACT permuter path and trajectory logging (correctness)
4. Diagnose every non-exact candidate; route by verdict
5. Build `kb/tms.py` and the poison test
6. Git-history replay evaluation
7. Experiment fingerprints, tests, dependency manifest
