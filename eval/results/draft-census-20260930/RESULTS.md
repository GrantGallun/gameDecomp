# Draft census: does m2c's draft shape predict what the pipeline solves?

`python -m eval.draft_census` (`eval/draft_census.py`). For each of 2,066 functions: a fresh assembly-only m2c draft
(`solver.m2c_input.draft`, no context), compared with the reference decomp's body on shape features.

The reference is only a grader here. It measures a tool, m2c, and is written to no ledger, draft, prompt or ranker.
"Solved" means an exact of our own in either ledger, or integrated / pending integration; recovered and
reference-copied exacts are excluded. 2,047 were compared: 7 draft bodies not found, 6 m2c failures, 6 reference bodies
not found.

"Control shape equal" means the draft has the same number of each of these as the reference: if, else, for, while, do,
switch, goto, &&, ||, ?: and return.

## Size-stratified (the pooled ratio would mostly measure size)

| instructions | solved: n, shape equal | unsolved: n, shape equal |
|---|---|---|
| 0–30 | 619, **88.9%** | 96, **64.6%** |
| 30–80 | 392, **86.7%** | 292, **56.8%** |
| 80–200 | 70, **70.0%** | 374, **34.0%** |
| 200+ | 11, **36.4%** | 193, **10.4%** |

At every size, the draft's control shape agrees with a correct source 25–36 points more often for solved functions
than for unsolved ones. That is consistent with draft shape being a binding constraint. It does not prove it: a
function whose draft is right may also be easier for other reasons. The causal test is to fix shape at draft time and
measure.

## Where unsolved drafts differ (955 unsolved; share of functions with any difference, draft-minus-reference mean)

| feature | differs | draft has more | mean delta | solved differs |
|---|---|---|---|---|
| goto | 19.0% | 17.7% | +0.45 | 1.8% |
| ternary | 26.3% | 24.4% | +0.48 | 5.6% |
| if | 35.5% | 23.5% | +0.63 | 4.7% |
| for | 21.4% | 0% | −0.43 | 1.0% |
| while / do | 26.8% / 27.3% | 17.6% / 19.7% | +0.23 / +0.29 | 1.9% / 2.0% |
| && | 13.1% | 3.8% | −0.05 | 1.2% |
| break | 8.6% | 0.4% | −0.18 | 0.5% |
| statements | 90.3% | 73.9% | +20.8 | 39.3% |
| locals | 78.7% | 63.7% | +6.5 | 28.9% |

Gotos appear in 19.8% of unsolved drafts but only 3.6% of their references. m2c never writes `for`, and the reference
uses it in about a fifth of unsolved functions. Ternaries are the known if/else select flattening. The extra statements
and locals are m2c temporaries.

## Reading

- About 580 unsolved functions (61%) have a draft whose control shape differs from any correct spelling. Shape repair in
  the search (branch_shape, counted_loop) works one gated edit at a time. Normalising the draft once, before the search,
  has not been tried.
- The reference lists these classes but must not become the fix. Rules have to come from the compiler, the assembly,
  and our own solved pairs (draft vs our exact for the ~150 solved functions whose draft shape was wrong).
