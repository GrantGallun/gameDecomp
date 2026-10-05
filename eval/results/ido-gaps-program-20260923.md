# Program: model IDO 5.3's remaining gaps and build the allocator inverter

Standing method (memory ido-understanding-campaign): PROTOCOL.md before any test; confirm on IDO 5.3 (uopt traces,
`cc -S`, paired synthetic compiles); verdicts say untestable when unmeasured; mechanisms only from confirmed rules,
fire tests from real residuals; paired population rerun, installed only if 0 exacts lost.

| # | Gap | Instrument | Output |
|---|---|---|---|
| 1 | Allocator preference: what gives a live range its preferred colour | traced synthetic compiles (block-row preferences) | rule for creating/removing preferences |
| 2 | Allocator inverter v1 | diagnosis + traces + rules 1 and uopt53-* | diagnosis-driven edits for priority, store order, preference |
| 3 | Range splitting and materialisation order | traces (`split out`), `-S` order of moves | rule for the five 99.936 siblings |
| 4 | Frame layout (locals/spills) | `-S` `.frame` and symbolic sp offsets | predictive frame model -> frame-size mechanism |
| 5 | as1 scheduling | corpus of (`-S` order, object order) pairs | scheduling rule -> which ugen order yields the target order |
| 6 | Branch and block layout | `-S` control flow vs target | loop/if shape rules -> structural mechanisms |
| 7 | uopt rewrites (strength reduction and kin) | `-S` vs m2c draft shapes | detector + inverse mechanisms like `index_form` |

Status is appended below as each item finishes.

## 1. Allocator preference: done (2026-09-23)
Argument k -> preference a(k), 4 of 4; arithmetic-only -> none; return -> no v0 preference (refuted); call result ->
no range (untestable). Catalog `uopt53-arg-preference`. `eval/results/alloc-preference-20260923/`.

## 2. Allocator inverter v1: built, 0 exacts; its action is now measured (2026-09-23)
`solver/alloc_inverter.py` (raise / earlier / inline), `eval/results/alloc-inverter-20260923/`.
- v1 run (3 rounds, 206 unsolved): 186 compiles, 7 functions improved, 0 exacts.
- The action, not the score (`action_trace.py`, trace-only: does x's range get the wanted register?): v1 18/113
  candidates in 6/15 functions. The diagnosis-based check (`action_check.py`) declined on 83/113 and 43/43
  candidates (attribution breaks on the edited source), so it cannot measure this operator.
- Why most raises fail (`blocked_check.py`, `sites.py`): after constant or copy assignments the empty test is
  folded, so it adds blocks (lowering priority) and no read. H16/H16b (`eval/results/empty-test-reads-20260923/`):
  +1 save after load / computed / call in straight-line code; propagated after constant and copy.
- Fixes: raise only on `blocked` ranges (a `selection` range had its colour free); skip constant and local-copy
  sites. A first version also gated conditional-arm sites from H16b's one synthetic shape; it dropped two real
  successes (freeRelocatableHeapBlock, func_80058C00), so that gate was removed, and globals are loads, not copies.
- Census of the wrong ranges in the 134 diagnosable unsolved functions: blocked 130, selection 104, split 64,
  ugen_temp 33. As the first wrong range: selection 35, split 24, blocked 18, ugen_temp 11. `selection` is the
  larger lever and needs the inverse of the lowest-free-colour scan: another range must hold the lower colour first.

## 4. Frame layout: rule confirmed, one exact, locals-area gap narrowed (2026-09-23)
`eval/results/frame-size-20260923/`, catalog `ido53-frame-layout`.
- H15 refuted, H15b 21/22, H15c 12/12 on fresh constructs: declared locals take virtual offsets in declaration
  order (scalars at natural alignment, aggregates at 4); regions outgoing / saves (ra on top) / locals, each
  rounded to 8.
- Frame census: the candidate frame differs from the target in 60 of 204 unsolved functions; sp lines are 16% of
  all changed lines.
- finishCurrentRdpTask: inlining the register local temp_v0 shrank the frame 0x28 -> 0x20, giving an exact
  object; two rounds of frontend_type_repair (prototype, then the new rule typing parameters from the gate's
  `passing 'T *'` diagnostics) passed the gate. Recorded independently: receipt 95886, 377 -> 378.
- Population trial (inline a local when the frame is too big): 36 functions, 156 candidates, frame fixed in 4
  functions, score up 28 / down 63, no further exacts. Not installed.
- Which register locals own frame bytes (H17 on the 1,505-procedure census, fit/check halves): the depth rule
  explains 75.6% of the check half, and counting vreg locals with an uncoloured piece gives 79.3%. Not confirmed.
  The rest are some caller-saved-coloured vreg locals; the selector is open (next test: range spans a call).
  Also open: the save region is sometimes padded 8 below the saves (s0+ra in a 32-byte frame with no locals).
  Instrument lesson: measure a region from its slots, never by subtracting models of the other regions.
- Same trace instrument across three versions (`action_trace_v1.json`, `action_trace_v2.json`, `action_trace_v3.json`):
  v1 18/113 (6 functions), v2 with the arm gate 12/43 (4 functions), v3 (current) 18/85 (6 functions). v3 compile loop
  (`log_v3.json`): 90 candidates, 5 functions improved, 0 exacts.
- `selection` opportunity (`selection_census.py`): 104 selection ranges; 57 have another range z whose target
  register is x's actual one, 46 interfere with x, 32 are coloured after x, 23 (17 functions) are locals in the same
  outcome class. In those z is mostly `blocked` by x itself: the same conflict seen from z, which the existing
  raise/earlier operators already target through z. A separate "yield" operator would duplicate them; not built.
  The other 47 have no range wanting x's register, so the cause there is not ordering (preferences or an
  unattributed holder; open).

