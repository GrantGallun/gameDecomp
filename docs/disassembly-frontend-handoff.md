# Handoff: own disassembly front end (started 2026-10-03)

Read CLAUDE.md, PIPELINE_MAP.md and memory `project-goal-transfer` first. Work on a new git branch `disasm-frontend`;
do not touch the SBK1 campaign or `solver/site_edits.py` defaults.

## Why
The user's goal (2026-10-03): a general binary -> matching-C decompiler that goes game to game. Other games are
PRACTICE environments for finding and filling holes with game-agnostic logic, not targets to fit. Gap found: the
pipeline has never disassembled a game itself. Function boundaries, data/code split and symbols come from the
reference decomp (`miner/import_existing.py`, SBK1's splat yaml `~/decomp/sbk1/snowboardkids.yaml`, symbol_addrs).
A new game has none of that.

## What to build
ROM -> (1) code segments + load addresses, (2) function boundaries incl. compiler padding (bootThreadMain: one
padding instruction made it unreachable), (3) code vs data (mining once found 629 data symbols disassembled as
code), (4) jump tables, strings, .rodata ownership, (5) compiler fingerprint (IDO 5.3/7.1 vs GCC/KMC, -O level)
from code patterns, (6) per-function asm + self-named symbols in the format the rest of the pipeline consumes.
Tools installed in `~/decomp/sbk1/.venv`: splat 0.39.1, spimdisasm 1.42.4. Nothing in our code drives them yet.

## How it is graded
On a game with a finished matching decomp, compare our boundaries / code-data split / compiler guess to the
decomp's own config. The reference GRADES only; it never feeds the stage (CLAUDE.md "ground truth is for checking").
"Explained-or-broken": every disagreement traced and fixed, modelled, or whitelisted with a reason. Every pass needs
a test that it FIRES on its motivating case (silent declines). First target: SBK1 (ROM `~/decomp/sbk1/snowboardkids.z64`).

## After SBK1
Practice games with the same MIPS ISA (oracle works today): N64 Perfect Dark, Paper Mario (GCC 2.8, closest to
SBK2's KMC GCC), SM64/MK64/DKR (checkouts + per-file recipes already in `~/decomp/public-pairs-20260921-v2`);
PS1 PsyQ GCC: Legend of Mana, Digimon World, Digimon Digital Card Battle. GBA/GameCube/x86 need a new verdict
backend later. Reimplementations (SMW, Zelda3, RSDK/Sonic Mania, Cut the Rope) cannot be graded.
Measure: per-game coverage from assembly only; holes counted by residual CLASS across games; transfer test =
leave-one-game-out (a fix built on games A,B must raise coverage on unseen game C).

## Ideas from the literature (2026-10-03, not yet tested here)
1. Don't write a disassembler. spimdisasm already does function detection, rodata migration, jump tables and data
   typing; splat drives it from a yaml. Our job is to DISCOVER the yaml (segments, vram, overlays) and grade it.
2. Segments/overlays dynamically: Mupen64+ RE finds code regions from DMA/TLB traces and writes a splitter config.
   We already have Project64 trace jobs (`eval/project64_trace_coverage.js`): log PI DMA (ROM -> RAM) to get every
   segment's ROM range and load address, then jumps into it mark code.
3. Keep uncertainty explicit (probabilistic disassembly, Miller et al. ICSE'19): a probability per address from
   control/data-flow features instead of a hard code/data call; unknown stays unknown (invariant 5).
4. Padding is the dominant boundary failure across tools ("Padding Matters", arXiv 2504.21520). Model IDO/GCC
   inter-function padding as a cataloged pattern with a fire test (bootThreadMain is the motivating case).
5. Compiler provenance per FUNCTION, not per ROM (games mix libultra -O1/-O2, libraries, game code). Lightweight
   features work (DIComP, SANER'22; fine-grained artifacts, USENIX ATC'22): prologue/epilogue shape, delay-slot
   filling, frame layout, padding. Labeled data is free: compile eval/rule_probes.py-style programs with each
   compiler and flag set.
6. Library identification first (FLIRT / Binary Ninja signature tries: bytes + relocation-wildcard mask, prefix
   tree). Signatures from SDK objects (libultra versions), never from a reference decomp. Removes known functions
   from the workload and pins SDK version/compiler.
7. Round-trip oracle for the front end: re-assemble our split and require byte-identical ROM (what splat projects
   do). The lock-and-key principle one layer down.
8. Learned disassemblers (XDA, DeepDi) target x86 instruction boundaries; MIPS is fixed-width, so they buy little
   here. Revisit only if heuristics plateau.

## Hard constraints
- Never put reference source in a prompt; SBK1/SBK2 never as training data. `tools/corpus_grabber.py` now blocks any
  repo name containing "snowboardkids" (incl. `cdlewis/snowboardkids2-recomp`).
- The pipeline never downloads a ROM; the user supplies dumps they own (`roms/` gitignored, `corpus/rom_inventory.json`).
- Recomps (N64Recomp, XenonRecomp output) and PC ports are not matching source; use the upstream matching decomps.
- Local models only. Build in WSL (`wsl -d Ubuntu`), tree on `~/`; pytest via `~/decomp/sbk1/.venv/bin/python -m pytest`.
- Launch long WSL jobs with `setsid nohup ... & disown` (plain `&` dies with the wsl session).
- When the user wants to game: pause runs, `ollama stop`, `wsl --shutdown`.
