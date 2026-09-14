# Autodecomp — Build Roadmap

Ordering principle: **every phase ends with a number you can look at.** No phase is
"done" because the code exists; it is done when its acceptance check passes.

---

## Status — 2026-08-26

**Phase 0: COMPLETE.** Both target repos build and produce byte-identical ROMs.

Two findings from setup changed the plan materially:

1. **cdlewis's repos already implement much of Phases 0–2.** A mature agent harness
   exists there: guardrail hooks, an Oracle, candidate scoring, m2c drafting, a
   contradiction-sweep equivalent, and 1,102 lines of recorded IDO 5.3 codegen quirks.
   We build on it rather than duplicating it. See "Existing tooling" below.
2. **Snowboard Kids 2 is also 100% decompiled**, not an unsolved target as originally
   assumed. Both games are finished. There is currently **no live target**, and Phase 5
   is rewritten accordingly.

---

## Target strategy (revised)

| Repo | State | Role |
|---|---|---|
| `~/decomp/sbk1` | 100.00% decompiled | **Development + primary eval.** 204 C files, ~2,086 functions, 170 headers, 2,983 annotated symbols. |
| `~/decomp/sbk2` | 100.00% decompiled | **Held-out generalization test.** Same compiler (IDO), same author's conventions, different codebase. 160 C files. |

Two independently completed games sharing a compiler is close to an ideal experimental
setup: develop against SBK1, and SBK2 answers "does this generalize, or did we overfit
to one codebase?" with the compiler held constant.

Choosing a live target is deferred to Phase 5, where it belongs — nothing in Phases 1–4
needs one, because every one of those phases validates against known answers.

## Existing tooling (do not rebuild)

In `sbk1/tools/` and `sbk1/.claude/`:

| Need | Already provided |
|---|---|
| Oracle | `build-and-verify.sh`, `asm-differ`, `data-differ` |
| Invariant enforcement | `block-asm-edits.sh`, `block-splat-split.sh`, `block-sha1-edits.sh`, `block-unverified-commits.sh` |
| Candidate selection | `score_functions.py`, `list_decomp_candidates.py`, `rank_decompile_similar_impact.py` |
| Drafting | `m2c_one_shot.py`, `m2ctx.py`, `tools/claude --bootstrap-only` |
| Contradiction detection | `find_inconsistent_global_types.py`, `find_oversized_symbols.py`, `find_struct_unions.py` |
| Match quality gate | `detect_low_quality_matches.py` |
| Permuter integration | `decomp-permuter` + skill + timeout hook |
| Compiler archaeology | `DECOMPILATION_LEARNINGS.md` (1,102 lines of IDO 5.3 quirks) |

**Our contribution is what is missing there:** a persistent evidence/inference database
with provenance, dependency-tracked retraction, an automatic ratchet, differential
blame, speculative branching, and structured trajectory logging. That is Phases 3–4.

---

## Phase 0 — Target + Oracle ✅ COMPLETE

*Goal: a callable function that scores any candidate C against the real binary.*

**Environment:** WSL2 Ubuntu 24.04, build tree at `~/decomp/` (WSL filesystem, not
`/mnt/c`). IDO 5.3 via `ido-static-recomp`, no Docker. clang 20.1.2, jq 1.7,
binutils-mips-linux-gnu, Python 3.12 venvs per repo.

**ROM provenance:** both dumps verified against the repos' own `.sha1` files —
`1583bacc9046a360df8ea4d536942155247e154c` (SBK1),
`5ce896fd64276948bc2b8cccd8cd51c25a9f32aa` (SBK2). Big-endian `.z64`, cart IDs
`NSKE` / `NK2E`.

### Acceptance results

| Criterion | Target | Measured | |
|---|---|---|---|
| Reference source reproduces the ROM | byte-exact | `cmp` clean, SHA1 matches | ✅ |
| Wrong source is detected | far below | diff score 270, exact instruction localized, ROM hash diverged | ✅ |
| Cold score call | < 2s | **0.59s** (0.45s rebuild + 0.14s diff) | ✅ |
| Cached score | < 50ms | 0.16s no-op make — **no score cache exists yet** | ⚠️ |

The negative control is worth recording precisely: changing `arg0->unk64 = 0` to `= 1`
in `initRaceItemSparkBurst` produced exactly `li t6,1` inserted and `sh zero` → `sh t6`,
and asm-differ localized it to the instruction. The Oracle detects wrongness, not just
confirms rightness.