## 6. Branch and block layout: studied, five mechanisms, trial running (2026-09-24)
`eval/results/branch-layout-20260924/`, `solver/branch_shape.py`, catalog `ido53-select-keeps-arm-paths`,
`ido53-o1-local-frame-epilogue`, `ido53-return-tail-per-statement`, `uopt53-loop-exit-test-rewrite`,
`ido53-struct-copy-loop`.
- Census: of 204 unsolved functions, **143 have the target's control-flow skeleton exactly**. The "146 with branch
  faults" counted offset knock-on. 61 are structural. No function differs in jump table against chain.
- Rules: the if/else select keeps one path per arm (m2c flattens it); at -O1 a local makes a frame and `b` returns
  (4/4); one return tail per return statement; the `<` -> `!=` exit-test rewrite is skipped in some loops (the
  general rule was refuted, adjacent reuse is confirmed); a struct assignment gets IDO's own copy loop.
- Fire tests: drawTrainingCourseLessonEndMenu, copyGfxCommandBlockToScratch and __MusIntFindChannel reach 100.0 by
  mechanism; the four -O1 io functions reach about 98.
- Paired trials: code-v10 (four families) lost 0 and gained 0, with 16 improved; code-v12 (six families plus site
  ordering) lost 0 and gained 3 exact in round 4 (__MusIntFindChannel, copyGfxCommandBlockToScratch,
  drawTrainingCourseLessonEndMenu). Recorded 378 -> 381 (SOLVED 275).
- Later the same day, three more closures from the same method:
  - **Gap 3, the five 99.936 siblings** (H6 confirmed 3/3, `eval/results/counter-loop-20260924`): uopt derives a counter
    loop's offset and index variables itself, so hand-derived variables compile differently. `counter_loop` rewrites
    them as one counter; 381 -> 386. The earlier "zero materialisation order" explanation was the symptom, not the cause.
  - **m2c `var_at`** (`at_inline`) and an **evidence_site silent decline** (relocation addends were dropped), in
    `eval/results/at-inline-20260924`: MusStop, MusHandleSetFreqOffset/Pan/Volume, resumeGameTask; 388 -> 393.
- Open in gap 6: the retargeted class (as1 delay-slot choices downstream of data flow, gap 5 territory), the -O1
  address temp (t6 vs a0), stepMainMenuSceneModelAnimation's return select, and the loop-exit selector.
