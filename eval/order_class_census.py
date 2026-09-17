"""Which instruction classes actually carry an `order` residual we could compute rather than search?

Blind permutation cost 7,305 attempts and 0 exact closures (`eval/results/family-yield-20260917/`).
Diff-READ permutation closed Fstop (five independent stores). And the analogous change on a LOAD
regressed updateRacePlayerMode16AerialTrick. So the useful question is not "does order matter" but
"for which instruction classes is the target's emission order a source statement order" -- and that is
answerable from the residuals we already have, with no compiles.

This reads each function's best compiling non-exact attempt, classifies the residual with
`patterns.ordering`, and labels the differing instructions by opcode class. The per-class populations
it prints are the test set for the diff-read transform.

    python3 eval/order_class_census.py [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import ordering                                              # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
STORE = re.compile(r"^\s*(sw|sh|sb|swc1|sdc1|swl|swr)\b")
LOAD = re.compile(r"^\s*(lw|lh|lhu|lb|lbu|lwc1|ldc1|lwl|lwr)\b")
MOVE = re.compile(r"^\s*(move|li|lui|mfhi|mflo)\b")
BRANCH = re.compile(r"^\s*(b\w*|j\w*)\b")


def classify_instruction(text: str) -> str:
    if STORE.match(text):
        return "store"
    if LOAD.match(text):
        return "load"
    if MOVE.match(text):
        return "move"
    if BRANCH.match(text):
        return "branch"
    return "arith"


def shape(text: str) -> str:
    """The class mix of one residual: 'store', 'load', 'store+load', ... in first-seen order."""
    kinds = []
    for line in text:
        kind = classify_instruction(line)
        if kind not in kinds:
            kinds.append(kind)
    return "+".join(kinds) if kinds else "none"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(str(args.db))

    rows = conn.execute(
        "select f.name, a.diff_summary from functions f join attempts a on a.func_addr = f.addr "
        "where a.compiled = 1 and coalesce(a.exact,0) = 0 "
        "and a.id = (select b.id from attempts b where b.func_addr = f.addr and b.compiled = 1 "
        "           order by b.score desc, b.id limit 1)").fetchall()
    print(f"live compiling non-exact functions: {len(rows)}")

    causes = collections.Counter()
    by_shape: dict[str, list[str]] = collections.defaultdict(list)
    order_only: list[tuple[str, str, list[str]]] = []
    for name, diff in rows:
        cause = ordering.classify(diff or "")
        causes[cause.name] += 1
        if cause.name != "order":
            continue
        moved = ordering.moved_instructions(diff or "")
        label = shape(moved)
        by_shape[label].append(name)
        order_only.append((name, label, moved))

    print(f"\n=== residual cause across the live set ===\n{dict(causes)}")
    print(f"\n=== the {len(order_only)} `order`-classified residuals, by instruction class ===")
    for label, names in sorted(by_shape.items(), key=lambda kv: -len(kv[1])):
        print(f"  {label:<18} {len(names):>3}   {', '.join(sorted(names)[:6])}"
              f"{' ...' if len(names) > 6 else ''}")

    print("\n=== does the STORE-only rule's precondition hold for each? ===")
    store_only = [n for n, label, _m in order_only if label == "store"]
    print(f"  all-moved-are-stores (StoreOrderRule's scope): {len(store_only)}  {store_only[:8]}")
    non_store = [(n, label) for n, label, _m in order_only if label != "store"]
    print(f"  everything else, i.e. the generalisation target: {len(non_store)}")
    for name, label in non_store[:20]:
        moved = next(m for n, _l, m in order_only if n == name)
        print(f"    {name:<48} {label:<14} {moved[:3]}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"causes": dict(causes), "by_shape": {k: sorted(v) for k, v in by_shape.items()},
             "order_residuals": [{"function": n, "classes": l, "moved": m}
                                 for n, l, m in order_only]}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
