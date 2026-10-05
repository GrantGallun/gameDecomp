# Assignment-scoped field-cache elimination and repair diagnostics

2026-09-28. The reusable generator and diagnostic packet are wired into the
existing main-tree search and model-repair paths. Native transfer testing is
recorded below. The running frozen campaign, KB, match ledgers and real build-path
C were not changed.

## What changed

`solver/scoped_field.py` proposes replacing reads owned by one top-level
`local = pointer->field` assignment with explicitly converted field accesses.
It retains the declaration and other definitions/uses of the local. A later
unconditional definition can end the region; conditional definitions cannot.
Visible calls, indirect calls, escapes, possibly aliasing stores, volatile
declarations, macros and unsupported control flow cause a decline. The grammar
is deliberately small. Included headers are not expanded, so the guards are not
a semantic equivalence proof. Frontend and object certification decide acceptance.

The family is enabled in `regalloc_mutations.variants` and the shared
`regalloc_search` machinery; `scoped_fields=False` disables it for ablations,
including previews, key restarts and research custom-generator arms. No new
register-search implementation was introduced.

`solver/repair_diagnostic.py` supplies the existing compiled byte-repair prompt
with mismatching instructions, source regions, leading scalar local declarations,
individual assignments/uses across earlier and later roles, visible barrier
locations and prior measured sibling edits. Direct compiler attribution requires
both source and diff hashes. Syntax associations remain hypotheses. Truncation
and unsupported scope are explicit, and directly implicated later uses take
priority over a long earlier use list. Model proposals retain the existing bounded
edit schema and must describe a predicted assembly effect. The existing
model-repair loop supplies measured feedback to later calls; compile-fix,
type-planning and semantic-repair prompts retain their specialized paths.
`focused_diagnostics=False` provides the prompt ablation.

## Motivating regression, separate from transfer

The starting source is the retained searched parent of
`updateRacePlayerMode48AerialTrick`, source SHA-256
`d3b676805330b2b207fb4ba26d757518b318d6b937f4cdd8be271c877266e1cd`.
The source-only generator offered two edits. Baseline plus both proposals used
three native compiles; eliminating the later `temp_v0_2` timer cache with its
explicit `s32` conversion produced `[0,0,0]`, a byte certificate and frontend pass.

A fresh test through the actual keyed shared search then established reachability:

| Same search and 128-unit allowance | Native compiles | Key calls | Effective work | Exact |
| --- | ---: | ---: | ---: | --- |
| Family disabled | 111 | 124 | 128.36 | No |
| Family enabled | 12 | 21 | 14.94 | Yes |

This is the already known motivating function, not an additional discovery.
The final label was `scoped_field:temp_v0_2@3110:s32:preserve-conversion`.
Receipts: `control-receipts.json`, `search-control.json`; audited source/object
artifacts under `matches/updateRacePlayerMode48AerialTrick/control/`.

## Preregistered transfer comparison

Selection was frozen before new outcomes. Exclude the previous 17 continuation
functions and two known coalescing exacts. Among the remaining retained original
roots, require the source-only family to fire; choose six with the smallest
previous full-listing gradients, breaking ties by function name. Seven were
eligible. These are exposed development functions with project headers and
unknown earlier lineage, not fresh held-out or training-eligible data.
Mode37 and Mode51 are close siblings; their successes are not independent evidence
of broad generalization.

At the frozen starting roots the family fired on 13/62 retained sources, or 7/43
after the exclusions above. This measures initial proposal coverage, not how often
later search descendants become eligible or how many eligible roots are solvable.

All arms use the existing `evolvability_coalesce` engine with enabling roots,
optimizer keys, 2% audits, beam 4, depth 12, preview 64, two charged probes and seed
0. This is the main-tree search configuration, not a frozen campaign replay.
Allowance is 128 compiles-equivalent units per function, counting every real
compile and `0.14` per key call. The existing engine can cross an action boundary
slightly; actual costs and overshoot are retained. Early success/exhaustion can
use less than the allowance.

