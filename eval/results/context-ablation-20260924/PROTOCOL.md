# Protocol: which part of the type context makes m2c's drafts match?

Written 2026-09-24 before `ablate.py` ran. Motivation: with the reference's context, raw m2c is byte-exact on 55% of
small mining functions (`draft-reference-mining-20260924`); about 172 of 278 SOLVED depend on the reference team's
headers (`type-flywheel-20260924`); cross-function identity is not the main lever for single-function matching
(`struct-identity-20260924`). Before recovering types from the binary, measure WHICH component of type knowledge
m2c needs. The reference context is the experimental TREATMENT here, on mining functions only (the scored
population and sealed sets are excluded, as in `pairs.py`); nothing from it reaches a scored function.

## Arms (m2c context; everything else identical)
The context is the target repo's `ctx.c`, parsed with pycparser and re-emitted:
- **NONE**: no context (assembly only).
- **FULL**: `ctx.c` as is.
- **NOLAYOUT**: every struct and union definition made incomplete (members removed); names, typedefs, prototypes and
  global declarations kept.
- **NOPROTO**: every function declaration removed; struct layouts, typedefs and global declarations kept.

## Procedure (`ablate.py`)
Sample: the mining functions whose reference compiles exact modulo relocation names in `pairs.py` (the harness
check), ordered by sha256(name), first 600. Each arm: m2c `--target mips-ido-c` on the workspace `target.s` after
`m2c_input.normalize_o32_registers`, with the arm's context. The draft's definition replaces the reference definition
in its compile root, with every other definition reduced to a prototype (the `pairs.py` harness), and is compiled
with the function's recorded recipe. Outcomes: m2c failed, did not compile, compiled, exact modulo relocation names.

## Reading (fixed now)
Per arm: exact and compile rates, overall and by size (target length < 50 / < 150 / larger).
- The FULL-minus-NONE exact gap is the type lever. Its share recovered by NOPROTO (layouts without prototypes) and
  by NOLAYOUT (prototypes without layouts) says what the type work must recover first.
- **Layouts dominate** if FULL - NOLAYOUT >= 2 x (FULL - NOPROTO); **prototypes dominate** if the reverse; else both.
- Sanity: FULL's small-function exact rate should be near `pairs.py`'s 55%. A large gap means this harness differs
  from how `base.c` was produced, and is reported as such.

## Amendment A1 (2026-09-24, smoke run of 8 functions, before the full run)
Every arm, FULL included, failed to compile all 8 (`x.field` on a non-struct): the repo's `ctx.c` is a stale
snapshot whose types disagree with the current headers the compile root uses. Fix: each function's FULL context is
`tools/m2ctx.py`'s `import_c_file` over a wrapper holding exactly its compile root's own `#include` lines (current
headers, as `solver.m2c_input.draft` builds contexts); NOLAYOUT and NOPROTO are the same pycparser transforms applied
to that per-TU context. Everything else is unchanged.

## Amendment A2 (2026-09-24, second smoke run of 12, before the full run)
With header-only contexts, FULL compiled 3/12 with 0 exact: many actor structs are defined in the `.c` compile roots
themselves, not in headers. FULL is now m2ctx over the whole expanded compile root with EVERY function definition
reduced to its prototype (file-scope types, globals, statics and prototypes; no bodies). It is identical for all
functions of one TU and cached per TU. The ablations are applied to it unchanged.

## Amendment A3 (2026-09-24, during the full run, at 102 of 600 rows)
One TU's context failed to preprocess (`<libaudio.h>`: m2ctx's fixed include flags lack the recipe's
`-Iinclude/PR -Isrc/ultra/audio -Isrc/ultra/libc` and its defines), and m2ctx's `sys.exit` ended the run. The
recipe's include paths and defines are added to m2ctx's flags for every TU. Rows already written are unaffected (their
contexts preprocessed without these flags and are kept); a context failure is now recorded per function.

## Amendment A4 (2026-09-24, before any BINARY row ran): a fifth arm built from the binary alone
**BINARY**: the m2c context and the compile header are `#include "common.h"` plus `binary_context.decls(fn)`:
struct types from the struct-identity D16 groups (observed offsets, widths, signedness; placeholder names), pointer
fields from structural children, prototypes from binary arity, globals from accesses inside ELF symbol extents. The
draft compiles STANDALONE (common.h + those declarations + the m2c definition), never inside the reference TU, so its
compile environment is strictly less informed than the other arms'. An exact BINARY object is an exact match reached
without reference source or headers (common.h and the SDK headers it includes are public). Same 600-function sample,
same outcomes. Reported next to NONE and FULL; the question is what share of the FULL-minus-NONE exact gap BINARY
closes.

## Amendment A5 (2026-09-24, after a 6-function BINARY smoke run, before any reported BINARY row)
The generator is developed on DEV = the first 150 of the 600 (sha256 order) and frozen (file hash recorded) before it
runs on CHECK = the other 450; BINARY is reported on CHECK only, with the other arms' numbers on the same 450.
Smoke findings fixed on DEV: multi-width offsets become unions; address-only data symbols are declared as byte arrays
of their ELF size; callees that read a3 (possible stack arguments) get an unprototyped declaration.

## Amendment A6 (2026-09-24, integrity fix; the first CHECK run was stopped before any result was read)
The reference repo's `include/common.h` includes `game/math/geometry.h`, a reference-team header. BINARY must not
see it: its prelude is now `common.h`'s public parts only (`include_asm.h`, `compiler_diagnostics.h`, `<PR/mbi.h>`,
the `sprintf` declaration), both as m2c context and in the compile. DEV is rerun under the clean prelude and the
generator re-frozen before CHECK. The earlier DEV figures (16 -> 30 -> 32 -> 34 exact) included geometry.h and are
superseded.
