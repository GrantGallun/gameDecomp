# Branch and block layout (gap 6): protocol

Written 2026-09-24, before the tests below were run. Amendments are labelled and dated.

## Census (exploration, `census.py`, `markers.py`, `bnext.py`)
The 204 unsolved functions of the restart round-3 run, best node each. The candidate dump is rebuilt by applying the
node's recorded diff to the target dump, and every context and deleted line is checked against the target. All 204
apply. An earlier round-trip check that re-diffed with difflib rejected 92 of them, because difflib hunks differ from
GNU diff. That was an instrument bug, not bad data.

- 143 / 204 have an identical control-flow skeleton (branch opcodes, and targets as block ordinals). Their branch diff
  lines (75 functions) are offset knock-on from instruction count changes elsewhere, not branch layout. The "146
  functions with branch faults" figure counted these.
- 61 are structural: count 32, retargeted 14, loop-shape 6, likely 3, other 3, delay-fill 3 (identical once as1's
  fill-from-target, which leaves a dead copy and branches past it, is undone).
- Unconditional branches the target has and the candidate lacks: to the next block 8 functions (menu draw functions,
  several at 88-98%), to the epilogue 10 (4 are -O1 libultra: `__os*DeviceBusy`, `__osSpSetPc`, `osCartRomInit`).
- `goto` appears in 15 / 61 structural sources against 11 / 143 same-skeleton ones.

## Exploration before the protocol (not evidence for the hypotheses)
- E1 `explore_select.py`, a select inside a loop. `if (c) x = B; else x = A;` and `x = c ? B : A;` give the target shape
  `li A; b!c join; nop; b join; li B`. `x = A; if (c) x = B;` (m2c's rendering) gives `li A; b!c join; nop; li B`.
- E2 `explore_o1.py`, at -O1: a declared local gives a leaf an 8-byte frame, and returns go through `b epilogue`. A
  `register` local sits in a0 with no store. With no local there is no frame and each return is `jr ra`. At -O2 none
  of these forms has a frame.

## Hypotheses and predictions (fresh constructs, not the explored ones; `test.py`)
Game recipe (IDO 5.3, the flags of a src/race TU), -O2 unless stated. "b-next" means an unconditional `b` whose target
is the instruction after its delay slot.

H1, select as if/else. An if/else (or ternary) whose arms each assign one variable keeps a `b` to the join. The
default-then-override form does not.
- P1a: straight-line, `int f(int a, int b) { int x; if (a == b) x = 7; else x = 3; return x + a; }`. Exactly one b-next;
  `li ?,3` comes before the conditional branch; `li ?,7` is in the b delay slot.
- P1b: `x = 3; if (a == b) x = 7;` has zero unconditional branches.
- P1c: the ternary `x = (a == b) ? 7 : 3;` gives an instruction list identical to P1a.
- P1d: computed arms (`x = a * 3` / `x = b + 100`). The if/else form has an unconditional `b` to the join; the default form
  has none. Delay-slot contents are not predicted.
- P1e: arms storing a global (`if (a == b) g = 7; else g = 3;`) have a `b` to the join; `g = 3; if (a == b) g = 7;` has none.
- P1f: empty else, `x = 3; if (a == b) { x = 7; } else { }`, is identical to P1b.
- P1g: if / else if / else over three constants has exactly 2 unconditional branches.

H2, the -O1 frame and epilogue.
- P2a: -O1 leaf `int f(int a) { int t = a * 3; if (t > 10) return 1; return 0; }` has `addiu sp,sp,-8`, exactly one
  `jr ra`, and at least one `b` to the epilogue.
- P2b: the same function without the local (`if (a * 3 > 10) ...`) has no `addiu sp`, and `jr ra` appears 2 times.
- P2c: with `register int t`, frame -8 and no `sw` of t.
- P2d: -O1 leaf with three int locals, all used, has frame -16 (align8(4 * 3)).

H3, one return tail per return statement (leaf, -O2).
- P3a: a loop that returns -1 from two places (`if (p[i] == k) return -1;` inside, `return -1;` after) has 2 copies of
  `li v0,-1`.
- P3b: the same with one `return -1` reached by `break` or fall-through has 1 copy.

H4, loop forms (-O2, leaf with a call in the body).
- P4a: `for (i = 0; i < n; i++)`, `while (i < n)`, `if (n > 0) do {...} while (i < n)`, and the goto form
  (`if (n <= 0) goto end; loop: ...; if (++i < n) goto loop; end:`) give identical instruction lists.
- P4b: with a constant bound, `for (i = 0; i < 3; i++)` and `do {...} while (i < 3)` from i = 0 are identical (no guard).

Verdicts: confirmed / refuted per prediction. "untestable" when the property cannot be read from the output (for
example, no instruction to check). A hypothesis is confirmed only if all its predictions hold.

## Use, if confirmed
- H1: a mechanism that turns `x = A; if (c) x = B;` into if/else, only when the target has a b-next whose delay slot
  sets the register the pre-branch instruction also sets. It needs a fire test on drawTrainingCourseLessonEndMenu
  (0x3cc), then the paired population rerun.
- H2: for -O1 functions, introduce a `register` local when the target has a leaf frame of 8 and `b` returns. Fire test on
  `__osAiDeviceBusy`.
- H3: merge m2c's "Duplicate return node" tails when the target has one tail. Fire test on `__MusIntFindChannel`.
- H4: if confirmed, loop form is not a lever and m2c's goto loops are harmless in themselves. If refuted, the
  differences are catalogued.

## Results (2026-09-24, `test.py` -> `results.json`)
- **H1 refuted as registered** (P1b, P1c, P1f hold; P1a, P1d, P1e, P1g fail). In a frameless leaf the join is the
  return block, and IDO duplicates it into each arm (`jr ra; <delay>` per arm) instead of emitting `b join`. The if/else
  and ternary forms get one exit path per arm; default-then-override gets one. That difference survives, but its form
  depends on what the join block is, so the b-next predictions were wrong. The ternary equals if/else (P1c), and an
  empty else equals no else (P1f).
- **H2 confirmed** (4/4): at -O1 a declared local gives a leaf a frame of align8(4 * locals), and every return then
  branches to one epilogue; a `register` local has the frame and no store; with no locals, each return is `jr ra`.
- **H3 refuted as registered** (P3a counted 5 copies of `li v0,-1`, not 2). The construct was confounded: IDO unrolled
  the loop 4x and copied the in-loop return into each unrolled body (4 + the final one). P3b (break form, 1 copy)
  holds.
- **H4 refuted** (P4b holds; P4a fails). `for` equals `while`. Guarded do-while differs only in delay-slot scheduling
  (the `i = 0` sits inside the guard). The goto loop keeps `slt at,i,n; bnez at` where structured loops get
  `bne i,n`: uopt rewrites the exit test `<` into `!=` only in loops it recognises. So loop form is visible, and the
  exit-test opcode is its fingerprint (drawTrainingCourseLessonEndMenu: target `slti/bnez`, candidate `bne`).

## Amendment A1 (2026-09-24, written after the results above and before `test2.py` ran)
Exploration since then (not evidence): E3/E3b/E3c/E3d (`explore_exit.py`, `explore_exit2.py`, scratch). With a
constant bound every loop form tested gets `bne`, including a goto loop. `slti/bnez` is kept for a narrow (short/u8)
loop variable (which adds sign/zero-extension, unlike the target), for an unknown start value, and when **the same
variable drives a later loop**: the earlier loop keeps `slt`, the last one gets `bne`. A plain use after the loop
does not block the rewrite.

H1' (replaces H1's b-next claim): the if/else select keeps one path per arm to the join. When the join is ordinary code,
this is a `b join` with the then-value in its delay slot and the else-value before the conditional branch. The
default-then-override form has one path.
- P1'a: `void f(int a, int b) { int x; if (a == b) x = 7; else x = 3; g(x, a); }` (join = a call) has exactly one b-next;
  `li ?,3` comes before the conditional branch; `li ?,7` is in the b delay slot.
