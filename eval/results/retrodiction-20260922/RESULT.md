# Retrodiction: exploring potential rediscovers mechanisms whose fix the evidence states, and only those

Four existing mechanisms were hidden (`owner:drop_mask`, `owner:layout`, `owner:per_object_layout`,
`owner:global_load_signedness`, with every node they produced). A blind potential analysis and one generic
derived mechanism were then run as pre-registered (`PREREGISTRATION.md`, written first). 90 compiles.

## Deviation, stated up front

The first analysis (`signatures-v1-invalid.json`) used `diffrepair.aligned_pairs`, which by design returns only
offset-differing pairs, so every other difference fell through as "unpaired"; and its site lookup never matched
offset pairs. Both are measurement bugs, found by inspecting why two targets were missing. v2 uses the general
aligner (`solver.alignment.align_diff`) and maps candidate-stream positions to dump lines directly. Criteria and
signature definitions were not changed.

## Criterion 1: potential flags where the hidden mechanisms belong. Pass, 4 of 4

| Hidden mechanism | Target signature | Rank of 164 (top quartile <= 41) |
|---|---|---:|
| drop_mask | `extra:andi` | 5 |
| layout, per_object_layout | `field:offset` | 8 |
| global_load_signedness | `opcode:lh/lhu` | 27 |
| global_load_signedness | `opcode:lb/lbu` | 28 |

The ranking also flags many classes no hidden mechanism owns (surplus `addiu`, `sll`/`sra`, `sw`/`lw`, `move`;
`field:immediate`; `opcode:lh/lw`-type width mismatches), so it locates slots; it does not certify every flag.

## Criteria 2-3: the generic "stated value at the attributed line" mechanism

| Hidden mechanism | Parents it succeeded on | Derived candidate for its residual | Derived source identical | Derived matches or beats it (compiled) | Its exacts reproduced |
|---|---:|---:|---:|---:|---:|
| global_load_signedness | 5 | 5 | 5 | **5 / 5** | - |
| layout | 16 | 5 | 0 | 8 / 16 | 0 / 2 |
| drop_mask | 24-27 | 8 | 4 | 7 / 24 | 0 / 2 |
| per_object_layout | 3 | 1 | 0 | 0 / 3 | - |

- **Rediscovered outright:** `global_load_signedness`. The residual (`lb` vs `lbu`) states the fix completely and
  the source map says where; the generic rule wrote the same source in all five cases.
- **Partly:** `layout` where offsets are literal in the source; `drop_mask` where the target really lacks the mask.
- **Not rediscoverable from evidence:** all four hidden exacts. Both `drop_mask` exacts (`randomNextMain`,
  `drawCharacterSelectCourseExitPopup`) had the mask in the target too; the diff showed register differences,
  and deleting the source mask won through register pressure, a side effect the diff never states. The
  `layout` exacts needed struct re-padding, a model of the declaration the generic rule does not have.

## What this says

Exploring potential does make mechanisms discoverable, in a bounded class: where the compiler's diff states the
fix and the source map locates it, a mechanism can be derived rather than hand-written, and it matches the
hand-written one. It locates where every hidden mechanism belongs. It cannot derive what the evidence does not
contain: a declaration model (struct layout) or an allocator effect (register pressure). Those gaps name the
information the machinery lacks, which is itself the next thing to build.

## Forward run: +5 exact (373 byte-exact, SOLVED 273)

The derived rule, plus one piece of generic compiler knowledge added before the run (MIPS spelling of C casts:
`sll`/`sra` by 16/24 = `(s16)`/`(s8)`, `andi 0xffff`/`0xff` = `(u16)`/`(u8)`), was applied at the best node of
every function the population arms left unsolved (`forward.py`, `score_forward.py`):

- 159 candidates in 58 functions; **158 compiled; 72 (45%) beat the search's best-so-far** (base rate for all
  mechanisms 5.3%); 37 functions improved in one compile each. Strongest classes: signedness 6/6,
  `field:immediate` 8/9, surplus `sra` (casts) 13/18, `opcode:lhu/lw` width 5/9, surplus `andi` 9/18.
- Declines name the missing models: 381 of 392 located offset faults have no literal (struct layout), 155 of
  164 immediates have no matching literal, 3,627 register and 491 branch faults are not C-expressible.
- Restarting the ordinary search from each derived best (`run_derived_search.py`, 1,184 compiles) improved all
  37 over search alone (mean +3.65) and took `releaseRelocatableHeapBlockMetadata` 81 -> 97 -> 100 with an exact
  object that failed only the frontend gate.

That exposed a class present in the recorded searches: **byte-exact objects rejected only by the source's
type-validity gate** (`bytes_exact_scan.py`: 5 functions). The frontend diagnostic states the fix, its line
and the types, so it is evidence too (`frontend_fix.py`): cast an int/pointer assignment to the named type
(same-width casts emit no code), `()` -> `(void)`, an implicit call gets a prototype from its call site, and a
pointer stored in an `s32` table retypes the element. All five closed and were independently recompiled and
recorded: `releaseRelocatableHeapBlockMetadata` (SOLVED), `MusAsk`, `MusHandleAsk`, `createGameTask`
(header-assisted), `osGetThreadPri` (counted reference-type-assisted by `eval.status`).

The chain for the first is the whole idea in one function: search stuck at 81.25 -> derived signedness retype
(the diff) -> 97.08 -> search drops a mask -> bytes exact -> retype from the frontend diagnostic -> exact.

## Next

Both derived mechanisms still live in this directory. Promote them into the solver (evidence-at-site as
`regalloc_mutations` families; the diagnostic repair wherever a byte-exact certificate meets a frontend
rejection), with fire tests from the cases above, and rerun the population.
