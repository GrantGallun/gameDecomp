# Autodecomp — Design

## Thesis

This is **not** a model. It is a **knowledge base with a solver attached**.

> The model proposes, the database remembers why, and the compiler decides.

Matching decompilation has a property almost no other code task has: a free, perfect,
dense verifier. Compile the candidate C, diff the object file against the original,
get a 0–100 score. No human labels, no LLM judge, no flaky tests.

The LLM is never an authority. It generates hypotheses. Byte-exact comparison
adjudicates them. A truth-maintenance system keeps the bookkeeping honest so that a
rejected hypothesis leaves no residue.

Prior art check: single-shot SFT on decomp.me scratches has been tried and yields
~1.2% byte-exact match, and fine-tuning barely beat zero-shot. The bottleneck is not
model knowledge; it is the absence of a loop and the absence of accumulated context.

## Non-goals

- Distributing weights, datasets, or decompiled source. Local research tooling only.
- Whole-program one-shot decompilation.
- Beating humans on any single function. The win is unattended throughput.

## Invariants

These must never break. Everything else is negotiable.

1. **The build is always green.** Every function is either still assembly (correct by
   construction) or matched C (verified byte-exact). Non-matching C lives behind
   `#ifdef NON_MATCHING` with the asm still used for the real build. There is no
   "probably right" state in the tree.
2. **The ratchet.** Global match count never decreases. Any change that reduces it is
   automatically rolled back.
3. **Evidence is mechanical; inference is retractable.** The model may write only to
   the inference tier. It may never write evidence.
4. **Every inference cites evidence.** No citation, no commit.
5. **Unknown is representable and is the default.** `char unk_00[0x24];` beats a
   plausible guess. Guard hard against a model that invents field names because
   plausible field names are what training data looks like.

## Architecture

The solver has two non-conflicting work lanes. The logic-first lane reconstructs
call-connected modules and records compiling, logic-shape, and structural
candidates without promoting them into the real build. The exactness lane uses
the compiler/object residual to polish structurally ready candidates. Both feed
the same evidence/TMS substrate, and only byte-exact candidates may commit. See
`LOGIC_FIRST.md` for the stage definitions and first connected DEV baseline.
The optional finished-decomp teacher is governed separately by target/TU
exclusion regimes and tamper-evident packets; see `REFERENCE_TEACHER.md`.

The completion campaign now enables the resilient agent worker: deterministic
build-context recovery, compiler-checked connected type plans, existing C89
normalization, and target-led differential evaluation after compilation. The
worker reuses the existing interpreter, coverage explorer, stress generator and
value-DAG feed. It retains separate semantic and byte champions; a lower byte
score can be worthwhile behavioral progress but never an exactness promotion.
For original void-pointer member drafts, an initial bounded constraint stage
measures header layouts using the configured target compiler, propagates draft
pointer assignments, and compares ordinary field accesses with observed entry
load/store widths. It enumerates remaining alternatives through the existing
type-plan actuator. This is a restricted candidate search, not binary-only type
inference or proof that the chosen union view is the original source type.
Repeated plan failures change representation and bounded stalls retry an intact
initial candidate. See `PIPELINE_MAP.md` for wiring and measured limits. Existing
header/TU metadata is explicitly assisted context, never binary-derived evidence.

The logic-first debugger may use counterfactual resynchronization to reveal
later independent call/write mismatches, but those runs are explanations, not
semantic evidence. A candidate passes only on an unintervened replay. Repair
prompts choose one source-grounded mismatch cluster as the edit objective and
use later clusters only to reject inconsistent hypotheses. Multiple observed
offsets do not by themselves authorize a whole-structure rewrite: shared
layout changes require project-header or repeated cross-function binary
evidence. See `DIFFERENTIAL_DEBUGGER.md`.

