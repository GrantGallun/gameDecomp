"""Extend the dev split of a frozen eval set, leaving heldout untouched.

Iteration 5 produced an accidental control: the small tier received identical
prompts across two runs and its mean still moved +9.8 points. So tier-mean
run-to-run variance is about +/-10 at n=10 -- the same size as the effects
being measured. Reading a 5-point tier move as signal was wrong.

Doubling dev roughly cuts the standard error by 30% (1/sqrt(2)). That is not a
cure, so it ships alongside an explicit significance floor rather than
pretending precision it does not have.

Heldout entries are copied through verbatim and their functions are excluded
from the new draws. Extending dev must never perturb the set that produces the
final number.

Run:
    python3 -m eval.extend_set --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1 \\
        --in eval/sets/sbk1_v1.json --out eval/sets/sbk1_v2.json --add 5
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

from eval.feasibility import check
from eval.sets import EXCLUDE_TU, TIERS, candidates


def extend(db: Path, repo: Path, src: Path, add: int, seed: int) -> dict:
    sets = json.loads(src.read_text())
    conn = sqlite3.connect(db)
    rng = random.Random(seed)

    assigned = {e["function"] for e in sets["dev"]}
    assigned |= {e["function"] for e in sets["heldout"]}
    assigned |= {e["function"] for e in sets.get("skipped_infeasible", [])}

    added, skipped = [], []
    for tier, lo, hi in TIERS:
        for is_leaf in (1, 0):
            pool = [f for f in candidates(conn, lo, hi, is_leaf)
                    if f not in assigned]
            rng.shuffle(pool)
            taken = 0
            for name in pool:
                if taken >= add:
                    break
                ok, reason = check(repo, name)
                if not ok:
                    skipped.append({"function": name, "tier": tier,
                                    "leaf": bool(is_leaf), "reason": reason})
                    assigned.add(name)
                    continue
                added.append({"function": name, "tier": tier,
                              "leaf": bool(is_leaf)})
                assigned.add(name)
                taken += 1

    out = dict(sets)
    out["dev"] = sets["dev"] + added
    out["heldout"] = sets["heldout"]          # untouched, byte for byte
    out["skipped_infeasible"] = sets.get("skipped_infeasible", []) + skipped
    out["extended_from"] = src.name
    out["extend_seed"] = seed
    out["significance_floor"] = (
        "Tier-mean deltas under 10 points and whole-set exact deltas under 3 "
        "are within measured run-to-run noise and are NOT evidence.")
    return out, added, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--add", type=int, default=5, help="new dev fns per stratum")
    ap.add_argument("--seed", type=int, default=20260828)
    args = ap.parse_args()

    out, added, skipped = extend(args.db.expanduser(), args.repo.expanduser(),
                                 args.src, args.add, args.seed)
    args.out.write_text(json.dumps(out, indent=2))

    src = json.loads(args.src.read_text())
    print(f"wrote {args.out}")
    print(f"  dev     : {len(src['dev'])} -> {len(out['dev'])}  (+{len(added)})")
    print(f"  heldout : {len(out['heldout'])} (unchanged)")
    print(f"  skipped : +{len(skipped)} infeasible")

    from collections import Counter
    c = Counter((e["tier"], "leaf" if e["leaf"] else "non-leaf") for e in out["dev"])
    print("\nnew dev composition:")
    for k in sorted(c, key=lambda k: (["tiny", "small", "medium", "large"].index(k[0]), k[1])):
        print(f"  {k[0]:7} {k[1]:9} {c[k]}")


if __name__ == "__main__":
    main()
