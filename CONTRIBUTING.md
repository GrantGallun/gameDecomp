# Contributing

gameDecomp is a research project with one maintainer, and the pipeline changes daily. Issues and small
pull requests are welcome. For anything larger, open an issue first so the approach is agreed before you
spend time on it.

## Setup

Follow the quick start in the [README](README.md#quick-start-the-test-suite): Python 3.12,
`pip install -r requirements.txt`, then `python -m pytest -q -rs`. The suite needs no ROM or toolchain.
Tests that need one skip and name what is missing. To run the full pipeline (WSL2, IDO 5.3 through
`ido-static-recomp`, the target decomp), follow [docs/RUNNING.md](docs/RUNNING.md). `./gd doctor`
shows what is still missing.

## Rules that are not negotiable

- **No game data.** Never commit a ROM, code or assembly derived from a game binary, or files from a
  reference decompilation. `tests/fixtures/` and `matched_recovered/` are gitignored for this reason. A
  test that needs such an input must skip when it is absent. `tests/conftest.py` does this for
  `tests/fixtures/`, `eval/results/` and `.cache/`.
- **Byte-exact or it didn't happen.** A function counts as matched only when its object code is
  identical to the original's. Similarity scores guide search. They are not results.
- **No reference source in prompts or training data.** Held-out reference code must never reach a model,
  including as a few-shot example. See "Evaluation hygiene" in [CLAUDE.md](CLAUDE.md).
- **Evidence is immutable.** The evidence tier is derived mechanically from the binary. Nothing may write
  it from a model's output, and every inference must cite evidence.
- **New passes must fire.** A new repair or analysis pass needs a test showing it fires on the case that
  motivated it, not only tests showing it declines elsewhere. A pass that returns nothing looks exactly
  like a pass with nothing to do.

## Before changing pipeline wiring

Read [PIPELINE_MAP.md](PIPELINE_MAP.md). It maps each mechanism to the controller that runs it, and it
must be updated when wiring changes. [DESIGN.md](DESIGN.md) has the architecture, and
[CLAUDE.md](CLAUDE.md) has the invariants and the analysis bugs they were written to prevent.

## Pull requests

- Keep them small and include tests. CI runs the suite on every push.
- Label every number as measured or predicted. A claimed improvement says what was measured, on which
  set, and where the receipt is.
- Record failed ideas in [memory/hypothesis-graveyard.md](memory/hypothesis-graveyard.md) so they are
  not retried.