- P1'b: the default form of P1'a has no unconditional branch.
- P1'c: non-leaf with a frame, `int f(int a, int b) { int x; h(); if (a == b) x = 7; else x = 3; return x + a; }`.
  (unconditional branches + `jr ra` count) for if/else is exactly one more than for the default form.

H3' (replaces P3a): no unrolling, because of a call in the body. `int f(int *p, int n, int k) { int i; for (i = 0; i < n;
i++) { h(); if (p[i] == k) return -1; } return -1; }` has 2 copies of `li v0,-1`; the break form has 1.

H5: uopt rewrites a loop's `<` exit test into `!=` unless the same variable is the induction variable of a later loop.
Constant bounds, int variables, calls in the bodies.
- P5a: three sequential loops reusing `i` (bounds 5, 7, 9, step 1): loops 1 and 2 are `slti/bnez`, loop 3 is `bne`.
- P5b: the same with three distinct variables: all three `bne`.
- P5c: loops i, j, then i again: loop 1 `slti`, loop 2 `bne`, loop 3 `bne`.
- P5d: an outer loop i with an inner loop j, then a later loop reusing j: inner `slti`, outer `bne`, last `bne`.

Use: for H5, merge m2c's split induction variables (`var_s1`, `var_s1_2`, ...) that share a register in the target when
the target keeps `slt` on the earlier loop. Fire test on drawTrainingCourseLessonEndMenu.

## Results of A1 (`test2.py` -> `results2.json`)
- **H1' refuted as registered** (P1'b, P1'c hold; P1'a fails). The `b join` is present, but the else value stays in its own
  block: the conditional branch's delay slot was filled from above (`move a2,a0`), so nothing hoisted the else
  assignment. Where the else value lands is scheduling, not C. What held in every H1/H1' construct is the path count:
  if/else (and the ternary) has one more path to the join than default-then-override (P1'c: 2 vs 1; P1a/d/e: 2 `jr ra`
  vs 1).
- **H3' confirmed** (2/2): one return tail per return statement; the confound in H3 was unrolling.
- **H5 refuted** (P5a, P5b hold; P5c, P5d fail). Loops i, j, i all get `bne`; in the nest the outer loop also keeps `slt`.
  "Reused by a later loop" is not the selector. Adjacent reuse of one variable (P5a and two explorations) does keep
  `slt` on the earlier loops, but the rule behind it is open. It may be allocator-coupled (whether the bound gets a
  register); that is untested.

## Fire tests on real residuals (2026-09-24; `fire.py`, `fire_module.py`; not population evidence)
Instrument note: `fire.py` first called the workspace's `build.sh`, which defaults to -O2, so -O1 functions were
compiled with the wrong recipe (__osAiDeviceBusy base 14.6 against a recorded 65.8). It now runs the recorded
`.compiler-*.sh`, and every base score reproduces the round-3 best.
- drawTrainingCourseLessonEndMenu: the select as if/else fixes the unconditional count (2 -> 4); merging m2c's
  var_s1 webs restores the target's `slt` loops; together **100.0**. Merging the s0 webs costs one instruction, so
  not every split is a merge.
- H2 (register local, -O1): __osAiDeviceBusy 65.75 -> 98.33, __osSpSetPc 67.6 -> 97.7, __osSi/SpDeviceBusy 62.6 ->
  98.2. The remaining diff: the target holds the address in t6 and loads into a0; the direct extern read uses a0 for
  both. `*(volatile s32 *)&G` adds an instruction (91.5). Open.
- osCartRomInit: an early return in m2c's empty then-arm, 83.6 -> 94.3, skeleton now identical.
- E4 (struct copy): `*(Blk *)d = *(Blk *)s` reproduces the target's copy loop instruction for instruction; a word
  loop does not. copyGfxCommandBlockToScratch 42.0 -> **100.0**.
- H3' (one tail per return): __MusIntFindChannel, m2c's marked duplicate `return -1` routed to the final one,
  90.864 -> **100.0** (goto-inverted and do-while forms alike).
- Module run (`solver/branch_shape.py`, first four families, greedy three rounds, every unsolved function): 18
  functions got a proposal and 17 improved (`fire_module.log`). Among them: updateEndingLindaHandshakeLoop 76.4 ->
  96.1, freeRelocatableHeapBlock 78.6 -> 95.0, Fstartfx 47.2 -> 66.4, drawControllerPakDeleteConfirmPrompt 96.2 ->
  98.4, drawRaceSetupPlayerCountPrompt 94.9 -> 96.9.
- Target-only b-next sites (`bnext_sites.py`, else value above the branch or in its delay slot): 11 of 12 are selects.
- Retargeted class (same opcodes, different targets): calculateRaceTimerDelta and fixedSine are as1 delay-slot
  choices that follow a data-flow difference (the target reuses one variable where the candidate has two). That is
  scheduling downstream of registers, not C control flow.

## Amendment A2 (2026-09-24, before `test3.py` ran): derived induction variables (gap 3 / gap 7)
Exploration (not evidence), `sib_variants.py`: the five 99.936 siblings differ from the target only in the order of
two zero initialisations (`move s2,zero; move s3,zero`). The target holds the second loop's bound 0x80 in `s0`, the
register of the first loop's counter `i`. Rewriting the second loop as `for (i = 0; i < 2; i++)` with `i * 0x40` for the
offset and `[i]` for the index gives **100.0** in 3 of the 5 (the other two have different field names; the ad-hoc
script failed to compile them). `i << 6` gives 97.2. Earlier, 25 compiles reordering the hand-written
initialisations never moved the order (register-steer-20260923).

H6: uopt strength-reduces a counter loop into derived induction variables in its own order, and hand-written derived
variables are not equivalent.
- P6a: `for (i = 0; i < 2; i++) g(p[i], i * 0x40);` and the hand-derived `t = 0; o = 0; do { g(p[t], o); o += 0x40; t++; }
  while (o != 0x80);` give different instruction lists.
- P6b: in the counter form, `i * 0x40` gives no shift inside the loop (a derived variable stepped by 0x40), while
  `i << 6` has an `sll` inside the loop.
- P6c: in the counter form (a call in the body, bound 2), the exit test is `bne` against a register holding 0x80
  (the test moved onto the derived offset), not a compare against 2.
