"""Regression: the composition search must rediscover both known matches.

Both were closed by hand after an external review, and the review's point was
that hand edits prove nothing -- they have to be generalised. This runs the
search from each function's PRE-EXPERIMENT baseline, i.e. the best candidate
that existed before any of my manual edits were logged, and asserts it reaches
byte-exact on its own.

Starting from the best candidate outright would be a vacuous test: my own
intermediate variants are in the attempts table now, so the search would be
handed a half-solved source and credited with finishing it. That mistake was
made once already and is what this guards against.

    python3 -m eval.compose_regress --db ~/decomp/kb-sbk1.sqlite \\
        --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from eval.compose import search
from solver import workspace

# (function, ceiling) -- the best score that existed BEFORE any manual edit.
# Selecting strictly below the ceiling excludes my own logged intermediates.
CASES = [
    ("updateRaceSplitscreenSelectPlayerCountIcons", 99.93),
    ("updateEndingLindaExitUntilPhase3C", 99.60),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))

    failures = 0
    for name, ceiling in CASES:
        row = conn.execute(
            "select a.score, a.strategy, a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.compiled = 1 and a.source_code is not null"
            "   and a.score < ? order by a.score desc limit 1",
            (name, ceiling)).fetchone()
        if not row:
            print(f"{name}: no pre-experiment baseline below {ceiling}")
            failures += 1
            continue
        start, strategy, src = row
        ws = workspace.bootstrap(repo, name)
        print(f"\n{name}")
        print(f"  baseline {start:.3f} (from {strategy})")
        att, _best, log = search(repo, name, src, ws, verbose=False)
        for line in log:
            print(f"    {line}")
        verdict = "EXACT" if att.exact else f"{att.score:.3f}"
        print(f"  result: {verdict}")
        if not att.exact:
            failures += 1

    print(f"\n{'=' * 60}")
    if failures:
        print(f"{failures} of {len(CASES)} did NOT reach exact from their "
              f"pre-experiment baseline.")
        print("The search has not yet generalised the hand-applied fixes.")
    else:
        print(f"all {len(CASES)} rediscovered from their pre-experiment "
              f"baseline")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
