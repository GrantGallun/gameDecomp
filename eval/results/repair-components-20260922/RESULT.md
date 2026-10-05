# Repair components as predictor variables

We completed the three-component factorial table for the existing generated
`__osDequeueThread` draft, using six previous measurements and only **two new
compiler calls**. All eight sources differ only by the declared components,
their receipts and parent edges are audited, and the new probes use the same
target/compiler context. This is a descriptive analysis on one development
function; it does not train or change the solver.

## A concrete mathematical representation

Set each variable to zero or one:

- **P**: add `register` to the local pointer-to-link.
- **N**: add `register` to the local node pointer.
- **A**: reuse the immediately established field address for the next load.

For these eight measured inputs, normalized similarity is exactly represented
at the reported score precision by this polynomial:

```text
score(P,N,A) = 27.600 + 30.000P + 25.567N + 16.771PN
                       + 6.781PA - 3.062NA - 3.657PNA
```

The standalone A coefficient is zero. This is linear in the coefficients, as in
linear regression, while the predictor columns include interaction products.
Coefficients use 0/1 coding relative to all components off. They are not
average effects across every context and are not coefficients in +/-1 coding.

| P | N | A | Normalized score | Certified exact |
| ---: | ---: | ---: | ---: | --- |
| 0 | 0 | 0 | 27.600 | No |
| 1 | 0 | 0 | 57.600 | No |
| 0 | 1 | 0 | 53.167 | No |
| 1 | 1 | 0 | 99.938 | No |
| 0 | 0 | 1 | 27.600 | No |
| 1 | 0 | 1 | 64.381 | No |
| 0 | 1 | 1 | 50.105 | No |
| 1 | 1 | 1 | 100.000 | Yes |

The two new observations are P+A and N+A. They were selected to fill missing
cells before either was compiled, not chosen after seeing a favorable outcome.
The all-three exact was independently confirmed in the prior experiment.

![Measured component interactions](interactions.png)

Without A, a purely additive prediction for both register declarations is
57.600 + 53.167 - 27.600 = **83.167**. The observed result is **99.938**:
the P-by-N interaction contributes **16.771** additional score points.

The effect of A depends on what is already present:

| Existing components | Score change from A | Exact result after A |
| --- | ---: | --- |
| Neither register declaration | 0.000 | No |
| P only | +6.781 | No |
| N only | -3.062 | No |
| P and N | +0.062 | Yes |

On this measured table, the exactness indicator has the simple expression
`exact(P,N,A) = P*N*A`. That is a truth table for these eight sources, **not** a
claim that the formula predicts other functions or that all repairs require
these components. Other source representations can reach exactness too.

This demonstrates why average score gains are an incomplete component ranking.
An apparently inert component can finish a repair in the right context; an
intermediate decrease is not sufficient evidence to abandon it.

## A model that can generalize

Define `s` as the repair state before an action, `a` as its component vector,
and `B` as a fixed remaining compile budget. A useful future model is:

```text
P(certified exact within B | s, a)
  = sigmoid(intercept
            + component effects
            + state effects
            + component × component interactions
            + component × state interactions)
```

This is a proposed regularized logistic regression, not one fitted here. Its
probability would describe uncertainty across repair tasks and search outcomes;
the compiler certificate still determines whether an individual result is exact.
Start with a few mechanistically motivated interactions and shrink coefficients
until there are enough independent task contexts to support more terms.

| Predictor group | Examples available before trying the candidate |
| --- | --- |
| Action components | Register qualification, parameter-copy removal, address reuse, loop-form change |
| Residual state | Extra stack loads/stores, load-base mismatches, unresolved type errors, remaining relocation differences |
| Source structure | Local pointer count, address escapes, branch count, calls, existing aliases |
| Compiler context | Compiler identity, optimization level, ISA, assistance tier |
| Search context | Remaining budget, depth, prior component sequence, generator order |

Do not include post-action score or a later successful descendant as an input
to a decision supposedly made before that action. Those are outcomes or labels.
Function identity can group or block observations but should not become a
memorized shortcut for predicting repairs on unfamiliar functions.

Use separate responses for frontend admission, immediate certificate exactness,
eventual exactness within B, diagnostic residual changes, and compile cost.
An inadmissible draft needs a different next action from a compiling draft with
register differences. A branch exhausted at budget B is unresolved beyond B;
it is not proof that its components can never work. A score of 100 is still not
an exactness label when the certificate rejects relocations.

## How to identify useful effects

1. Record the exact parent state, component toggles, order and cost for every
   intervention, including failures. Existing attempt edges and state hashes
   are the foundation; a function name alone is insufficient.
2. For a small promising component set, run a complete factorial block on the
   same starting state. Three binary components require eight combinations.
   Reuse prior cells only when source and context bindings agree, as done here.
3. Repeat the block on independently selected functions exhibiting the relevant
   residual. Block comparisons by function/compiler context; randomize order
   where execution order could affect results. Cross-function replication is
   what supports transfer claims, not repeatedly compiling one source.
4. Fit a small model on designated research data. Reserve whole functions and,
   where feasible, whole related function families for validation. Do not split
   ancestor and descendant attempts randomly across training and validation.
5. Choose the next experiment partly for information: combinations whose
   outcomes would distinguish competing explanations. Reserve an explicit
   exploration budget instead of always selecting the largest predicted gain.
6. Freeze any proposed policy and compare against the incumbent at equal
   budgets while retaining known successes. An offline association does not
   authorize a behavior change or weaken the compiler gate.

Candidate components and search scheduling need separate experiments. A
transform can generate an exact source but be reached too late, as the previous
parameter-first regression showed. Test direct component combinations first;
then test the scheduling rule with the generator held fixed.

## Limits and reproducibility

Eight observations and eight coefficients form a saturated interpolation: zero
residual degrees of freedom, no meaningful p-values or confidence intervals.
The previous 1,484 calls are not 1,484 independent tasks. Many are repeated or
adaptively chosen descendants of the same initial states. Ordinary regression
over that flat log would overstate the evidence. Regularization cannot recover
separate effects for predictors that were never varied independently.

This is standard factorial modeling with interaction terms; the NIST handbook
also explains why a saturated factorial fit has no error degrees of freedom:
[NIST: Estimate Main and Interaction Effects](https://itl.nist.gov/div898/handbook/pri/section6/pri615.htm).

`complete_factorial.py` compiled only the missing cells. A reporting assertion
initially assumed nonexact attempts had certificates; they do not always have
one. The first call was already durably logged, so it was recovered from its
receipt without recompiling. Compiler/target identity is checked independently,
and the second missing cell was then compiled. Total new calls remain **two**.

`analyze.py` validates source toggles, receipts, parent edges, recipe bindings,
certificate labels, and exact reconstruction of all eight observed outcomes.
Its output is `analysis.json`; `plot.py` renders those measurements without
invented predictions. The experiment database includes ten inherited probe
receipts and two new ones; only eight unique factorial cells enter the model.
Confirmation duplicates and alternate loop representations do not add rows.

All observations remain header-assisted, exposed development evidence and
training-ineligible. No model weights, policy defaults, main KB or production
translation units were changed. The useful next step is a small prospective
set of matching residual contexts, not a larger regression on duplicate logs.
