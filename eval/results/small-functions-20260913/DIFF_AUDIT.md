# Small unresolved functions: source-bound diff audit

Frozen checkpoint **13212**, inventory pointer SHA256
`82407100fe14636ad945148bd906ff4d9fa9b96a7086e99d8f0fd8143257456e`.
`audit-diffs.py` reads only explicitly selected attempt IDs through readonly SQLite,
verifies each complete source hash against the inventory, and retains full assembly
diffs, selected C, compiler diagnostics, semantic feedback and object certificates
in `diff-audit.json`. No reference C was read, no compiler/model was run, and no
live state changed. The inventory and report concern current selected candidates,
not the best imaginable historical candidate or universal correctness.

## Cohorts and obstacles

Among all 623 unresolved functions of at most 256 bytes, **78** occupy inferred
clusters of at most three members; **40** of those are at most 128 bytes.
The primary cohort contains 62 pending and 16 parked functions.

- 13 have no selected C attempt: 11 need privileged/hardware instruction support;
  two hit the current SDK intake's control-flow/relocation limits.
- Seven selected C attempts do not compile. These include missing controller
  structure members, unresolved callback types, malformed timer declarations and
  three parked intake/parser failures.
- One more, `_allocatePVoice`, compiles with IDO but fails the project frontend:
  three inferred integer members are assigned to `ALLink *` without valid types.
- Semantic receipts report 10 observed passes, 23 passes with execution debt,
  nine observed failures, 15 unavailable, and 21 with no semantic result. These
  categories are independent of byte-diff categories and must not be added together.
- Fourteen of the 15 unavailable reports explicitly require hardware-register
  environments. `alSavePull` has no completed target cases.

The separate **44-function no-known-call-edges cohort** has 31 functions at most
128 bytes. Only seven overlap the 78-function primary cohort. Missing direct call
edges are not proof of isolation: callback assignments and dispatch tables matter.

## Three cases where the instruction diff cannot explain the blocker

All three report normalized-assembly score 100, yet correctly remain unverified.

| Function | Bytes | Actual certificate blocker | Useful next evidence |
|---|---:|---|---|
| `osCreateMesgQueue` | 44 | Text section bytes, size and attributes match, but repeated `__osThreadTail` HI16/LO16 pairing differs: target pairs offsets 4→8 and 0→12; candidate 0→8 and 4→12. | Apply both relocation streams in the actual symbol environment, preserving pairing/carry semantics, then perform a disposable whole-ROM link. |
| `drawRaceMotionAnimationDebugViewerMotionNumber` | 72 | Text bytes match. Target owns a 16-byte `.rodata` section and section-relative format-string relocations; candidate uses an external format symbol and owns no `.rodata`. Function-only verifier refuses allocated data. | Recover section ownership and exact constant bytes from binary evidence; verify TU/link output with the real format symbol. |
| `fadeOutMultiplayerCourseSelectMenu` | 200 | Normalized assembly matches, but target text is 224 bytes and candidate 208. Function-only check rejects an unsupported/out-of-range relocation shape. Visible external relocation records match; this alone does not establish a valid boundary proof. | Audit actual function extent, padding and repeated HI16 groups, then link the source in a disposable build. |

Do not promote these using the scalar score or remove the relocation gate. The
existing whole-program checks can answer questions that normalized assembly omits.

## Recurring instruction-level causes

**Named MMIO/absolute-address relocations versus literal addresses.**
`__osSiRawReadIo` (80 bytes, 99.474) uses target `%hi/%lo(D_A0000000)` versus
candidate `lui at,0xa000` / `lw ...,0(...)`. `__osSiRawStartDma`,
`__osSpRawStartDma`, `__osSpSetStatus`, `__osSiRawWriteIo`, and
`osAiSetNextBuffer` similarly differ only in address expressions in their recorded
diffs. The right experiment is source symbol spelling/declaration plus actual
link-address validation. Runtime MMIO modeling is a separate obligation; inventing
random RAM behavior for hardware registers would not supply it.