```
                    +------------------------------+
                    |           ORACLE             |
                    |  build(tu) -> .o             |
                    |  objdiff(.o, target) -> score|
                    |  ground truth; never guesses |
                    +------------------------------+
                        ^                    |
                 verify |                    | score + instruction diff
                        |                    v
                    +------------------------------+
                    |         SOLVER LOOP          |
                    | SELECT DRAFT REFINE VERIFY   |
                    | STALL CHALLENGE COMMIT       |
                    +------------------------------+
                        |                    ^
                   read |                    | write (inference tier only)
                        v                    |
                    +------------------------------+
                    |      KNOWLEDGE BASE (TMS)    |
                    |  evidence   (immutable)      |
                    |  inference  (retractable)    |
                    |  support / depends / funcdeps|
                    +------------------------------+
                        ^
               populate |
                    +------------------------------+
                    |        MINER (offline)       |
                    | split, disasm, callgraph,    |
                    | rodata, asserts, xrefs       |
                    +------------------------------+
```

## Data model

SQLite. Single file, transactional, cheap savepoint/rollback — which is exactly what
the ratchet needs.

### Evidence tier (immutable, mechanically derived)

Facts about the *binary*, not about types. A `lbu` load at offset 0x24 yields
width=1, signed=0, class=int. This is observation, not interpretation, and it is
never wrong.

```sql
CREATE TABLE evidence (
  id        INTEGER PRIMARY KEY,
  kind      TEXT NOT NULL,   -- mem_access | call | imm | reloc | string_ref | rodata
  addr      INTEGER NOT NULL,-- instruction address
  func_addr INTEGER,         -- containing function
  base      TEXT,            -- symbolic base: 'param0' | 'global:0x80123456' | 'sp'
  offset    INTEGER,
  width     INTEGER,         -- 1 | 2 | 4 | 8
  signed    INTEGER,         -- 0 | 1 | NULL when not determinable
  class     TEXT,            -- int | float | addr   (from opcode family)
  op        TEXT,            -- raw mnemonic
  UNIQUE(addr, kind)
);
CREATE INDEX ev_base_off ON evidence(base, offset);
CREATE INDEX ev_func     ON evidence(func_addr);
```

### Inference tier (retractable claims)

```sql
CREATE TABLE inference (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,  -- field | signature | symbol_name | tu_assign | flags | struct_size
  subject     TEXT NOT NULL,  -- 'struct:Actor@0x24' | 'func:0x8004A1B0'
  value       TEXT NOT NULL,  -- JSON payload
  confidence  REAL,
  status      TEXT NOT NULL DEFAULT 'active',  -- active | retracted
  created_at  INTEGER,
  retracted_at INTEGER,
  retraction_reason TEXT
);

-- justification: which observations support this claim
CREATE TABLE inference_support (
  inference_id INTEGER, evidence_id INTEGER,
  PRIMARY KEY (inference_id, evidence_id)
);

-- claims built on other claims
CREATE TABLE inference_depends (
  inference_id INTEGER, depends_on_id INTEGER,
  PRIMARY KEY (inference_id, depends_on_id)
);
```

### Functions, TUs, and the dependency ledger

```sql
CREATE TABLE tus (
  id INTEGER PRIMARY KEY, name TEXT, start_addr INTEGER, end_addr INTEGER,
  compiler TEXT, flags TEXT, source_path TEXT
);

CREATE TABLE functions (
  addr INTEGER PRIMARY KEY, name TEXT, tu_id INTEGER, size INTEGER,
  state TEXT NOT NULL DEFAULT 'asm',  -- asm | attempted | matched
  best_score REAL DEFAULT 0, attempts INTEGER DEFAULT 0, source_path TEXT
);

-- THE critical table. How retraction knows what to rebuild.
CREATE TABLE func_deps (
  func_addr INTEGER, inference_id INTEGER,
  PRIMARY KEY (func_addr, inference_id)
);
```

### Trajectory log

Every attempt, kept. This is the debugging record now and the RL dataset later.

