"""Build and freeze stratified evaluation sets.

Everything measured so far came from small LEAF functions. Leaf functions are
154 of 1,816 game functions in SBK1 -- 8.5% -- and 91.5% of the real work is
non-leaf. A match rate quoted from that slice describes the easiest corner of
the problem, not the problem.

This samples across size tiers AND leaf/non-leaf, then splits into:

    dev      -- may be inspected, tuned against, iterated on freely
    heldout  -- ground truth NEVER inspected, prompts NEVER tuned against it,
                run once when a number is wanted

The split is frozen to JSON with a fixed seed so it is reproducible and so
`heldout` cannot quietly drift toward whatever currently passes. Functions
already studied by hand are barred from `heldout` -- once ground truth has been
read, that function can only ever be dev.

Run:
    python3 -m eval.sets --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1 \\
        --out eval/sets/sbk1_v1.json --per-stratum 6
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

from eval.feasibility import check

# Ground truth already read while debugging. Permanently ineligible for heldout.
TAINTED = {
    "getRaceItemEffectType", "setBootFadeColor", "randomNextSecondary",
    "lockRelocatableHeapBlock", "unlockRelocatableHeapBlock",
    "resetRacePlayerSurfaceCueState", "enableViewportClear",
    "initMenuAssetHandles",
}

# Library code whose source is widely published -- a model may have memorised
# it, which would inflate the score without reflecting any real capability.
EXCLUDE_TU = ("%ultra%", "%libmus%", "%libc%", "%audio%")

# 300+ is a real population -- 85 game functions in SBK1 -- and the hardest.
# Stopping at 300 measured only the part of the problem that was tractable,
# which flatters the number. Review finding 7.
TIERS = [
    ("tiny", 8, 20),
    ("small", 20, 50),
    ("medium", 50, 120),
    ("large", 120, 300),
    ("huge", 300, 100000),
]

QUERY = """
SELECT f.name, f.insn_count, f.is_leaf
FROM functions f JOIN tus t ON t.id = f.tu_id
WHERE t.name LIKE '%src/%'
  AND f.insn_count >= ? AND f.insn_count < ?
  AND f.is_leaf = ?
  {excl}
ORDER BY f.name
"""


def candidates(conn, lo: int, hi: int, is_leaf: int) -> list[str]:
    excl = " ".join(f"AND t.name NOT LIKE '{p}'" for p in EXCLUDE_TU)
    rows = conn.execute(QUERY.format(excl=excl), (lo, hi, is_leaf)).fetchall()
    return [r[0] for r in rows]


def build(db: Path, repo: Path, per_stratum: int, seed: int) -> dict:
    conn = sqlite3.connect(db)
    rng = random.Random(seed)

    dev: list[dict] = []
    heldout: list[dict] = []
    skipped: list[dict] = []

    for tier, lo, hi in TIERS:
        for is_leaf in (1, 0):
            pool = candidates(conn, lo, hi, is_leaf)
            rng.shuffle(pool)

            # Screen for harness-infeasibility BEFORE assigning. A function whose
            # ground truth uses do-while can never be matched, and counting it as
            # a miss understates the model.
            picked: list[str] = []
            for name in pool:
                if len(picked) >= per_stratum * 2:
                    break
                ok, reason = check(repo, name)
                if not ok:
                    skipped.append({"function": name, "tier": tier,
                                    "leaf": bool(is_leaf), "reason": reason})
                    continue
                picked.append(name)

            for idx, name in enumerate(picked):
                entry = {"function": name, "tier": tier, "leaf": bool(is_leaf)}
                # Tainted functions can never be heldout, regardless of the split.
                if name in TAINTED or idx % 2 == 0:
                    dev.append(entry)
                else:
                    heldout.append(entry)

    return {
        "seed": seed,
        "per_stratum": per_stratum,
        "note": ("heldout ground truth must never be inspected and prompts must "
                 "never be tuned against it; run it once when a number is wanted"),
        "dev": dev,
        "heldout": heldout,
        "skipped_infeasible": skipped,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--per-stratum", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260827)
    args = ap.parse_args()

    sets = build(args.db.expanduser(), args.repo.expanduser(),
                 args.per_stratum, args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sets, indent=2))

    print(f"wrote {args.out}")
    print(f"  dev     : {len(sets['dev'])}")
    print(f"  heldout : {len(sets['heldout'])}")
    print(f"  skipped : {len(sets['skipped_infeasible'])} infeasible")
    print()
    for split in ("dev", "heldout"):
        by = {}
        for e in sets[split]:
            key = (e["tier"], "leaf" if e["leaf"] else "non-leaf")
            by[key] = by.get(key, 0) + 1
        print(f"{split}:")
        for k in sorted(by):
            print(f"  {k[0]:7} {k[1]:9} {by[k]}")


if __name__ == "__main__":
    main()
