# Small unresolved functions: causes and next methods

This is a read-only analysis of immutable checkpoint 13212. No live candidate,
campaign policy, build, or verdict was changed. Source/attempt hashes, selected
diffs, target assembly and binary data references accompany this report.

## What “isolated” means here

For an explicit, reproducible screen, small means at most 256 target bytes;
tiny means at most 128 bytes. A weak layout cluster has at most three members.
These inferred clusters are not established original translation units.

There are 78 unresolved small functions in weak clusters, including 40 tiny
ones. Sixty-six have recorded callers; only seven have neither recorded callers
nor callees. Separately, 44 small unresolved functions have no recorded call
edges, and only seven overlap the weak-cluster group. A small rectangle or
singleton cluster therefore does not establish lack of useful context.

The 78 split into 62 pending and 16 parked. Thirteen have no selected attempt,
seven selected candidates fail compilation, and 58 compile. There is also
one compiler-accepted but frontend-rejected candidate,
`_allocatePVoice`, with integer fields being used as `ALLink *`.
Semantic receipts
include ten observed passes, 23 passes with execution debt, nine observed
failures, 15 unavailable and 21 absent. These are finite modeled tests, not
universal correctness. Eleven parked routines explicitly need a hardware
instruction backend. Fourteen of the 15 unavailable semantic reports require
a hardware-register environment. Blocker categories overlap.

## Assembly patterns with different causes

| Observed case | What is stopping it | Appropriate method |
|---|---|---|
| `strlen`, 40 B, 98.5 | Loop register assignment differs (`t7`/`v0`) | Small compiler-guided enumeration of cursor/return forms, types and lifetimes; preserve semantics and verify complete object |
| `osAiGetLength`, 12 B, 96.667 | Address-base register differs; hardware-register environment unavailable | Search small volatile load forms, with a read-event model; register choice and device modeling are distinct issues |
| `__osSiRawReadIo`, 80 B, 99.474 | Named address relocation versus literal address representation | Recover and bind addresses from original encodings; verify linked bytes and relocation effects |
| `osCreateMesgQueue`, 44 B | Text bytes match, but HI16/LO16 pairing metadata differs | Inspect linker-applied relocations at actual addresses; do not promote from disassembly or text hash alone |
| `drawRaceMotionAnimationDebugViewerMotionNumber`, 72 B | Text matches; target has 16 B local rodata while candidate uses an external format-string symbol | Reconstruct binary data ownership/string placement and verify section and linked-ROM identity |
| `fadeOutMultiplayerCourseSelectMenu`, 200 B | Normalized listing matches; section sizes and relocation admissibility differ | Audit function boundaries, padding and relocation grouping, then isolated link verification |
| `initRaceCameraCourseStart`, 72 B, 99.412 | Callback field at offset 0 instead of target 0x2c; 60 recorded failing cases | Recover callback/object layout from uses and address-taken tables, then replay counterexamples |
| `getRacePlayerPathOffset`, 148 B | Target course*16 + player*4 pointer table and signed-byte load; candidate dimensions/width differ | Lift effective-address expressions and load widths into typed constraints; test distinct rows and nonzero selectors |
| `guPerspective`, 88 B | Candidate treats `sp28` as a global; target passes a stack matrix at sp+0x28 | Callsite ABI, stack-object size and argument producer analysis, followed by differential replay |
| `alResampleParam`, 236 B | Target has a nine-way jump table; candidate is largely a dummy | Enumerate target dispatch cases and complete missing paths; ordinary polishing cannot recover omitted logic |

The broad residual flags overlap: 46/78 carry register-allocation signals,
33 have stack-related changes and 20 have relocation signals. These are
diagnostic flags, not 46 proven register-only bugs. Counting `lw`, `sw` or
`addiu` is less informative than asking whether each changed operation touches
a stack spill, a structure field, a callback pointer, a device register or an
unresolved symbol.

An example target leaf (`osAiGetLength`) is just:

```asm
lui t6, %hi(AI_LEN_REG)
jr  ra
lw  v0, %lo(AI_LEN_REG)(t6)   # return delay slot
```

That is well suited to bounded source-template search. It also needs a device
read interpretation if behavior is to be compared. A three-instruction body
does not mean that all necessary context is inside those three instructions.

## Evidence beyond diffs

**Binary function-pointer tables.** Hash-pinned `libmus/player.data.s` contains
addresses of Fcutoff, Fendit and Fdrums. Pinned race-camera data contains several
camera initializer addresses that have no recorded direct call edges. These
prove address-taken table membership, not the exact dynamic caller or ABI.
Combine them with the dispatcher's index calculation and indirect call to
establish those relationships. This should complement the current call graph.

**Callers and argument producers.** `sprintf` has 40 recorded callers;
`__osSiRawStartDma` has eight. Caller register definitions, stack arguments,
return-value uses and pointer increments can constrain types and hidden
arguments. Use these as evidence-bound hypotheses and verify candidates;
names or prototypes alone are not proof.

**Data layout and memory access expressions.** Preserve the target's complete
address expression, width and signedness. Give distinct table rows distinct
contents in tests. All-zero data can conceal a swapped dimension or wrong
stride. Track stack objects passed to callees rather than comparing opaque
pointer numbers alone. Existing dataflow/differential machinery provides part
of this evidence; missing callee effects and extents must stay explicit.
For example, `alSavePull` attempted 1,005 target inputs without completing one:
its pointer/object chain faults before the callback. Capture a valid object
graph and the callback's returned buffer contract before increasing test count.

**Object and linker records.** The three normalized-listing matches above
are particularly strong reasons to inspect sections, symbol addresses,
relocation pairing and final linked bytes. The fix is a narrower, demonstrated
verifier interpretation or corrected data placement, not weaker exactness
criteria. Keep source/recipe/object/ROM bindings and the ratchet.

**Hardware and entry-state contracts.** MMIO requires address/width/order-aware
read/write events and, for busy polling or DMA, device state transitions.
Treating registers as generic zero-filled RAM is insufficient. Separately,
CP0, cache and TLB operations already identified by sdk_intake need an admitted
assembly/intrinsic backend or explicit preserved-assembly category. Some tiny
entries may be padding or special entry stubs; verify boundaries before asking
for another C draft.

**Compiler context and small exhaustive searches.** Freeze the actual compiler
recipe. For a pure tiny leaf, enumerate a bounded grammar of equivalent C
forms (signedness, casts, cursor forms, temporary lifetimes, expression order),
deduplicate resulting code shapes, and validate each emitted object. A future
bit-vector equivalence stage can strengthen pure-leaf comparisons under an
explicit ABI/memory model. It cannot replace hardware or whole-program context.

## Recommended order

1. Audit the three matching-listing object/relocation blockers with the actual
   linker. These already have unusually narrow residuals.
2. Add binary address-taken/dispatch relationships and targeted caller/layout
   packets, prioritizing the callback and pointer-table failures above.
3. Run a bounded tiny-leaf compiler search on passing, frontend-clean cases
   such as strlen; measure new exact functions per compile, not score churn.
4. Build separate MMIO and special-instruction paths with honest coverage
   status, so ordinary C repair does not spend repeated budgets on unavailable
   backend capabilities.

This analysis identifies testable next steps, not new matches. No ground-truth
function C was used. See `diff-audit.json`, `diff-summary.json`,
`beyond-diff-evidence.json`, `binary-data-callback-refs.json` and `topology.json`.