```sql
CREATE TABLE attempts (
  id INTEGER PRIMARY KEY, func_addr INTEGER, run_id TEXT, iteration INTEGER,
  parent_attempt_id INTEGER, source_code TEXT, score REAL, compiled INTEGER,
  diff_summary TEXT, strategy TEXT, created_at INTEGER
);

CREATE TABLE attempt_edges (
  parent_attempt_id INTEGER, child_attempt_id INTEGER,
  relation TEXT, action TEXT, feedback TEXT,
  PRIMARY KEY (parent_attempt_id, child_attempt_id)
);
```

Attempt order is not trajectory order. Independent samples have no attempt parent;
best-anchored refinement can branch from an older row; deterministic beam search has
many children. Training pairs therefore come only from explicit edges.

### Local-model repair loop

After deterministic repairs reach a fixed point, the optional local-model stage
branches from the best verified parent. Each call proposes one bounded JSON edit
(one hypothesis, at most four unique substring replacements). The harness applies
the edit, recompiles it, and uses only the compiler/object verifier to admit and
rank children. Compiling parents remain eligible, so a regression never becomes a
mandatory next step. Non-compiling children survive only in the bounded error beam,
where a changed compiler diagnostic can reveal the next blocker.

Refusals, malformed edits, duplicates, and generation failures are stored in
`model_proposals`; compilable or non-compilable source children are stored in
`attempts` and connected to their true parent by a `model-repair` edge. This keeps
"the model did not propose source" distinct from "the compiler rejected source."

The stage is disabled by default pending a controlled exact-match A/B. Enable it
with `--model-repair-draws`; depth and beam width have separate flags and are part
of the experiment fingerprint.

The newer completion campaign also has an explicit **coordinated type
transaction** profile for noncompiling roots. It extends the same search kernel,
not the KB evidence tier: at most 64 source-bound line/declaration-slot edits,
12,000 changed-span characters, and 4,000 net source growth, applied atomically.
Included ordinary public signatures are locked modulo parameter names; complex
or conflicting declarations remain explicitly unresolved and compiler-checked.
Candidate views must use explicit C rather than preprocessor substitutions.
New recognized typedef collisions with included headers and changes to the
control-keyword sequence are rejected before compilation. These checks protect
the type-repair scope; they are not a general C parser or semantic proof.
A temporarily worse noncompiling child can receive two follow-up depths while
the best verified candidate remains retained. All provider calls, source
parents, declines, compiler results, and bounded stopping conditions remain
logged. This exception to the ordinary four-edit path is mapped and evaluated
in `PIPELINE_MAP.md` and `COMPLETION_CAMPAIGN.md`.

### Contradictions

Produced by a background sweep, consumed by the solver.

```sql
CREATE TABLE contradictions (
  id INTEGER PRIMARY KEY, subject TEXT, kind TEXT, detail TEXT,
  status TEXT DEFAULT 'open', found_at INTEGER
);
```

## Core algorithms

### Retraction (truth maintenance)

```
retract(I, reason):
    mark I retracted, record reason
    D <- transitive closure over inference_depends of I
    retract all of D
    F <- { f : (f, x) in func_deps, x in {I} union D }
    for f in F:
        demote f to 'attempted'
        rebuild f
    ratchet_check()
```

### The ratchet

Every mutation runs inside this. It is what makes unattended overnight runs safe.

```
propose(delta):
    savepoint
    before <- count(state = 'matched')
    apply delta
    rebuild(dependents(delta))
    after  <- count(state = 'matched')
    if after < before:
        rollback to savepoint
        record rejection (with delta, for the do-not-repropose set)
    else:
        release savepoint
```

### Contradiction sweep

Continuous background pass, grouped by `(base, offset)`:

| Signal | Meaning |
|---|---|
| width disagreement | union candidate, or wrong base identification |
| signed/unsigned disagreement at same width | one reading is wrong |
| class disagreement (int vs float, same offset) | strong union signal, or error |
| overlapping fields | layout error |
| access beyond known struct size | size underestimate |

Contradictions are free bug reports sitting in data you already have. Run the sweep
constantly, not on demand.

### Differential blame

When a function stalls, do not keep mutating C. Localize the poisoned fact.