**Register allocation and lifetime, including tiny leaves.** `strlen` (40 bytes,
98.5) has the same loop but uses `v0` where the target keeps a loaded byte in `t7`.
`osAiGetLength` and `__osSpGetStatus` (12 bytes each, 96.667) use `v0` rather than
`t6` for the address producer. `Fdrums` (40 bytes, no known call edges, 97.5) changes
only pointer/intermediate register assignment. `_ldexpf` (40 bytes, 89.909) introduces
an extra `mov.d f2,f12` because the floating-point argument and result live ranges
differ. These motivate bounded compiler experiments over expression grouping,
argument reassignment, temporary lifetimes and declarations, guided by last writes
and uses. A register mismatch is not automatically a wrong algorithm.

**Frame and callee-preserved state.** `osSetEventMesg` (104 bytes, 72.6) passes all
64 semantic cases, but the target retains the interrupt mask in `s0`, saves `ra`
at stack+0x1c and restores the mask from `s0`; the candidate spills the mask and
saves `ra` at +0x14. The useful unit of repair is the value's lifetime across
`__osDisableInt`/`__osRestoreInt`, not each isolated `lw`/`sw` offset. Many SDK
wrapper diffs share this problem.

**Equivalent scheduling.** `alSavePull` (140 bytes, 98.235) merely swaps adjacent
`ori t2,t2,0x580` and `lui t1,0xd00`. `copyPackedMatrixTranslation` (104 bytes,
96.538, no known call edges) changes expression operand ordering and corresponding
register allocation while passing 64 cases. These are small compiler-shape searches,
not evidence for changing memory layout.

## Cases where beyond-diff evidence already identifies the bug

| Function | Measured evidence | Concrete source issue to test |
|---|---|---|
| `initRaceCameraCourseStart`, 72 bytes, no known call edges | 99.412 score but 60 failed/one passed reported cases. Target stores and reloads `updateRaceCameraCourseStart` at object+0x2c; candidate uses +0. | Correct the callback member offset; use address-taken/callback-table evidence to connect this initializer to its updater. |
| `getRacePlayerPathOffset`, 148 bytes | A recorded course-six branch input returns in target but faults in candidate. | Target indexes course×16 + player×4, loads a table pointer, then a signed byte. Candidate swaps/scales dimensions and loads a word. |
| `updateRacePlayerSmoothedPathOffset`, 232 bytes | 28 failures/36 passes; same table-derived candidate memory fault. | Treat the shared table shape as a binary-derived cross-function constraint; repair array-versus-pointer and element-unit confusion. |
| `guPerspective`, 88 bytes | All 64 call-trace comparisons fail: target passes a matrix at stack+0x28, candidate passes global `&sp28`. | Recover an actual 64-byte stack matrix with proper floating-point element type; bind it to the two callees' buffer roles. |
| `alAuxBusPull`, 216 bytes | All 64 fail. Target emits two audio commands and advances output by 0x10; candidate omits one and advances by 0x80. | Correct command element size, callback pointer indirection and pointer-array stride (target four bytes, candidate 16). |
| `alResampleParam`, 236 bytes | Target has a nine-entry indexed branch dispatch. Candidate is a five-instruction dummy; 56 failures/eight passes. | Reconstruct one executed case and its stores at a time while preserving explicit debt for remaining cases. |
| `lldiv`, 256 bytes | First helper call receives different high/low argument words. | Reconstruct o32 argument alignment, hidden aggregate result and 64-bit word pairing before optimizing the call sequence. |

`__osContDataCrc` additionally fails 63/64 return comparisons; `osGetTime` fails
61/64. Both currently have weak return-only causal feedback (no mismatching calls
or persistent stores), so richer return-producer dependency slices would be more
useful than another broad assembly prompt.

## What the aggregate opcode counts do and do not say

The most frequent changed opcodes in the primary cohort are `lw` (369), `sw`
(339), `addiu` (148), and `lui` (123). Thirty-three functions have changed stack
instructions. Existing descriptive heuristics flag register differences in 46,
structural differences in 45, relocations in 20, immediates in 16, offsets in 13,
width in seven and scheduling in five. These flags overlap and alignment is
heuristic: they are **not** a mutually exclusive fault partition or a claim that
46 functions can be fixed by renaming registers. A missing load or wrong table
shape can create many secondary register and frame differences.

The highest-value split is therefore: proof/linkage-only near matches; genuine
compiler-shape ties; measured semantic bugs; frontend/intake failures; and missing
hardware/backend capability. Cluster size alone does not identify which work is
needed, and no-direct-calls alone does not establish that the function has no useful
neighbors.
