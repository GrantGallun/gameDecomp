# Hidden object differences: what the normalized asm diff cannot show (2026-09-30)

Question: does the lossy normalized diff (`.text` only; `jtbl_*` -> `.rodata`; trailing nops dropped) hide object
differences on unsolved functions, and is any of it actionable? Trial DB only
(`~/decomp/runs/lead3-20260930/trial.sqlite`); nothing written to the campaign or ledgers.

## Instruments
- `census.py`: compiles the best candidate of every unsolved function (frame = `loop-shape-20260930/rescore.frame`,
  both ledgers, sealed 50 excluded; 828 functions) and compares every non-`.text` section of `target.o` vs candidate.
- `analyse.py`: summary; separates the 32 functions the 9/30 jump-table certificate already covers.
- `bytes8.py`: raw `.rodata` bytes and differing relocations for the `.text`-identical cases.
- `inline_extern.py`: lever probe, extern string -> the literal at the target relocation's `.rodata` addend.
- Receipt pass (before the census): `solver/object_discrepancy.py` over the 15.5k campaign + 2.6k kb attempts that
  carry certificate images.

## Measured
- 828 compared, 0 errors. **155 (19%) have >= 1 non-`.text` difference**, 124 outside the jump-table set.
  Function counts: `.rodata` extra 90, alignment 26, size 21, missing 16; `.bss` extra 20; `.data` extra 4.
- **Zero** functions with same-size `.rodata` and different bytes. The worst hypothesis (wrong float/string constants
  invisible to the diff) has no instance in this frame.
- Score bands of the 155: 100: 3, >= 99: 43, >= 90: 37, < 90: 72. Most carry visible `.text` faults too; the hidden
  row is an additional blocker, not the only one.
- `.text` byte-identical, blocked only by non-`.text` rows: jump-table set (covered 9/30) plus 8 others:

| function | blocker (measured) | outcome |
|---|---|---|
| guMtxIdent | stray file-scope `float sp18[4][4];` -> 64 B `.bss`; diff empty, every lane declined ("no mismatching instruction mapped") | **deleting the line -> object_sections_exact** (trial) |
| func_8005905C | extern `D_800E128C..` for strings the target owns | already function_exact (schema 3); inlining the literals LOSES that |
| func_8005CF60, func_8005A884, updateEndingObjectSpriteDebugViewer | `.rodata` = target bytes + IDO pad to 16 / align 16 vs 4 | function_boundary declines: "address-taken candidate rodata needs its named symbol for integration" |
| func_8005C14C | `.rodata` 160 B identical to target; align 16 vs 4 | declines: "candidate rodata the function does not read" |
| func_8005D558 | extern `gRaceUiTrickValueFormat` for target-owned `"%4d"` | declines "unresolved candidate external"; after inlining, declines like the three above |
| func_8005AC44 | 5 extern labels for target-owned strings | inlining rewrite failed to compile (not diagnosed) |

- `.bss` extra (20 functions): unused file-scope `dummy` / `padding` / `spNN` objects, apparently stack-padding edits
  placed outside the function. Deleting unreferenced ones on 14 best candidates: 1 exact (guMtxIdent); 1 loses the
  hidden row but keeps `.text` faults (waitForNextFramebufferEvent); the rest are unchanged (certificate not reached).

## Built on this (same day)

**Levers (generators; catalogued with provenance in `patterns/catalog.py`):**
- `solver.rodata_symbol.address_variants`: a function-owned string, or an extern the ROM does not define, is
  rewritten to the target's own label at the same instruction offsets. Handles definitions, externs, and anonymous
  literals; the anonymous case applies only when the source literals rebuild `.rodata` exactly. With `.text`
  byte-identical, it also renames a different external symbol to the target's.
- `solver.file_scope_objects.variants`: deletes unused file-scope objects when only the candidate has `.bss`/`.data`.

**Layer:**
- Producer: `workspace.score` attaches `Attempt.object` = `object_discrepancy.summarize` to every compiled attempt,
  with route `exact` / `c_edit` / `generator` / `certify` / `unexplained`.