Rank each inference the stalled function depends on by:

- **Rarity** — how seldom it appears in the dependency sets of *matched* functions.
  A fact that only ever shows up under failures is the prime suspect.
- **Thin support** — inferences justified by a single evidence row rank higher.
- **Recency** — recently added facts rank higher.

This is spectrum-based fault localization pointed at the type database instead of at
code. It converts "something upstream is wrong" into a ranked list of three candidates.

### Speculative branching

An advantage humans structurally cannot use: branching is cheap for us because the
verifier is cheap.

```
on ambiguous evidence for a field:
    fork KB into N candidate branches
    pick k dependent functions
    run bounded REFINE in each branch
    keep the branch with the highest total match
    record losing hypotheses in the do-not-repropose set
```

## Solver loop state machine

```
SELECT --> GATHER --> SAMPLE --> TRIAGE --+-- 100%    --> COMMIT --> (ratchet)
                                          |
                                          +-- >=95%   --> PERMUTE
                                          |
                                          +-- 80-95%  --> RETYPE
                                          |
                                          +-- <80%    --> RESHAPE
                                          |
                                          +-- budget  --> PARK --> ESCALATE
```

**Triage, because the failures are not one problem.** Sampling uniformly and
hoping scored 19.4% on a stratified 41-function set, with every match in the
`tiny` tier and zero above it. Breaking those failures down by score showed
four distinct populations needing four different tools, and the score itself
says which one applies.

| Route | Band | Tool | Why |
|---|---|---|---|
| COMMIT | 100% | — | verified byte-exact |
| PERMUTE | ≥95% | decomp-permuter | register allocation only |
| RETYPE | 80–95% | decoded stride + KB facts + one sibling | wrong struct sizes and field types |
| RESHAPE | <80% | mirror matched siblings | wrong shape wholesale |

**GATHER** is what the knowledge base is for. Before the first sample it
assembles: verified access widths and signedness from the evidence tier, array
strides decoded arithmetically from the target's own index arithmetic, catalog
prescriptions whose detectors fire on this target, and — for the lower bands —
the source of already-matched near-twins.

**The 95% floor is a historical budget heuristic, not a tool limitation.**
The permuter also changes types and source structure; route by the measured
residual before using a score fallback. `unlockRelocatableHeapBlock` sat at 99.167%
purely because the model declared an 18-byte struct against a real 20-byte one;
300 seconds of permuting moved it nowhere. A high score is not evidence that
what remains is a polish job.

**PARK is a normal outcome, not a failure.** The reference decomp took 4,015
commits over three months, full of "Improve X match to 90.704%" then "96.626%"
then "99.210%". Converging over several passes with better context each time is
how this work actually goes, so an unsolved function keeps its best attempt and
is revisited when the knowledge base has learned more.

Earlier design, kept as a warning: a REFINE state fed the instruction diff back
for a targeted fix. Across 8 functions it contributed *zero* — every match came
on the first attempt. Best-of-N sampling doubled the rate on the same budget
(75% vs 37.5%), because the answer is already in the model's distribution and
sampling reaches it where arguing with the model does not.

**SAMPLE, not REFINE — this was measured, not assumed.** The original design had a
REFINE state that fed the instruction diff back for a targeted fix. Across 8 functions
that contributed *zero*: every match it found came on the first attempt. The diffs were
small and precise; the model simply cannot localise which source change produces a given
instruction change, so feeding it a diff makes it thrash.

Independent best-of-N sampling from the same prompt doubled the match rate on the same
budget (75% vs 37.5%). The reason is measurable: one function resampled three times
scored 87.61%, 82.42%, and 100%. The answer is already in the model's distribution, and
sampling is how you reach it. See the A/B in [ROADMAP.md](ROADMAP.md).

- **SELECT** — priority = reverse-topological (leaf-first) x small size x high fact
  coverage x same TU as recent matches. Leaves need the least type context and their
  signatures unlock their callers, so cheap wins compound.