**Outstanding:** the cached-score criterion is unmet because no cache layer exists —
0.16s is `make`'s own no-op overhead across 470 objects. A content-addressed
per-function cache keyed on (source hash, flags hash) is Phase 1 work. The 0.59s cold
round trip is comfortably inside what Tier 3 RL needs, so this is an optimization, not
a blocker.

---

## Phase 1 — Knowledge Base (rescoped) — IN PROGRESS

*Goal: the existing knowledge, made queryable, with provenance.*

Originally "mine the binary from scratch." Rescoped: splat already produces the
structural data, and `symbol_addrs.txt` (2,983 entries) plus 170 headers already hold
the type knowledge. The gap is that none of it is queryable or carries provenance.

**Tasks**
- [x] `kb/schema.sql` — evidence / inference / support / depends / func_deps / attempts
- [x] `miner/evidence.py` — instruction walk; emit evidence rows with width, signedness,
      class, and a conservatively resolved symbolic base
- [x] `kb/contradictions.py` — the sweep, with bulk-copy detection
- [x] callgraph — emitted as `kind='call'` evidence; `is_leaf` computed per function
- [ ] `miner/import_existing.py` — ingest `symbol_addrs.txt` and headers as *inference*
      rows, seeded with high confidence and provenance "human, pre-existing"
- [ ] `oracle/cache.py` — the missing content-addressed score cache

### Results on SBK1

Extraction runs in **1.7s** for the whole game.

| | |
|---|---|
| Functions | 2,742 (892 leaf) |
| Translation units | 469 |
| Evidence rows | 77,640 |
| — memory accesses | 67,552 |
| — call edges | 10,088 across 786 targets |
| Base resolved | 54.7% (`stack` 34%, `global` 14%, `param0..3` 7%) |
| Base unknown | 45.3% |

The 45% unknown is honest conservatism, not a defect. Those accesses go through
registers (`s0`, `v0`, `ra`) holding pointers whose origin needs dataflow through
loads — beyond the evidence tier. IDO also allocates `$ra` as scratch in functions
that spill it, which surprised the first pass. Invariant 5 says record it as unknown
rather than guess, and that is what it does.

### Acceptance: contradiction sweep — PASSED

Swept 1,469 global and 3,196 in-function param locations. **16 findings initially, 5
after one validated fix, and all 5 are true positives.**

Two design findings came out of investigating them, both verified against SBK1's
known-good source rather than assumed:

1. **Bulk copies poison width evidence.** IDO compiles struct assignment into runs of
   word loads/stores at consecutive offsets. A `sw` landing on offset 0x24 during a
   copy of a nested `Transform3D` does not mean the field there is four bytes wide —
   confirmed against `RaceUiPodiumTrailActor.copyBlock`, whose first member is an s16
   read by a genuine `lh` at the same offset. Detected by pattern (three or more
   stride-4 same-direction word accesses) and excluded. This removed 11 of 16 findings,
   including *every* param-level one.
2. **Signedness disagreement is a cast, not a conflict.** `lh` and `lhu` on one
   location is ordinary C. Downgraded from contradiction to note.

The 5 survivors are genuinely polymorphic memory: `gRaceElapsedTimer` and
`gRaceChallengeTimeLimit` (packed 4-byte time values read both byte-wise and
word-wise), and `__osThreadSave+0x118` in libultra (64-bit `ld` register saves beside
32-bit `sw` fields). Zero false positives attributable to extraction error.

**Identity scoping — important and easy to get wrong.** `global:0xADDR` is a genuine
cross-function identity; `param0` is function-LOCAL and `stack` is frame-local.
Grouping `param0@0x24` across functions would manufacture thousands of meaningless
contradictions. Cross-function param grouping only becomes valid once signatures exist
in the inference tier — and at that point a contradiction there *is* the poisoned-fact
detector Phase 3 needs.

### Acceptance: golden test vs real struct layouts — PASSED

`eval/ground_truth.py` parses SBK1's actual struct layouts (235 structs) and function
signatures (2,098, of which 1,535 take a struct pointer as param0).
`eval/validate_evidence.py` then checks every param access in the KB against the real
field layout and exits non-zero on unexplained disagreement.

