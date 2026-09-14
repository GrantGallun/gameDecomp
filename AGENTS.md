# gameDecomp agent guide

Build an unattended matching-decompilation system that produces C compiling
byte-for-byte to the original game binary. The compiler and object comparison
decide correctness; model output is only a hypothesis.

## Read by task

- Use `DESIGN.md` for architecture or service-boundary changes.
- Use `PIPELINE_MAP.md` when adding or rewiring pipeline machinery, and update it
  when the actual wiring changes.
- Use `TRAINING.md` for model-training, dataset, or attempt-logging work.
- Use `ROADMAP.md` for phase order and milestone acceptance criteria.
- Use `CLAUDE.md` for the project's detailed historical lessons and strategic
  context when those are relevant. Do not load every document for a routine edit.
- Use `CURRENT_HANDOFF.md` only when resuming the campaign it describes or when
  the user asks for current campaign state.

## Project invariants

- Real build-path functions are `asm` or verified byte-exact `matched` functions.
  Keep non-matching C behind `#ifdef NON_MATCHING`.
- Global match count must not decrease. Roll back KB mutations that break the
  ratchet.
- Binary-derived evidence is immutable. Writable inference must cite evidence and
  remain retractable.
- Prefer unknown layouts over invented fields or names.
- Ground-truth source is for evaluation only; never feed held-out answers into the
  solver, KB, prompts, or training examples.
- Miner passes remain deterministic and LLM-free.
- Log every solver attempt, including failures.
- A proposed pattern cannot change behavior until it is confirmed. A generator
  needs a test showing that it fires on its motivating residual, not only tests of
  when it declines.

## Environment and verification

Build N64 targets in WSL2 Ubuntu with build trees on the WSL filesystem, not
`/mnt/c`. Run checks proportional to the change and fix failures caused by the
requested work. After changes under `miner/` or `kb/`, run the evidence validator
described in `CLAUDE.md` against the appropriate completed-decomp oracle when it is
available.

Treat checked-in status prose as a snapshot. Use `python -m eval.status` when a
decision depends on current counts, and distinguish `SOLVED`, header-assisted, and
recovered matches when reporting capability.
