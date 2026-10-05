# Frozen multi-edit held-out panel (2026-10-03)

`~/decomp/experiments/edit-capability-20261002/multi_heldout.jsonl`, written by `plant_multi.py` (salt `multi-v1`):
54 cases (39 with k=2 visible planted edits, 15 with k=3) on 24 functions. None of those functions is in
`cases.jsonl` (single-edit dev), `heldout.jsonl` (single-edit held-out), `multi_train.jsonl` or `multi_dev.jsonl`:
the split is by function hash.

sha256 `7ba5fab1dd0e230aa5ef861fff8263115751f6ed159ea81d65a5761c0b3653bb`

Frozen before any arm was scored on it, and before search priors were fitted. Cases are not inspected
individually: runners print the class only, and `eval.coverage --heldout` prints counts only.

## Preregistered comparison

Search: `site_edits.search(operators=True, gaps=True, depth=3)`, budget = 3 x per-step width. No model.

| arm | order | widths |
|---|---|---|
| base | propose()'s hand-set order | 24, 8 |
| learned | `search_priors.orderer(priors_v1)`, fitted on `multi_train` only | 24, 8 |
| drop | learned + dead families removed | 8 |

**Amendment (2026-10-03, before any held-out arm was scored):** two arms added after dev work, from the user's
"pruned branch, regenerate the full set on a miss" idea: `site_edits.search(escalate=True)`, per-step width 8 as a
TIER (a tier that improves nothing pulls the next tier of the same level), budget 72.

| arm | order | widths |
|---|---|---|
| tiered | learned + escalate | tier 8, budget 72 |
| base_tiered | propose()'s order + escalate | tier 8, budget 72 |

Dev (37 cases), for the record before held-out: base width 24 covers 23; learned 24 covers 24 (+2 -1, refused);
learned 8 covers 20; tiered covers 25 (+2, -0, accepted; 868 -> 329 compiles on the 23 both cover); base_tiered 24
(+2 -1, refused). Primary held-out comparison is now `tiered` against `base` width 24.

Primary (original): `learned` against `base` at the same width, paired (`python3 -m eval.coverage ... --against`).
Adopt only on gained >= 1 and lost = 0, or equal coverage at lower cost on the cases both cover; any lost case
refuses. Secondary: whether `learned` at width 8 covers at least what `base` covers at width 24 (pruning: the same
coverage at a third of the compiles). The priors table and code version are fixed by dev work before this panel
is run, and it is run once per frozen code version.

## Result (scored once, 2026-10-03; counts only)

| arm | coverage | compiles (all cases) | at <=24 / <=36 / <=72 |
|---|---|---|---|
| base, width 24 | 24/54 | 2,320 | 2 / 16 / 24 |
| tiered (learned + escalate, tier 8) | 30/54 | 1,751 | 26 / 27 / 30 |

Primary comparison, tiered against base: gained 9, lost 3, compiles on the cases both cover 734 -> 278.
**Verdict under the preregistered rule: refuse** (any lost case refuses), despite +6 net.

Post-hoc, NOT preregistered: the cascade "tiered, then base width 24 only where tiered failed" is computable exactly
from these two deterministic runs: 33/54, lost 0 by construction, 2,836 compiles (+22%). On dev the same cascade is
25/37 at 1,422 -> 1,267 compiles. Because it was chosen after seeing held-out, it needs a fresh panel to confirm.
All arms (counts only; paired against base width 24):

| arm | coverage | compiles | gained / lost | verdict |
|---|---|---|---|---|
| base, width 24 | 24 | 2,320 | - | - |
| base, width 8 | 9 | 726 | | |
| learned, width 24 | 31 | 2,015 | +8 / -1 | refuse |
| learned, width 8 | 24 | 779 | +5 / -5 | refuse |
| drop, width 8 | 24 | 776 | +5 / -5 | refuse |
| tiered (learned + escalate) | 30 | 1,751 | +9 / -3 | refuse |
| base_tiered (escalate only) | 32 | 2,083 | +9 / -1 | refuse |

learned 8 vs base 8: 9 -> 24 (+16 / -1). Every arm covers at least as much as base width 24, and five of six cover
more, but each loses at least one case, so none is adopted under the preregistered rule. Escalation alone
(base_tiered, 32) did as well as escalation + learned order (30) here, unlike dev (24 vs 25): the learned order's
contribution is not established; escalation's is (+8 net on held-out, +1 on dev).

## Recertification (2026-10-03, after an external audit)

Exactness above was a masked-listing comparison (`mine.mask` hides relocation symbols). Every arm was re-run with
`trails.py score ... --suffix _recert`, which certifies each masked match with `solver.byte_certificate.certify`
against the workspace's ROM-extracted `target.o`, and also certifies each case's ORIGINAL source as a control.
Result, dev and held-out, all 14 arms: every masked match certifies, every original certifies (37/37, 54/54), and
every replay is identical to the frozen run (same exact flag and candidate count on every case). The coverage
numbers above stand as certified. Rows: `cov_{dev,heldout}_*_recert.jsonl`.
