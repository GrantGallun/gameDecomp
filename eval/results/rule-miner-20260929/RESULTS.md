# Rule miner — results

Pre-registration with results-so-far and the T5 amendment: `PREREGISTRATION.md`. Code: `solver/rewrite_library.py`
(Engine A vocabulary), `solver/rule_miner.py` (features, table, matcher, template application), `eval/rule_mine.py`
(mining), `patterns/equivalences.directed_hunks` (Engine B abstraction), `patterns/mined_rules.json` (the table),
the `mined` lane in `solver/site_edits.py`. Tests: `test_rewrite_library.py`, `test_rule_miner.py`, and the site-edit
suites.

## Verdict

**T5, sealed held-out 50: wins/losses/ties 17/3/30, one-sided sign test p = 0.0013. Positive and significant, so
under the pre-registered rule the mechanism is mature and work moves on.** The mined lane fired on 47 of 50
functions (the earlier package fired on 1 of 50). 16 of 17 winning best states came from a mined edit.

## All tests

| test | prediction | result |
|---|---|---|
| T1 rediscovery | loop and copy-back rules rank top 3 on osMotorStart's stages | PASS 4/4 (ranks 3 and 1) |
| T2 pruning | increment spellings, comparison flips inert (< 5%) | 14 of 21 rule directions pruned; increments inert except pp->pe (11%, n=18); comparison flips not implemented |
| T3 Engine B yield | tens to low hundreds | 360 kept (≥3 improving edges, ≥2 functions); 318 meet the pre-registered ≥5 changed / ≥3 functions criterion (a lower bound; the code's filter differed from the text, noted as a deviation); 253 applicable to new code |
| T4 development pool 50 | fires ≥ 60%, wins > losses | fired 92%; 13/2/35, p = 0.0037; 11 of 13 wins via mined edits |
| T5 sealed 50 | fires ≥ 80%, significant | fired 94%; 17/3/30, p = 0.0013; 16 of 17 wins via mined edits |

Engine A: 1,053 exact functions probed (915 reproduced), 1,859 rewrite applications. Effective: if/else arm swap
(98%), rotated↔for (13/13), adjacent statement swap (77%), copy-back temporary (33–40%), compound expansion (7%).
Inert: ternary↔if (0/45), subscript↔pointer (0/259), truth tests (≤ 2%), increment spellings (≤ 2% on n=154).
Engine B: 190,851 logged edges, 174,130 local, 10,841 distinct templates.

## What it does not show

- **0 exact** in every arm of T4 and T5. The wins are lower instruction/register distance, not finished functions.
- **Mean best score −0.09** against control on T5. The search optimises the gradient (instruction distance, then
  registers), not the similarity score, and the two disagree on some functions.
- **Cost**: 1,808 compiles against the control's 1,232 (+47%). The treatment also carries the budget-72 change, so
  budget and lane are not separated on T5. On T4 (same budget in both arms) the lane alone won 13/2.
- Engine B templates are learned from edits that improved the score, not from equivalences, so a template can change
  meaning (`x[i] << 24` -> `x[i]`). As search steps that is acceptable, because only a byte-exact result counts.
- Two precedence bugs in my rewrites and one analysis-filter deviation were found and fixed or recorded during the work.
- Main tree only. The campaign runs frozen code until an amendment. `repair_queue.site_edit_digest` now covers the
  new modules and the rule table, so an amendment schedules fresh site-edit visits.

## Upgrade: B→A promotion and localisation (pre-registered in PREREGISTRATION.md, "Upgrade")

| test | prediction | result |
|---|---|---|
| U1 promotion | ≥ 50 validated, ≥ 20% of probed pruned | 70 validated ✓; 2 of 104 probed pruned (2%) ✗. Only 104 of 282 promotable templates were probed (cap of 10 per function, ordered by past improvements); 56% of reversed probes didn't compile |
| U2 development frame vs T4 mined arm | wins > losses | 6 / 6 / 38, p = 0.61 ✗ |
| U3 sealed 50 vs T5 | wins > losses | 4 / 6 / 40, p = 0.83 ✗ |
| U3 vs this morning's control | — | 17 / 4 / 29, p = 0.0036 (T5 was 17/3/30) |

Both upgrades are neutral. They stay in the code: behaviour against the control is unchanged within noise, and on
the sealed frame they cost fewer compiles (1,665 vs 1,808). But they bought nothing, and the mechanism is rated 4/5 on the
mechanism map, not higher. Reading (a hypothesis, not tested): with the lane already firing on 94% of functions, ranking
is not the constraint; vocabulary and search depth are. Next is automatic enumeration of new rewrites.

## Frame correction (2026-09-29, found after the tests)

The "unsolved" pool counted only functions with no exact attempt row. The campaign state also marks functions
`integrated` or `function_exact_pending_integration` without such a row. The sealed 50 contain 2 of those (1 integrated,
1 pending) and the development 50 contain 4 (2 and 2). They start at a perfect state in both arms, so they can only tie;
no reported direction or significance changes. Future frames exclude them by reading node status
(`eval.campaign_state.read`).
