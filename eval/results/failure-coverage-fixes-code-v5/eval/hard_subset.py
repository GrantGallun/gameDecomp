"""Build an eval set of only the functions that actually fail.

The wall is above `small`: 71% of tiny functions match, and the rate collapses
to 16% / 6% / 0% across small, medium and large. Re-running all 87 functions to
study that wall spends most of the compute re-matching tiny functions that
already match, and the answer is always the same.

A hard subset -- the unmatched functions from a completed run -- makes each
experiment on the wall several times cheaper, which is what makes questions
like "does a bigger sampling budget help?" or "does a stronger model help?"
answerable in one sitting rather than one night.

This is a DEV instrument. It is derived from dev results, so it inherits their
tuning exposure and can never be used to report a headline number. Held-out
stays untouched and remains the only honest source of a final figure.

Run:
    python3 -m eval.hard_subset --results eval/sets/sbk1_v3_dev_....jsonl \\
        --out eval/sets/hard_v1.json --max-score 99.9
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def build(results: Path, max_score: float, min_score: float,
          tiers: set[str] | None) -> dict:
    rows = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]

    hard = []
    for r in rows:
        if r.get("draws", 0) == 0:
            continue                       # never ran; not evidence of failure
        if r["exact"]:
            continue
        if not (min_score <= r["best_score"] <= max_score):
            continue
        if tiers and r["tier"] not in tiers:
            continue
        hard.append({"function": r["function"], "tier": r["tier"],
                     "leaf": r["leaf"], "prior_score": r["best_score"],
                     "prior_route": r.get("route", "")})

    hard.sort(key=lambda e: -e["prior_score"])
    return {
        "derived_from": results.name,
        "note": ("Unmatched functions from a completed dev run. A DEV "
                 "instrument only -- derived from dev results, so it carries "
                 "their tuning exposure and must never produce a headline "
                 "number. Held-out remains the only honest final figure."),
        "selection": {"min_score": min_score, "max_score": max_score,
                      "tiers": sorted(tiers) if tiers else "all"},
        "dev": hard,
        "heldout": [],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--max-score", type=float, default=99.9)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--tiers", default="", help="comma list, e.g. medium,large")
    args = ap.parse_args()

    tiers = {t.strip() for t in args.tiers.split(",") if t.strip()} or None
    sets = build(args.results, args.max_score, args.min_score, tiers)
    args.out.write_text(json.dumps(sets, indent=2))

    c = Counter(e["tier"] for e in sets["dev"])
    print(f"wrote {args.out}")
    print(f"  hard functions: {len(sets['dev'])}")
    for t in ["tiny", "small", "medium", "large", "huge"]:
        if t in c:
            print(f"    {t:8} {c[t]}")
    if sets["dev"]:
        scores = [e["prior_score"] for e in sets["dev"]]
        print(f"  prior scores: min {min(scores):.1f}%  "
              f"median {sorted(scores)[len(scores)//2]:.1f}%  max {max(scores):.1f}%")
        near = sum(1 for s in scores if s >= 95)
        print(f"  already >=95% (permuter territory): {near}")


if __name__ == "__main__":
    main()
