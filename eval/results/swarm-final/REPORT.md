# Small-agent-team byte matching results

Three independent agents investigated RNG, timer and callback while the primary agent reviewed and integrated source operators, added exact-input semantic replay caching, and tested the normal search pipeline. No reference C, compiler flag changes or production-source integration were used.

| Function | Starting score | Verified best | Semantic validation |
|---|---:|---:|---:|
| __MusIntRandom | 98.590 | 100.000 exact | 256/256 |
| calculateRaceTimerDelta | 94.324 | 100.000 exact | 175/175 |
| createCallbackTaskPreservingArgs | 93.702 | 94.723 | 76/76 |

The two new exact matches have allocated-object-section/relocation certificates and pass the strict project frontend check. These certificates do not cover final whole-ROM linking. The previously exact release candidate remains unchanged.

## What was blocking progress

The existing candidate library mostly respelled the current implementations. It lacked three useful source idioms:

- RNG: reuse a single floating-point quotient through both divisions and recover a normal counted loop. The earlier nested loop scaffold affected instruction scheduling. The divisions retain their original order and precision. IDO can also unroll a simple eight-iteration loop itself.
- Timer: reuse a dead input local for the absolute difference; then store each remainder directly and divide that same local in place. Separate temporary lifetimes were selecting the wrong registers and schedule.
- Callback: pair unsigned parameter narrowing with an explicit cast in place of the existing mask assignment. Either partial change could regress; together they restore the target's argument stack-home store. Its remaining residual includes a separate four-instruction sentinel-head reload and related register allocation. Tested loop and declaration alternatives did not resolve that gap.

The team helped by independently proposing different source structures and checking them against the compiler. Agreement between agents was not used as evidence of correctness.

## Integration verification

The normal deterministic pilot reproduces the RNG exact match after one candidate and the timer exact match after 41 candidates from their old seeds. Callback reaches 94.723 in 28 candidates. These integrated runs use zero model tokens. The timer integrated search replays its original six cases; its exact artifact separately passes175 cases (the original 6 plus 169 signed/boundary pairs). RNG and callback use their complete 256/76 panels in the integrated runs.

A two-entry semantic replay cache reuses only identical interpreter inputs, including target/candidate assembly, every case, call arities and return registers. Defensive copies prevent result mutation from contaminating later trials. It does not reuse byte certificates or bypass compilation. Callback reused nine 76-case suites; other changed outputs were replayed normally.

Source operators are in `solver/rng_alternatives.py`, `solver/timer_alternatives.py`, and generic `masked_parameter_casts` in `solver/callback_alternatives.py`. They are wired into `deterministic_exactness_candidates`, with scoped matching, conservative rejection guards, bounded candidate budgets, and normal semantic/certificate gates. Policy labels identify these families separately for future compiler-response ranking.

## Evidence and candidates

- [RNG exact candidate](__MusIntRandom.c), [full 256-case verification](../swarm-rng-validation/receipt.json), [agent report](../swarm-rng-validation/REPORT.md), [integrated search](../swarm-integrated-rng/summary.json).
- [Timer exact candidate](calculateRaceTimerDelta.c), [full 175-case verification](../swarm-timer-final/replay.json), [agent report](../swarm-timer-final/REPORT.md), [integrated search](../swarm-integrated-timer/summary.json).
- [Callback candidate](createCallbackTaskPreservingArgs.c), [full 76-case verification](../swarm-callback-v2/replay.json), [agent report](../swarm-callback-v2/REPORT.md), [integrated search](../swarm-integrated-callback/summary.json).

Final validation: **159 regression tests passed**, covering all integrated operators, scope/alias/termination guards, semantic replay cache isolation, policy families, and the existing pilot, attribution, semantic-regression and OSS fallback paths.