- Carrier: `ResidualPacket.object`.
- Routing, in `repair_queue`: a lever opens operand repair even with an all-zero fault vector, and takes that visit
  first. `certify` spends no C-edit budget.
- Generators wired into `eval.operand_repair._proposals`, which now receives the incumbent's object.

**End-to-end run over the same 828 functions** (`layer_run.py`; re-run of non-`c_edit` routes in `layer_run2.jsonl`):

| route | functions |
|---|---|
| c_edit | 773 |
| certify | 32, all already function_exact (correctly no C edit) |
| generator | 22 |
| exact | 1 (setCurrentGameTaskCallback; the certificate's pairing stage) |
| unexplained | 0 |

- The first run routed drawCharacterSelectCourseExitPreviewPanel to `certify` through a `symbol_name` row, but it was
  not function-exact: a different symbol, not a spelling. `EXPLAINED` was narrowed; the rename lever now fires on it.

**Lever outcomes** (22 generator routes):
- **Object exact, 2:**
  - guMtxIdent (unused object). **Whole-ROM verified** in the integration dry run
    (`object_exact_integration_dry.py`: rom_exact).
  - drawCharacterSelectCourseExitPreviewPanel (symbol rename). Blocked at `prepare_integration`: shared declarations.
- **Function exact (schema 3), 6:** func_8005A884, func_8005CF60, updateEndingObjectSpriteDebugViewer, func_8005C14C,
  func_8005D558, func_8005AC44. All six are blocked at `prepare_integration`: shared declarations (local
  typedefs; `address_integration_dry.jsonl`).
- **Other:** 3 more rodata rewrites improved the score without reaching function exact. 11 unused-object deletions
  changed nothing: the `.bss` went away but visible `.text` faults remain.

Everything above is in the trial DB only. The campaign runs a frozen snapshot, so deploying needs an amendment.

## Reading
- The diff does hide things, but mostly harness and certificate artifacts (IDO `.rodata` padding and alignment, jump
  tables, string-literal ownership), not wrong C. The one C-level hidden fault class is stray file-scope objects.
- The first reading ("extend the certificate for string literals") was wrong. The refusal is deliberate: a literal
  once passed and then failed the ROM checksum. The certificate's own message pointed at the right fix, "needs its
  named symbol", which is a C edit using the target's label.
- The next wall is not rodata: 7 of the 8 wins stop at the preparer's shared-declaration limit (local typedefs),
  the same limit the 9/14 notes count at 107 exact candidates.

## Linking follow-up (2026-09-30, later)

`prepare_integration` now admits three file-scope shapes outside the function: brace typedefs, object-like `#define`
aliases and `const char NAME[N] = "literal";` objects (`_extract_preamble`; tests in `tests/test_prepare_integration.py`).
Preparation of the six function-exact rewrites then succeeded, and all six **failed at link**: `undefined reference to
D_800E...`. The `extern` label is defined by no linker script and its bytes were only ever emitted by a TU literal, so
the extern form is a certificate-only artifact. The original inline-literal sources, through `linking_probe.py`
(certificate bypassed, disposable copy, trial DB read-only):

| function | whole ROM |
|---|---|
| updateEndingObjectSpriteDebugViewer, func_8005D558, func_8005AC44, func_8005905C | rom_exact |
| func_8005A884, func_8005CF60 | rom_exact (needed the string-object shape) |
| func_8005C14C | build_failed: its own source aliases strings to `D_800E...` by `#define` + `extern`; unlinkable |

Open decision: these sources are not function_exact (`object_sections_differ`: IDO `.rodata` padding/alignment), and
`prepare()` requires a source-bound exact certificate, so they cannot enter the campaign as-is. Whether a whole-ROM
`rom_exact` on a disposable copy should stand as the verification for TU-local data candidates is a status-machinery
change, not made here.

**Batch:** `linking_probe.py batch` puts the six ROM-exact functions in ONE combined build (2 TUs: `ending_credits_ui.c`
and `race_ui_effects.c`, four functions sharing the latter and its typedefs): `rom_exact`, whole_rom_verified True.
