# Why the sweep and the two-case regression look different

The saved sweep is correctly invoking `solver.scalar_coalesce.variants`. A
compile-free replay on all 62 retained baseline sources reproduced every saved
child source exactly. The two saved winning C files also have the same SHA-256
hashes as the earlier strict-native motivating controls. This is evidence against
a generator wiring error in the sweep. It does not validate a subsequent frozen
campaign amendment, which is a different operation.

The earlier 2/2 experiment deliberately replayed the two cases from which the
family was motivated. Both already needed precisely the proposed merge. It was a
generator/wiring regression, not a randomly sampled success rate. Claude's wider
sweep reproduces both results and adds information about limited transfer.

## Cohort and search differences

The pasted sweep output reports 831 screened pending functions and 62 with at
least one candidate (7.46%). The JSONL retains only those 62, so the other 769
and their individual decline reasons cannot be reconstructed from that log alone.
The implemented family is deliberately restrictive: leading plain scalar locals,
no later declarations, loop/jump/label rejection, and visible binding/escape
guards. For example, a leading pointer declaration can prevent later scalar
locals from being considered. This narrow scope is part of our implementation,
not an omission in Claude's call.

Of the 62 recorded roots, 58 have non-register faults in their saved campaign
classification. Only four are classified register-only: the two motivating
winners plus `updateRacePlayerMode04Spinout` and `updateRacePlayerMode23ItemSteal`.
The 14 non-exact score improvements all come from the mixed-fault group, and the
highest resulting score among those 14 is 95.783. These labels and scores are
heuristics; they do not prove a mixed-residual function is unfixable by this family.

The sweep evaluates one coalescing edit from each root. It does not call
`regalloc_search.search`, expand children, compose families, rank offspring, or
preserve a same-object/worse-scoring child for another mutation. It therefore
does not measure the hill-climbing/stepping-stone hypothesis. Conversely, the
earlier two-case successes occurred during first-root expansion; exploration did
not account for that 2/2 result either.

## Retained-artifact audit

`audit_retained.py` reads saved sources, objects, frontend reports and full
normalized listings. No compilers or campaign writes were used. Receipts:
`retained-audit.json`, including implementation/metric/input hashes.

| Observation | Count |
|---|---:|
| Functions with every saved child reproduced exactly | 62/62 |
| Evaluated candidate objects | 286 |
| Same allocated object sections/relocations as parent | 53 |
| Better full-listing gradient than parent | 41 |
| Tied full-listing gradient | 91 |
| Worse full-listing gradient | 154 |
| Functions with any full-listing gradient improvement | 17, including both matches |
| Strict target certificate plus frontend exacts | 2 |
| Missing or failing saved frontend reports | 0 |

The 53 same-object children may have different source neighborhoods. This audit
does not establish that any such child enables a match. The sweep simply did not
expand them. Object identity and gradient ties are different measurements.

There is also a real coverage shortfall: the sweep slices to eight candidates,
while the generator's default cap is twelve. Replay found **81 additional
proposals across 22 functions** within that default cap which were never compiled.
Their outcomes are unknown. Thus the result is a capped one-step result, not a
complete measurement of the production family or the full search.

The reported 286 compiles counts children only. The script also scores 62 roots:
**348 scored compile calls including baselines**. All 286 retained candidate
objects were available. The two exact verdicts survive independent certificate
and frontend checks. `workspace.repair_complete` permits a missing frontend in
general, but that permissiveness did not affect these retained candidates.

## Interpretation and next measurement

Coalescing is highly effective on two selected near-misses and has limited
observed one-step transfer in this wider sample. Neither result contradicts the
other. Claims that campaign integration is "nearly free" or will finish the
14 improving cases are not established: candidates compete for a fixed budget,
and coverage can change after other families edit the source.

Before inferring live benefit, run the existing equal-budget production and
production-coalescing arms on the same frozen fresh roots. Include the
evolvability pair when measuring stepping stones. Count exact wins and losses,
full-listing progress, all baseline/probe/recheck costs, and source-distinct
descendant opportunities for no-op candidates. Completing the omitted 81
one-step proposals is a separate cheap coverage check, not a substitute for
that paired search. Neither additional experiment was run in this review.
