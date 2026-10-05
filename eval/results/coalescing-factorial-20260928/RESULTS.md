# Coalescing is wired and confirmed on its two motivating cases

The final generator reaches both previously reported matches through the existing
register-search engine. The native adapter verified exact allocated object
sections/relocations **and** a passing, source-bound frontend check. These are
reproduced, header-assisted development matches, not new discoveries or a fresh
population estimate.

Implemented `solver/scalar_coalesce.py`, the opt-in `coalesce=True` switch through
`regalloc_mutations.variants` and `regalloc_search.search`, and the
`production_coalesce` / `evolvability_coalesce` suite arms. The switch persists
through previews and key-violation restarts. Added the confirmed pattern to
`patterns/catalog.py` and updated `PIPELINE_MAP.md` and the suite README.
Campaign defaults, residual routing and ledgers were not amended.

## Native comparison

Frozen retained m2c roots: `stepRaceMotionLoopingAnimation` (attempt 255793) and
`stepRaceMotionLoopingJointAnimation` (260195), from the prior trial's `bundle-1`.
No reference function body or previously winning C was supplied. Both targets
belong to the same `race_motion` cluster and are not independent transfer cases.

The table totals real compile calls over both functions. Each arm had nominal
budget 128 **per function**, seed 0, beam 3, depth 4, enabling roots and optimizer
keys on, diversity off, key cost 0.14, and 2% audits.

| Arm | Certified functions | Real compiles, both functions | Key calls, both functions | Effective cost per function |
|---|---:|---:|---:|---:|
| production | 0/2 | 108 | 468 | 86.76 |
| evolvability | 0/2 | 214 | 316 | 129.12 |
| production_coalesce | 2/2 | 12 | 22 | 7.54 |
| evolvability_coalesce | 2/2 | 12 | 22 | 7.54 |

Both winners were `scalar_coalesce:temp_h0->temp_v0:s32->s16`, after six real
compiles per function in either coalescing arm. The unmodified-vocabulary arms
kept best actual-compile gradient `[0, 3, 3]`; the coalescing arms reached
`[0, 0, 0]` and certified exact. There were no key violations or preview/generation
caps. Production finished below budget; evolvability crossed the nominal limit
by 1.12 effective units under the existing pre-action budget check. This is a
shared nominal budget and cost model, not identical spent work or wall time.

Separately, direct generator controls compiled each root and its one proposal:
four calls total, including both baselines. Both roots compiled and failed the
exact check; both proposed children passed the byte certificate and frontend.
The final comparison used 346 calls and 828 key requests; adding the direct
controls gives **350 real calls**. A preliminary run of the same size is retained
at the path without `-final`; it preceded the final typedef guard hardening and
is not pooled as additional evidence. Total native work this implementation turn:
700 real compile calls across the two runs.

This supports adding the missing family on its motivating cases. The match was
found during first-root expansion, before offspring ranking could distinguish
the coalescing arms. It therefore does not establish whether exploration helps
after vocabulary expansion, or whether the old graph lacks a longer successful
path. A fresh frozen cohort and the preregistered four-arm comparison are still
needed for those questions.

## Verification and limits

**226 targeted tests passed**, including real generator firing on the retained
m2c shape, token/scope preservation, address escapes, shadow declarations,
comma-separated typedef aliases, source macros, type changes, loop/jump declines,
deterministic limits, all four arms, restart accounting, and existing research,
register-search, attempt-logging and pattern tests. Receipt: `tests.xml`.
The focused reviewer found two classes of scope/macro guard holes; reproducing
tests failed first, fixes passed, and the final review reported no remaining
findings. Targeted whitespace checks also passed.

The generator is intentionally conservative: leading plain scalar declarations,
textually ordered uses and visible initial assignments; it declines unsupported
visible bindings and backedges. It does not preprocess included headers or prove
control-flow liveness. Cross-type integer merges remain explicitly labeled
hypotheses, with exact certification as the acceptance authority.

Full native sources, objects, frontend reports, certificates, attempt logs,
key/reuse logs, decisions and paired summaries:

`/home/grant/decomp/experiments/coalescing-factorial-20260928-final/`

Compact copied receipts: `native-receipts.json`. Final bundle SHA-256:
`44331eee25c34b7555c24cc5e0aa1669e06ec783c523a6deb379a125d6beb43a`.
Environment SHA-256:
`ab0084e81af211a504bc75efc26f78cfa46e8cad8f703260e1373add0e0e4e9f`.

Replay in WSL with a new output directory:

```bash
/home/grant/decomp/sbk1/.venv/bin/python \
  /mnt/c/Code/gameDecomp/eval/results/coalescing-factorial-20260928/run.py \
  --repo /home/grant/decomp/sbk1 \
  --input-bundle /home/grant/decomp/experiments/evolvability-trial-20260928/bundle-1 \
  --output /home/grant/decomp/experiments/coalescing-my-replay --budget 128
```

For fresh tasks use the standard `eval.research_suite freeze` and `run` commands
with all four arms, then summarize with baselines `production`, `evolvability`
and `production_coalesce`. See `PREREGISTRATION.md` and the suite README.
