# Protocol: which IDO 7.1 register-allocator rules hold on IDO 5.3

Written before any test in this directory ran.

## Why
SBK1 is IDO 5.3. The only readable allocator descriptions are for IDO 7.1 (akratch's instrumented-uopt model,
`docs/allocator-model.md` in akratch/ssb64-func_ovl0_800CEF4C-frontier; the n64decomp/ido uopt decompile). A
7.1 rule may only change this project's behaviour after it is confirmed on 5.3 (`patterns/catalog.py`,
`confirmed_on`), because an untested compiler hypothesis encoded as a rule is the one bug shape this project
keeps shipping.

## Evidence
5.3 only: uopt's own trace from the gated patched compiler (`tools/ido-trace/`, byte-identical ROM), levels 5
and 6 of all 115 uopt-compiled SBK1 translation units (`~/decomp/tools-src/uopt-trace-census/traces/`), parsed
by `solver.uopt_trace`. Interventions compile synthetic `syn_*` functions with the same compiler and recipe.

## Hypotheses (7.1 claim -> 5.3 test)
- **H1 priority.** adjsave = save/units, save = 10 x references, units = raw if raw < 3 else ((raw-2)>>2)+2,
  raw = references + block span. Test: for each live range with a finite adjsave, does an integer
  reference count r in 1..400 reproduce it (relative error < 1e-4) given the range's recorded block span?
  Informativeness: the same test with block spans shuffled across ranges.
  **Confirmed** if pass rate >= 95% and at least 30 points above the shuffled rate.
- **H2 order.** 7.1: all webs in descending priority, ties by first source appearance. Test: the fraction of
  consecutive colouring decisions consistent with descending adjsave, for constrained and unconstrained
  decisions separately; for equal-adjsave constrained pairs, the fraction in increasing live-range number.
  **Confirmed** for a decision kind if >= 95% consistent; the 5.3 alternative (unconstrained in live-range
  order) is scored the same way.
- **H3 selection.** 7.1: lowest free register not taken by an overlapping web. Test: agreement with the chosen
  colour, band read from the chosen colour as `select_colour` does, against the existing 5.3 model.
  **Confirmed** if >= 95%.
- **H4 interference.** 7.1: two webs conflict iff they share a live basic block. Test: over range pairs with
  block information, the confusion matrix of (share a block) vs (listed as interfering).
  **Confirmed** if both conditional rates are >= 95%.
- **H5 steering (interventions).** (a) `if (!x);` raises x's adjsave exactly as one more reference under H1;
  (b) a bare expression statement `x;` changes nothing (object and trace identical). Paired compiles on
  synthetic functions. **Confirmed** if every pair behaves as predicted.

A refuted rule is recorded with its counterexamples. Only confirmed rules enter `patterns/catalog.py` with
`confirmed_on`; nothing here changes solver behaviour.
