# gameDecomp

**Automated matching decompilation for N64 games.** gameDecomp turns a function's machine code back into C
that, compiled with the original compiler, produces **byte-identical** object code. It runs unattended
across a whole game.

> **Work in progress.** This is an active research project, not a finished tool. The pipeline changes
> daily, the numbers below come from a live campaign, and setup assumes a specific local environment.
> Expect rough edges.

![Progress dashboard](docs/dashboard.png)

*The live progress dashboard during the current campaign. Each block in the map is one function, sized by
its code; colour shows its state (object-exact, ROM-verified, compiling at some similarity, blocked, parked).*

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

Live campaign `resume-pipeline-20260908`, checkpoint 21492 (2026-09-15):

| | |
|---|---|
| Functions in the campaign | **2,051** (62 more held out and never attempted) |
| Object-exact | **909** (44.3%) |
| Whole-ROM verified replacements | **21** |
| Function-exact, awaiting integration | **20** |
| Still pending | 1,059 (42 parked on known pipeline gaps) |
| Object-exact share of function bytes | 14.8%. Small functions match first. |
| Repair work items completed | ~9,900 |

Register-allocation search is the most productive repair. It produced 155 of the first 218 exact
functions the campaign gained, with no model call. Its latest amendment (2026-09-15) adds "enabling
roots": edits that don't improve the score but unlock a later repair. In an offline test they made 15 of a
30-function family exact; none was reachable before.

**What these numbers are and are not.** This is a *header-assisted development* run:
- Repairs see the decompilation project's headers and build setup.
- They never see its function bodies.
- The 62 held-out functions are excluded.

It is **not** a binary-only or unseen-game benchmark. How much the headers help is itself an open
question. An earlier experiment found that handing the model perfect type information made results
*worse*.

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

Most remaining functions compile but differ in *structure*: branch shape, extra or missing loads, and
frame layout. These are larger functions where register search alone cannot finish. The current focus is
finding the recurring causes in the pending residuals and turning each into a tested, deterministic repair.