The third arm adds two local `gpt-oss:20b` proposals per function, using the new
packet, the existing parser/applicator, actual compiler feedback and the same
remaining search allowance. Generation has a 180-second/4096-output-token bound
per call. Failed/invalid/no-op/regressing proposals are retained in logs. Only
strictly better full-listing gradients or exact candidates replace the root for
the subsequent register search; source-distinct ties/regressions are not expanded
in this small model-allocation experiment. Model time/tokens are additional costs.

The model arm tests model proposals plus the packet together; it does not isolate
the packet's causal benefit against an old-prompt model arm. No post-training was
performed. Prompts, responses, model digest, source hashes, compilation receipts
and raw full-listing outcomes are retained.

Compiler-only outcomes: existing search matched **0/6**; adding the family matched
**2/6**. Both winners (`updateRacePlayerMode37AerialTrick` and
`updateRacePlayerMode51AerialTrick`) matched in **9 compiles and 19 key calls** each,
versus 112 compiles without a match in each disabled-family baseline. Both edits
remove a cached `player->unk306` value from one bounded region while preserving
the explicit conversion. Their starting residuals were `[1,2,2]`, so a blanket
"reject every non-register residual" rule would exclude these successful cases.

The other four compiler-only cases remained unmatched. Their best gradients were
the same with and without the family. The result supports adding a missing source
operation; it does not show that this operation covers every remaining failure.

Model arm (family + two packet-guided `gpt-oss:20b` proposals per function, completed; summarized
2026-09-28 from `/home/grant/decomp/experiments/scoped-field-transfer-20260928/summary.json`):

| Function | existing | + family | + family + model |
| --- | --- | --- | --- |
| Mode23ItemSteal | `[0,5,6]` | `[0,5,6]` | `[0,5,6]` |
| Mode04Spinout | `[0,8,8]` | `[0,8,8]` | `[0,8,8]` |
| Mode37AerialTrick | `[1,2,2]` | **exact**, 9 compiles | **exact**, 2 compiles (model proposal 2) |
| Mode51AerialTrick | `[1,2,2]` | **exact**, 9 compiles | **exact**, 10 compiles (search, after 2 failed proposals) |
| Mode25SpinHit | `[5,26,35]` | `[5,26,35]` | `[5,25,33]` |
| Mode05SpinoutStun | `[15,39,52]` | `[15,39,52]` | `[15,39,52]` |

The model arm matched the same **2/6** as the family alone. Of 12 proposals, 8 compiled: one was
Mode37's exact edit (the same `unk306` elimination the family makes, reached in 2 compiles instead of 9),
the others were no better or worse than their parent (e.g. Mode23 `[2,6,7]` from `[0,5,6]`). The other 4
failed before compiling with `old substring occurs 0 times`: the bounded edit schema requires quoting
source text exactly, and the model misquoted it. Calls were short (200-850 generated tokens, 3-119 s).
The packet did not add a match here; the one model success duplicates the family. The quote-anchored edit
format lost a third of the proposals, which is a mechanical failure worth fixing (line-slot anchoring,
`solver/edit_slots.py`) before judging the packet's value.

## Verification and artifacts

**265 focused tests passed in WSL**, including the compiler-backed attribution
test. They cover the motivating source, conversion preservation, older/later
roles, guard declines, default search reachability, disabled-family propagation,
stale attribution, bounded packets and measured sibling feedback reaching the
next model prompt. Review found and corrected indirect-call and typedef-member
call-guard loopholes and an ablation flag omitted by custom research generators.
Windows initially denied its installed clang executable; the complete focused
suite passed using the WSL toolchain.

`preregistration.json`, `compare.py`, `native_control.py`, `search_control.py` and
`summarize.py` preserve the plan and replay/audit procedure. Native input bundles
and all attempts remain under:

```
/home/grant/decomp/experiments/scoped-field-control-20260928
/home/grant/decomp/experiments/scoped-field-search-control-20260928
/home/grant/decomp/experiments/scoped-field-transfer-20260928
```

Exact artifacts are isolated object-section certificates plus frontend passes,
not final-ROM verification or a global-ledger novelty claim. No reference C was
used for generation, selection or grading; full target object/listing comparisons
are the oracle. These development roots remain excluded from clean training data
until their lineage is independently admitted.
