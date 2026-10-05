# Research suite verification — 2026-09-28

**Superseding baseline review:** the native receipts below predate the correction of the weakened
`production` arm. They establish historical wiring, not campaign-baseline fidelity. The corrected
enabled/keyed production arms and their local tests are documented in
[the review response](../../../docs/research-suite-review-20260928.md). Native verification of that
correction is deferred until the competing pilot finishes; these historical counts are unchanged.

The research test wiring passed mechanism tests, relevant regression tests, and native IDO integration controls. These results do not measure improvement on unresolved campaign functions.

| Verification | Result | Receipt |
|---|---|---|
| Windows, six research test files plus related solver regressions | 256 passed, 2 skipped | [windows-tests.xml](windows-tests.xml) |
| Same selection in WSL | 258 passed | [wsl-tests.xml](wsl-tests.xml) |
| Native synthetic smoke after review fixes | 11/11 arm runs valid; all seven controls passed | [native-smoke.json](native-smoke.json) |
| Native candidate attempts | 38 logged; 1 deliberately invalid C attempt; 7 certified-exact attempts | [native-costs.json](native-costs.json) |
| Deliberate layout-key stress | 4 conclusive equal-key pairs, 0 violations, 0 unavailable | [key-stress.json](key-stress.json) |
| `__LINE__` negative key control | 1/1 pair changed the key | [line-stress.json](line-stress.json) |

The two Windows skips require frozen assembly artifacts present in WSL. The 38 candidate attempts exclude three synthetic target-construction compiles and seven separately recorded optimizer-key evaluations. The exact attempts include repeated synthetic positive controls; they are not seven discovered functions. The four layout pairs are a deliberately small stress sample, not a failure-rate estimate.

Native artifacts were built at `/home/grant/decomp/experiments/research-suite-20260928-smoke-03`. The complete captured sources, objects, input manifests, compiler/verification receipts, and results are in [native-smoke-artifacts.tar.gz](native-smoke-artifacts.tar.gz). Earlier smoke runs passed before final review hardening; the packaged run includes the symbol/assistance checks and the decimal-literal grammar correction.

## What the tests establish

- A bounded archive can retain a useful same-object source alternative in the motivating graph; exploration can cross a worse-ranked intermediate. These tests use explicit synthetic oracles.
- Real IDO rejects the nonmatching control, certifies the matching control, and records the deliberate compiler failure.
- The narrow composition and type-view generators emit changed C that compiles and receives the existing object certificate in their synthetic motivating cases.
- A retained execution case falsifies a candidate that passed the fixed panel; target faults/ABI debt stay inconclusive, and all candidates use the same versioned inputs.
- Frozen inputs reject tampering, historical metric reconstruction rejects incomplete/ambiguous input, budgets include failures/restarts, and equal-key checks distinguish conclusive results from unavailable ones.
- Source-bound frontend/object checks are required for exactness. Wrong function symbols are refused. Recorded proposals retain inherited assistance, including header-assisted winners.

Review led to fixes for live header identity, frontend/key recipe consistency, macro-preserving stress generation, newline ambiguity, replayable counterexamples, proposal assistance, function/object binding, distinct-function reporting, setup-time accounting, and refusing octal literals in the decimal-only rewrite grammar. Standalone JSON summaries explicitly identify their input as unverified.

## Reproduction

Commands and input schemas are in [the suite README](../../research_suite/README.md). The regression selection used:

```text
tests/test_research_search.py
tests/test_research_counterexamples.py
tests/test_research_proposals.py
tests/test_research_suite.py
tests/test_research_runner.py
tests/test_research_cli.py
tests/test_regalloc_search.py
tests/test_investigation_loop.py
tests/test_execution_experiment.py
tests/test_search_replay.py
tests/test_mips_differential.py
tests/test_mips_differential_grounded.py
tests/test_regalloc_signature.py
tests/test_invariants.py
tests/test_byte_certificate.py
tests/test_compiler_experiment.py
tests/test_frontend_check.py
```

Run this list with `python -m pytest -q` and optionally `--junitxml=<new-receipt.xml>`. The native smoke command was:

```bash
/home/grant/decomp/sbk1/.venv/bin/python -m eval.research_suite smoke \
  --repo /home/grant/decomp/sbk1 \
  --output /home/grant/decomp/experiments/research-suite-20260928-smoke-03
```

Use a fresh output directory when repeating it. The full repository test suite was not the acceptance check: an exploratory Windows-wide collection by a worker encountered unrelated external-package import conflicts and the Unix-only `fcntl` campaign service import. The relevant selected tests above completed.

The next empirical step is a frozen unresolved development cohort with paired budgets/seeds and assistance-aware reporting. No campaign promotion or production default was changed by this suite.