- **DRAFT** — m2c primary; Ghidra headless pseudocode as an optional second view
  (reported to outperform raw asm as model input). Substitute known types from the KB.
- **REFINE** — compile, objdiff, feed score + instruction diff back, regenerate.
  Bounded iterations, track best.
- **PERMUTE** — above ~95, hand to `decomp-permuter` before reasoning further. It is
  far cheaper per attempt than a model call.
- **CHALLENGE** — the encoded human heuristic: *if I have been fighting register
  allocation for two hours, my types are wrong.* Run differential blame, propose
  alternates, speculate. Without this the loop grinds forever on a function whose
  types were poisoned days earlier.
- **PARK** — give up, keep the trajectory, revisit automatically when a dependency
  changes.

## Miner passes (no LLM involved)

1. **split** — `splat` (N64) / `decomp-toolkit` (GC/Wii): sections, TU boundaries,
   function boundaries. TU boundaries are *recoverable*, not guessed: the original
   linker laid each `.o` out contiguously.
2. **evidence extraction** — walk instructions, emit evidence rows.
3. **callgraph** — from `jal`/`bl` targets and relocations.
4. **rodata** — floats, doubles, jump tables, strings; associate to TU. Emission order
   constrains the source, so this matters for matching.
5. **assert mining** — locate `__FILE__` string references. Many of these games shipped
   with asserts baked in, yielding original filenames, function-to-file mapping, and
   line ordering within files. Nearly free, and it is how projects like OoT recovered
   the source tree. Do this before anything else.
6. **xref index** — global data references.
7. **compiler fingerprint** — prologue/epilogue and idiom patterns. Wrong compiler
   means nothing ever matches, so pin this early and hard.

## Oracle

- Compiler binaries in Docker; `wibo` to run Win32 PE compilers (MWCC) on Linux.
- `build_tu(tu) -> .o`, `score(func) -> (0..100, diff)` via objdiff.
- **Must be fast.** This runs millions of times. Cache aggressively, keyed on
  (source hash, flags hash). Warm-process the compiler where possible.

## Why an agent beats a human at the rigor specifically

| Advantage | Why humans cannot use it |
|---|---|
| Exhaustive re-verification | Nobody rebuilds 2,000 functions to test one field change; it is boring and slow, so bad facts survive. For us it is a CI job. |
| Global consistency, continuously | A human sees one function's use of offset 0x24. We index every access to every offset across the whole binary and cross-check constantly. |
| Perfect provenance | Humans forget why they believed something six weeks ago. Every fact here cites an instruction address. |
| Parallel hypotheses | Branching is expensive for a human, so they commit to one reading. We fork and let match rate decide. |
| No sunk cost | Humans defend a struct they spent a week deriving. |

Because retraction is cheap and its consequences are immediately measurable, the
correct posture is *aggressive* revision. Humans have to be conservative. We do not.

## Metrics

- **Match rate on held-out already-matched functions** (primary)
- Compile rate
- Mean iterations to match
- KB precision — % of inferred fields agreeing with the known-good decomp
- Retraction rate, and mean time-to-detect a poisoned fact
- Cost per matched function

## Stack

Python (splat and m2c are Python; ecosystem fit) · SQLite · Docker for compilers ·
Claude API with tool use for the propose step.

## Repo layout

```
gameDecomp/
  CLAUDE.md      conventions + pointers, auto-loaded by sessions
  DESIGN.md      this file, source of truth
  ROADMAP.md     phased build order with acceptance criteria
  oracle/        build + objdiff wrappers, caching
  miner/         passes 1-7
  kb/            schema, TMS, retraction, ratchet, blame
  solver/        loop, strategies, prompts
  eval/          harness, held-out sets
  targets/       per-game configs
```

## Open questions

- Union detection: when is a class disagreement a union vs a mis-identified base?
- Cross-TU struct identity: same layout in two TUs — same type, or coincidence?
- Do we need Ghidra at all once the KB is rich, or is m2c plus types sufficient?
- Budget policy: how much compute per function before PARK is correct?
