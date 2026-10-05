"""Paired comparison of graded logic-exam arms against base (LOGIC_PILOT.md).

    python3 logic_compare.py BASE_GRADES.jsonl ARM_GRADES.jsonl [ARM_GRADES.jsonl ...] [--metric rows|label]

Per (split, kind, variant): n, base correct, arm correct, gained (arm right, base wrong), lost (base right, arm wrong).
Both grade files must cover the same task ids; a mismatch is refused, not averaged over.
"""
import argparse
import collections
import json
from pathlib import Path


def load(path):
    return {r["id"]: r for r in map(json.loads, Path(path).read_text().splitlines())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--metric", default="rows", choices=("rows", "label"))
    a = ap.parse_args()
    base = load(a.base)
    for path in a.arms:
        arm = load(path)
        if set(arm) != set(base):
            raise SystemExit(f"{path} grades {len(arm)} tasks, base {len(base)}: not the same exam")
        by = collections.defaultdict(collections.Counter)
        for tid, b in base.items():
            r = arm[tid]
            key = (b["split"], b["kind"], b["variant"])
            by[key]["n"] += 1
            by[key]["base"] += bool(b[a.metric])
            by[key]["arm"] += bool(r[a.metric])
            by[key]["gained"] += bool(r[a.metric]) and not b[a.metric]
            by[key]["lost"] += bool(b[a.metric]) and not r[a.metric]
        print(f"== {Path(path).stem} vs base ({a.metric})")
        for (split, kind, variant), c in sorted(by.items()):
            print(f"  {split:6} {kind:14} {variant:9} n {c['n']:4}  base {c['base']:4}  arm {c['arm']:4}  "
                  f"+{c['gained']:<4} -{c['lost']}")


if __name__ == "__main__":
    main()
