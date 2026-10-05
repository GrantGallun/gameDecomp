# audioThreadMain: private native reproduction

One search started from the retained native campaign attempt 153528 at checkpoint
28415, while the live controller was advancing. The source hash was
`d69767817a6784e2c7f5c6d8405425a1611e45e4f9bde269f2272e1ffd38c759`;
the node was pending at 97.778. The earlier experiment's candidate source and
reference C were never loaded. Its metadata was used only after the new search
to compare paths.

The baseline reproduced 97.778 and passed the frontend gate. The first assembly
divergence was at `@@ -70,7 +70,7 @@`: the target used `li s0,1`, while the
retained candidate used `move s0,s4`. The ordinary `regalloc_mutations` stream
generated `local_type:s32->u32@329`, changing only the `var_s0` declaration
from `s32` to `u32`. Its private attempt 14 scored 100, passed the frontend
gate and received `object_sections_exact` from the object-section certificate.
The generated source hash is
`2ef07de54f60ffa698da5bbbd2d12fadc0a5e0c521b4374e7752712d3cca1fb2`.
This is object-exact C acceptance, not a whole-ROM integration result.

The exact appeared on proposal compile 13. The bounded greedy step completed
its eight siblings, for 16 proposal compiles plus one baseline compile. All 17
attempts and 16 explicit parent edges are in the private DB; 14 is the sole
exact receipt. `do_restore` fired once and compiled at 97.778, so it was not
selected. The prior experiment metadata reported a different accepted
`local_type:s32->s16@1181` path after 13 proposal compiles. The first proposal
order difference here was an `operand_local` variant at compile 2; four such
variants entered this run. Both routes independently reached the ordinary
certificate/frontend gates from the same retained native source.

The scorer, mutation stream, byte certificate and frontend checker in main
were SHA-256 identical to their frozen campaign copies when audited. The
private isolate and DB are under
`/home/grant/decomp/experiments/frontier-run-20260926/audio/`. The full
certificate/frontend receipt is in [receipt.json](receipt.json); [verify.py](verify.py)
checked logging, lineage, gates and code hashes. Neither live campaign state
nor its database, frozen code, production KB, or game source was changed.

## Delivery and eligibility gap

A read-only frozen-scheduler projection at native checkpoint 28454 still found
the same pending source and score. Its environment-lane evidence key is
`33571d49322533b5ab73141e99987a822c7786dc09b89e119d5ca8876db30676`.
The next profile is `binary_types@0e101db35c25fe10`, because this node has not
yet taken the versioned binary-draft retry. That retry scores independent
binary-derived drafts plus retained candidates, with deterministic budget zero;
it does not generate a `local_type` child from the incumbent. It can preserve
the incumbent and is not a reproduction of this repair.

The same source/evidence key already has evaluated `local_rewrites` and
`deeper_composition` jobs. Re-eligibility of `local_rewrites` by itself is also
insufficient: that worker calls `solver.repair.search`, whose generator is
`solver.rewrites`, not `regalloc_mutations.local_types`. The existing
`regalloc_search` profile does use `regalloc_mutations`, but both the scheduler
and `agentrepair._regalloc_search` require register-dominant faults. This node's
stored residual has one structural fault and zero register-allocation faults,
so it cannot reach that profile.

The smallest normal-controller route is a versioned deterministic operand
repair profile, eligible once for the current source and generator revision,
that runs the ordinary `regalloc_mutations.variants` stream with the incumbent's
source attribution and verdict, logs every `workspace.score` attempt, and
accepts only the frontend plus object-certificate gate. This private run gives
its motivating positive test: `local_type` fired and the gate accepted it.
The controller should regenerate the candidate from the retained source; the
private candidate C must not be imported. Integration remains a separate gate.

## Production route canary

`eval.operand_repair.run` now implements that bounded route. A fresh private
canary cloned the retained attempt and its 17 ancestors, then called the new
production API in an isolated native repo. It logged one baseline and 13
proposal compiles, with 14 explicit new lineage edges. The route stopped
immediately on the same `local_type:s32->u32@329` edit: frontend passed,
`object_sections_exact`, generated source hash `2ef07de5...`. Its private
attempt is 153542. See [production-canary.json](production-canary.json).

Targeted WSL verification: 60 tests passed across the new campaign tests,
regalloc mutation tests and byte-certificate tests. The live campaign remains
untouched by this canary; scheduler and worker wiring are owned separately.

## Two-node frozen-controller canary

After the new scheduler and worker dispatch were wired in a copied staged
project, a second private campaign cloned the paused native checkpoint 28456.
It retained both nodes' actual job histories and required attempt ancestry,
then ran `fast_campaign` with one worker, deterministic-only, and an eight-job
ceiling. The normal controller selected `operand_repair@59dc599b1f103928` for
both nodes and finished after two jobs with no deterministic work remaining.

| Node | Native retained score | Private accepted result | Proposal compiles |
| --- | ---: | ---: | ---: |
| `audioThreadMain` | 97.778 | 100, object exact, attempt 153547 | 13 |
| `releaseSoundEffectHandleNode` | 99.615 | 100, object exact, attempt 153533 | 3 |

The release candidate came from `local_web_merge:temp_v1+temp_v1_2`, which
appeared at position 3 in the full mutation stream on its retained native
source. Both accepted candidates passed the frontend and received
`object_sections_exact`. Worker sync/merge imported 18 new attempts and 18
explicit parent edges; the private DB passed its foreign-key audit. Each
baseline's parent was its live retained attempt, and each exact receipt appears
in the canonical worker attempt map. Existing job-history prefixes were
preserved. Zero model attempts or calls occurred, and no worker remained
inflight. [controller-canary.json](controller-canary.json) carries the detailed
receipts; [controller_audit.py](controller_audit.py) checks canonical lineage
and gates. Integration was intentionally skipped by deterministic-only mode,
and the live campaign was not mutated.

## Refreshed stage and nonidentity import

The refreshed copied project (manifest SHA-256
`9c7d9b96e75f4548ceb2aedf849a884ca976524a3998f376d415a727c7d89056`)
passed 190 staged tests with one skip. A second private normal-controller
canary retained v1 artifacts and ran two workers in one wave, forcing the
second worker's attempt IDs to be remapped during import. Both nodes again
became object exact at 100 with passing frontend and
`object_sections_exact` certificates under profile
`operand_repair@f4aa2692fc97a7ad`. The canary imported 18 attempts and 18
edges, preserved prior job histories, had zero model attempts and no inflight
work, and did not touch the live campaign.

The strict [v2 receipt](controller-canary-v2.json) audit found a genuine
nonidentity mapping for all 14 audio attempts. Every canonical log
`receipt_id` (14 audio, 4 release) resolves to its imported attempt and
matching parent edge. The baseline parents are native attempts 153528 and
145970; the accepted exact attempts are 153547 and 153533, respectively.
The audit command is:

```bash
python controller_audit.py --report controller-canary-v2.json \
  --require-nonidentity --require-log-receipt-ids
```
