# Protocol: could a block-by-block inverse (target assembly block -> C statements) work?

Written before `coverage.py` ran. Zero compiles.

## Data (no reference source)
Every compiled node in the recorded population searches carries `source_attribution`: each emitted instruction
with the C line that produced it. Consecutive instructions of one line form a (C line, instruction shape) pair.
Pairs are observations of the compiler, from our own candidates only. SBK1's reference `src/` is not used.

## Shapes
Registers abstracted to their class (temp / saved / arg / return / fp / special), since allocation is global.
- strict: opcode + operand structure, constants/offsets kept, symbol names abstracted
- loose: constants and offsets abstracted too

## Measure
For each unsolved function's TARGET dump, split into basic blocks; per block, the longest-first DP tiling of its
instruction shapes by concatenations of pair shapes observed in OTHER functions. Coverage = share of target
instructions inside a tiled block; also share of blocks fully tiled, per level.

## Reading
Fully-tiled block share >= 50% (loose) and >= 25% (strict): a retrieval inverse is worth building.
Below 20% (loose): blocks are too idiosyncratic or too interleaved by as1; shelve the direction.
