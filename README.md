# gameDecomp

**Automated matching decompilation for N64 games.** gameDecomp turns a function's machine code back into C
that, compiled with the original compiler, produces **byte-identical** object code. It runs unattended
across a whole game.

> **Work in progress.** This is an active research project, not a finished tool. The pipeline changes
> daily, the numbers below come from a live campaign, and setup assumes a specific local environment.
> Expect rough edges.

![Progress dashboard](docs/dashboard.png)

*The progress dashboard at checkpoint 38,599 (2026-10-04). Each block in the map is one function, sized by
its code; colour shows its state (object-exact, ROM-verified, compiling at some similarity, blocked, parked).
[The same view on 2026-09-15](docs/dashboard-2026-09-15.png) had 909 object-exact and a visibly larger share of
orange "compile blocked" blocks. A dated log of what changed is in [docs/PROGRESS.md](docs/PROGRESS.md).*

## What it does

The first target is **Snowboard Kids** (N64, IDO 5.3 `-O2`). Its community decompilation is complete, so
every result can be checked against a known answer, and contamination can be policed.

For each function, the pipeline:

1. **Mines evidence from the binary.** Memory accesses, calls, and data references are extracted
   deterministically. That evidence is immutable, and nothing a model says can write to it.
2. **Drafts C** with m2c and adapts it to the project's headers and build.
3. **Repairs it.** Deterministic repair comes first, guided by the compiler's own diff:
   - **Register-allocation search:** a beam search over source edits known to move IDO's register colouring.
   - **Stack-frame layout:** declaration order, extents and padding.
   - **Structural rewrites:** loop shape, chained assignments, signed comparisons.
   - **Relocation and symbol fixes.**
   - **Compile-error recovery:** undeclared identifiers, placeholder types, C89 declarations.

   A local LLM (`gpt-oss:20b` via Ollama) proposes edits only where those run out.
4. **Compiles every candidate** with the real IDO toolchain (via `ido-static-recomp`) and compares object
   code byte for byte. **The compiler decides.** The model has no authority, and a result counts only when
   the bytes match.
5. **Checks behaviour, then integrates.** Candidates are also run against the original in a MIPS
   differential harness. Exact functions are rebuilt into the full ROM, and the ROM's checksum is verified.

Every attempt, including every failure, is logged with its source, compiler output, and diff. That record
is both the debugging trail and future training data.

> The model proposes, the database remembers why, and the compiler decides.

## What you get

- **Byte-exact C per function**, each with a receipt: source hash, compiler command, and object comparison.
- **A live dashboard** (`eval/progress_app.py`): coverage, a map of the whole program, per-function
  inspection with repair history, recent results, and where the time goes, plus pause and resume for the
  campaign.
- **A pattern catalog** (`patterns/catalog.py`): recurring IDO code-generation shapes and the C that
  produces them, each with provenance. Where a pattern repeats, a repair can be written once and reused.
- **Compiler-internals tooling:** a patched IDO `uopt` build that can dump its register-allocation decisions,
  so allocation mismatches can be diagnosed, not guessed. It lives in the working tree and isn't committed yet.

## Current numbers

Live campaign `resume-pipeline-20260908`, checkpoint 38,599 (2026-10-04), compared with checkpoint 21,492
(2026-09-15), both read from the campaign's own saved state with the dashboard's classifier:

| | 2026-09-15 | 2026-10-04 |
|---|---|---|
| Functions in the campaign | 2,051 | 2,051 (62 more held out and never attempted) |
| Object-exact | 909 | **1,042** |
| Whole-ROM verified replacements | 21 | **62** |
| Function-exact, awaiting integration | 20 | **25** |
| **Object-exact or better, total** | 950 (46.3%) | **1,104 (53.8%)** |
| **Compile-blocked functions** | 74 | **14** |
| Bytes in compile-blocked functions | 70.2 KiB | **19.2 KiB** |
| Object-exact share of function bytes | 14.8% | 19.6% |
| Repair work items completed | ~9,900 | ~17,900 |

The dashboard also shows a "Non-compiling" figure of 17. That adds three parked functions whose current
candidate fails to compile; the 14 above are the pending ones. Compile-blocked functions fell by 81% (74 to
14). The pipeline gained compile-error recovery in that window (undeclared identifiers, placeholder types,
C89 declaration order), which is the likely driver; I have not isolated its share. A compile-blocked function
can't be repaired at all, so each one recovered becomes a candidate for the other repairs.

**Where the 1,104 come from.** Every function starts from a draft (m2c plus the project's headers and build
setup), is compiled with IDO, and is repaired where the bytes differ. The campaign state and the repair ledger
(`repair_yield.exact_functions_gained`) split the result by stage:

| Of the 1,104 object-exact or ROM-verified functions (2026-10-04) | |
|---|---|
| exact from the draft at intake, with no repair needed | **563** (51%) |
| exact after repair, credited by the repair ledger | **358** (32%) |
| exact after repair, earlier than the ledger's measurement window | 183 (17%) |
| functions lost (`exact_functions_lost`) | **0** |

Repair is the part that is hardest to get and the part the project is built around: **541 functions went from
failing to byte-exact through repair**, 358 of them measured directly by the ledger, and none has been
lost. The 563 intake matches are small (median 80 bytes, largest 496). Of the 358 ledger-credited, 299 are under 256 bytes, 58 are 256 B to 1 KiB, and 1 is larger.
`python3 -m eval.status` reports a different figure for the smaller research knowledge base (393 byte-exact of
1,074 attempted, 278 of them SOLVED), which is a separate measurement.

