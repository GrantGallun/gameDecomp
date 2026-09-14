# Typed pool-count and assignment-result experiments

**40 candidates compiled; none improved 98.511.** No replacement source or new
semantic-success claim was submitted, and no unproductive operator was added
to the production search.

The first thirty candidates tested:

- Unsigned 16/32-bit or signed 32-bit local count values carried through the
  zero check, decrement, global write and pool lookup. Best score: 98.369.
- Signed 16-bit backing declarations with explicit unsigned reads and
  decrements, applied to the pool count, dispatch counts, or both. All six
  variants remained at 98.511.
- A shared decrement result or duplicate decrement expressions. These did not
  recover the target index/new-task register assignment.

The final ten candidates (`../callback-allocation-pool-v2`) tested assignment
results embedded in the pool lookup, acquiring the new-task pointer through
another local, and reusing the dead index local for the active-field value.
Pointer acquisition through `cur` and simple dead-index reuse remained at
98.511; other forms regressed. The candidates are experiments, not production
source, and are retained with compiler receipts in `scores.json`.

These results narrow the failed mechanisms to concrete typed-value flows.
They do not establish that other source structures cannot affect coloring.
Compiler settings, helper guards and production translation units were not
changed. The experiment generator is
`eval/experiments/code-shape-search/callback_allocation_pool.py`.