| | |
|---|---|
| Param accesses in KB | 4,633 |
| Checkable against ground truth | 2,842 |
| Agree | **2,842** |
| Disagree | **0** |
| Accuracy | **100.00%** |

Coverage gaps are reported honestly rather than hidden: 933 unresolved struct types,
646 offsets past the declared struct, 96 bulk-copy words excluded, 85 without a parsed
signature, 6 landing in pad-only regions.

Getting here took three iterations, and **every bug was in the test, not the
evidence** — see the "How to not ship analysis bugs" section of CLAUDE.md. The most
instructive was brace-depth: matching the first `};` ended each struct at its first
anonymous union, so accuracy read 99.85% while coverage was silently halved. Fixing it
took checkable rows from 1,358 to 2,842. **Coverage numbers deserve the same scrutiny
as accuracy numbers** — a high score on a quietly shrunken denominator is the easiest
way to fool yourself.

**Still open:** the cached-score criterion (<50ms) awaits `oracle/cache.py`.

---

## Phase 2 — Inner loop (rescoped)

*Goal: beat the published single-shot baseline, decisively.*

Rescoped: `tools/claude --bootstrap-only` and `m2c_one_shot.py` already provide the
draft-and-attempt workspace. We add the refine loop and, critically, an eval harness
that does not exist there.

**Tasks**
- [x] `solver/refine.py` — compile → diff → feed back → regenerate, bounded, best-so-far
- [ ] `eval/harness.py` — hold out SBK1 functions, stratified by size and leaf-ness
- [ ] Trajectory logging per [TRAINING.md](TRAINING.md) — full source, prompt context,
      stderr, full diff, model + sampling params. Log this from the first run or the
      training path closes.
- [ ] Contamination check: prompt the base model for held-out functions with no asm
      context; drop and count any it reproduces from memory

**Acceptance**
- Baseline to beat: **~1.2%** byte-exact (published single-shot SFT result)
- Target: **>30%** on leaf functions, **>10%** overall
- Contamination-check result reported alongside every match rate

**This is the go/no-go gate.** If the loop cannot clear the baseline by a wide margin
on leaf functions, the thesis is wrong and no knowledge-base machinery rescues it.
Stop and reassess rather than building Phase 3 on sand.

### Early signal — local models, single-shot, n=8

Local inference via ollama on an RTX 5080 (16GB). Scored by the repo's own per-function
oracle. **No refine loop yet** — one attempt per function, so this is a floor.

| Model | Exact | Compiled | Mean (compiled only) | tok/s |
|---|---|---|---|---|
| gpt-oss:20b | **3/8 (37.5%)** | 6/8 | 88.0% | 114 |
| qwen2.5-coder:14b | 1/8 (12.5%) | **8/8** | 76.0% | 52 |

They split along an interesting axis: gpt-oss lands more exact matches but writes C that
sometimes does not compile; qwen always compiles but lands fewer exact matches. Both of
gpt-oss's failures were the same genuine C89 error — using a struct type in an `extern`
on line 4 before defining it on line 8 — which one refine iteration would fix, since the
compiler names the line.

**Mean score is the wrong headline metric.** In matching decomp, 96% is a failure; only
100% counts. Exact rate is the number that matters, and mean-including-failures unfairly
punishes gpt-oss for two fixable compile errors.

**Do not treat this as the gate passed.** n=8, all small leaf functions — deliberately
the easiest tier — and the published 1.2% baseline was measured over a much broader
function mix, so the comparison is not apples-to-apples. What it does establish is that
the approach is alive and worth building the loop for.

### A/B: sequential refinement vs best-of-N — refinement FALSIFIED

Same 8 functions, same model (gpt-oss:20b), same budget (4 generations each). Only the
allocation of compute differs.

| | Sequential refine | Best-of-N |
|---|---|---|
| Exact | 3/8 (37.5%) | **6/8 (75.0%)** |
| Gained by iterating | **0** | **4** |
| Mean best score | 84.19% | **96.20%** |
| Mean draws to a match | 1.0 | 1.7 |

**Diff-guided sequential refinement contributes nothing.** Across 8 functions it rescued
exactly zero — every match it found landed on the first attempt. The feedback quality was
not the problem: the diffs are small and precise (one was 457 bytes and stated plainly
that the target indexed a 20-byte struct while the attempt used 24). The model simply
cannot localise "which source change produces this instruction change", so a diff makes
it thrash rather than converge.