Register-allocation search is the most productive repair. It produced 155 of the first 218 exact
functions the campaign gained, with no model call. Its latest amendment (2026-09-15) adds "enabling
roots": edits that don't improve the score but unlock a later repair. In an offline test they made 15 of a
30-function family exact; none was reachable before.

**The setting.** This is a *header-assisted development* run. Repairs see the decompilation project's headers
and build setup, and the 62 held-out functions are excluded. The starting drafts can also draw on the project's
tree, and about half of the intake matches don't carry the m2c marker, so I'm measuring how much of the intake
share comes from reference code. This is not a binary-only or unseen-game benchmark, which is what the
disassembly front end and the model-training work below are for. How much the headers help is itself open: an
earlier experiment found that handing the model perfect type information made results *worse*.

## Training our own model

Alongside the campaign, the project is training its own small model (a LoRA adapter on `gpt-oss:20b`,
trained and served locally on one RTX 5080) in place of calling a hosted one. The aim is to teach it what the
compiler does, not to memorise answers. The compiler labels every training example, so no label is a guess.

- **Data.** Edits are planted into code from public decompilations (Super Mario 64, Mario Kart 64, Diddy Kong
  Racing). IDO compiles each before and after, and the object diff gives a verified label. The current set has
  27,462 tasks: predicting whether two sources compile the same, and explaining or undoing an edit. It is split
  by function, with 612 exam tasks and a separate check split. Snowboard Kids itself is never in the training data.
- **Training.** Supervised fine-tuning, then reinforcement learning (GRPO) with compiler-graded rewards. Held-out
  exams freeze before a run, and success criteria are written down before the results are read.
- **What the experiments showed.** Where the model is weak is *reading* assembly, not finding the line to edit:
  telling it where the problem is changed little. A "reading" task (blank a statement, then recover it from the
  instructions) helped most. On repairs of missing statements it solved 42 tasks to 18 for an equal-size control,
  though the reading arm also had more examples of that task, so a class-matched control is still pending.
- **General skill transfer.** On 336 held-out functions the base model compiled none of them (it pastes inline
  assembly). The best adapter compiled 229 raw and matched 21 byte-for-byte, though it was never trained to
  decompile whole functions. It replaces the previous adapter, which matched 15. These are single greedy runs, so
  treat the gap as a lead, not a result.
- **Honest limits.** An outside audit found real problems in earlier measurements (an exactness check that
  ignored which global a symbol referred to, shared-success cost accounting, silent split fall-through). Those
  are fixed and the affected results were re-certified against the object files. The adapter's gains so far
  are on planted-edit and held-out exams, not new functions in the campaign. See `docs/model-capability-training-audit-20261003.md`.

Training code is in `eval/` (`logic_tasks.py`, `train_*`, `arm_runner.py`), serving is in `tools/lora_serve/`, and
`TRAINING.md` has the plan and the evaluation rules.

## How the work is judged

The project's rules exist because every analysis bug found so far had the same shape: a guess about
compiler behaviour encoded as a rule without testing it.

- **Byte-exact or it didn't happen.** Similarity scores guide search; only an identical object counts.
- **Evidence and inference are separate tiers.** Anything derived from a model is retractable and must cite
  evidence.
- **Changes are tested before they steer.** New repairs get a written prediction before their test runs. They
  must fire on the case that motivated them, not merely decline elsewhere. Failed ideas are recorded in `memory/hypothesis-graveyard.md` so they are not retried.
- **The live campaign runs frozen code.** Improvements are staged, pass the full suite and an end-to-end
  check, then deploy as recorded amendments with rollback copies.

## Repository layout

| Path | What's there |
|---|---|
| `miner/`, `kb/` | Deterministic evidence extraction and the SQLite knowledge base |
| `oracle/` | Compile-and-compare against the original objects |
| `solver/` | Repairs: register search, stack layout, structural rewrites, compile recovery, model prompts |
| `patterns/` | Catalogued IDO code-generation patterns and the hypothesis ledger |
| `eval/` | Campaign controller, service, and dashboard. Experiment results and receipts go to `eval/results/`, which is kept out of git. |
| `tools/` | Toolchain helpers, including the IDO allocation-trace build |
| `tests/` | The test suite (2,741 tests in the campaign's current runtime) |
| `DESIGN.md`, `ROADMAP.md`, `PIPELINE_MAP.md` | Design, phase plan, and a map of which mechanism is wired where |

## Running it

There is no one-command setup yet. The current environment:

- **Windows with WSL2 Ubuntu.** The build tree lives on the WSL filesystem, and the N64 toolchain runs
  inside WSL.
- **The IDO 5.3 toolchain** through `ido-static-recomp`, plus m2c.
- **A local Ollama server** for the model-assisted stages. A GPU helps.
- **Your own legally obtained ROM.** No ROMs or game assets are included in this repository.

Start with `DESIGN.md` and `PIPELINE_MAP.md`. With a campaign running, `launch-progress.ps1` starts the
dashboard at `http://127.0.0.1:8765`.

## Status and next steps

**New: a disassembly front end that starts from the ROM alone.** `disasm/` finds function boundaries, overlays
and load addresses without the reference project's symbol files: SBK1 boundaries come from the ROM, and for
Snowboard Kids 2 (a GCC build) it finds 20 of 20 overlays and 720 of 720 functions exactly. It also emits
assembly and checks it round-trips. This is the first step toward running on a game with no existing
decompilation.

Most remaining functions compile but differ in *structure*: branch shape, extra or missing loads, and
frame layout. These are larger functions where register search alone cannot finish. The current focus is
finding the recurring causes in the pending residuals and turning each into a tested, deterministic repair.
