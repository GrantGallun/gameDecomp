# Round 11: stuck, called per the rule, with the wall and the evidence

The operator asked for stuck-detection: *"if two consecutive rounds produce neither a verified gain nor a
new falsifiable fact, stop grinding and say so, naming the wall and what evidence would move it."* Round
10 produced a fact (the catalogue entry) but no gain; round 11 produced neither. The reconciliation vein
has now been flat twice in a row at the same 14 nodes, and the cohort runs are no longer settling new
ones. So this is the call.

## The evidence, not an impression

| check | measurement |
|---|---|
| reconcile v15+v16+v17, round 10 | 14 nodes, all `already-counted` |
| reconcile again, round 11 | 14 nodes, all `already-counted` |
| v15 elapsed | 1,879 s, no new `object_exact` node since round 9 |
| v16 elapsed | 1,377 s, same |
| ratchet across rounds 10-11 | 287 byte-exact / 221 SOLVED, unchanged |

The cohort runs are not stuck — they are in their **late-batch, low-yield phase**. The early functions in
a batch settle fast and the remainder are the hard ones, which is exactly the shape that produced the
curve in the first place (0/9 at per-stratum 1, 12/48 at 8): the yield came from *breadth*, and the tail
of any single cohort is thin.

## The wall, named

**The live producer is resource-bound and the next producer needs an implementation, not a wider sweep.**

1. **Reconciliation is mined out.** Two flat rounds. Everything legitimate has been counted: 22 + 4 + 16
   + 12 + 2 + 12 = **68 matches recovered from work that was already finished.**
2. **Cohort breadth needs wall time beyond one round.** The knob is monotone and would keep paying, but
   per-stratum 12 is already ~25 minutes per batch on a contended machine, and pushing to 24 or 32 is a
   scheduling change (single-flight, longer budgets over multiple rounds), not a discovery.
3. **The largest pool is gated by machinery I have not built.** 1,611 functions have never been
   attempted; 166 have an m2c draft and **one compiles**. The block is `typedecl` correctly declining to
   place one member name among 60 pooled offsets. That needs cross-draft member attribution through
   `typepool.member_offsets`, or an `unk`-only admission route — a real implementation, and the only
   item on the priority list still untouched.

## What would move it, concretely

* **Cross-draft member attribution.** For `PlayerCommandState`, `members_used` is `['pdata']` and the
  pool has 60 offsets. If `pdata`'s offset is attested anywhere else in the corpus — the same member
  name on the same type at a known displacement — then the declaration becomes decidable: name that one
  field, fill the rest with `unk_NN`, which is the project's own "unknown is the default" rule. That is
  a bounded implementation in `solver/typedecl.py` + `solver/typepool.py`, and it is the gate on the
  largest pool in the project.
* **Single-flight cohorts at much greater width.** One run at a time, per-stratum 24-32, reconciled per
  settled node rather than per finished ledger. The curve says this pays; it needs rounds, not ideas.

Neither is blocked on the operator. Both are blocked on budget in this session, which is the honest
reason to say so here rather than start one and leave it half-built.

## What was NOT done, and should not be mistaken for done

`v16` was killed to free the machine for `v15`; its settled nodes are already counted and its node file
is written incrementally, so no verified result was lost. `v17` never started.