Best-of-N works for a specific, measurable reason: the same function resampled from the
same prompt scored 87.61%, 82.42%, then 100%. **The right answer is already inside the
model's distribution.** Sampling reaches it; arguing with the model does not. Four of the
six matches came from a draw after the first.

This is Tier 1 of [TRAINING.md](TRAINING.md) — "best-of-N against the verifier is
training-equivalent capability for zero training cost" — confirmed empirically. It also
raises the bar the other tiers must clear: any fine-tune must now beat 75% on leaf
functions, not 37.5%.

An earlier design failure worth keeping: the first refine loop fed back the LATEST
attempt rather than the BEST, and one bad step poisoned every step after it
(87.61 -> 71.00 -> 23.60). Best-anchoring stopped the collapse but did not produce gains,
which is what made the falsification clean rather than confounded.

**Hardware notes:** WSL must be capped (`.wslconfig memory=8GB`) or it and ollama fight
over RAM and WSL fails to boot. Write that file BOM-free — PowerShell 5.1's
`-Encoding utf8` emits a BOM that WSL's parser rejects.

---

## Phase 3 — Truth maintenance + ratchet

*Goal: make bad facts survivable. This is the first phase that is genuinely novel.*

**Tasks**
- [x] `kb/tms.py` — inference writes with mandatory evidence citation; reject uncited
- [ ] `kb/retract.py` — transitive retraction; demote and rebuild affected functions
- [ ] `kb/ratchet.py` — savepoint / apply / rebuild / compare / rollback
- [ ] `func_deps` population from the refine loop
- [ ] Do-not-repropose set for rejected deltas

**Acceptance — the poison test**
1. Start from a KB with N matched functions
2. Deliberately corrupt one field type several matched functions depend on
3. The system must **detect**, **localize**, and **auto-retract** it, returning to N
4. Record mean time-to-detect
5. Fuzz: 50 random corruptions. Below 100% detection means `func_deps` has holes

The Phase 0 negative control is a manual dry run of exactly this loop, and it worked —
which is encouraging for the mechanism, though it proves nothing about automation.

---

## Phase 4 — Full autonomous loop

**Tasks**
- [ ] `solver/loop.py` — SELECT / DRAFT / REFINE / PERMUTE / CHALLENGE / PARK
- [ ] `solver/select.py` — leaf-first priority (wrap `score_functions.py`)
- [ ] `solver/blame.py` — differential blame (rarity, thin support, recency)
- [ ] `solver/speculate.py` — KB forking, bounded parallel evaluation
- [ ] Stall detection, budget policy, parked-function re-wake
- [ ] Run dashboard: match rate over time, retractions, cost burn

**Acceptance**
- 8-hour unattended run with monotonically non-decreasing match count
- At least one poisoned fact caught by CHALLENGE without human input
- Blame ranking puts the true culprit in its top 3 for >70% of injected poisons

---

## Phase 5 — Generalize, then find a live target

*Goal: prove it is not overfit to one codebase, then do something useful with it.*

**Tasks**
- [ ] Run the full SBK1 eval; report match %
- [ ] **Run against SBK2 with no SBK2-specific tuning.** Same compiler, different
      codebase — this is the real generalization number
- [ ] Only then: choose a live target. Criteria — N64/MIPS with IDO (best m2c support),
      partially complete so there is headroom, obscure enough to avoid pretraining
      contamination, and an active repo whose maintainers want the help
- [ ] Evaluate whether accumulated trajectories justify Tier 2/3 per TRAINING.md

**Acceptance**
- SBK2 match % reported honestly, with failures characterized
- The gap between SBK1 and SBK2 match rates is the overfitting measure. A large gap
  means the KB learned this codebase, not the compiler.

---

## Working agreements

- **Never** let held-out reference source reach the model. Contamination invalidates
  every number after it.
- Respect the host repos' conventions and hooks. We are guests in cdlewis's projects;
  their CLAUDE.md rules apply when working inside them.
- Every phase's acceptance check becomes a permanent regression test.
- Report failures with the actual output. A phase that half-works is reported as
  half-working, not as done.

## Immediate next action

Phase 1, task 1: `kb/schema.sql` and `miner/evidence.py`. The evidence tier is the
foundation nothing else can be built without, and it is the piece that exists nowhere
today.
