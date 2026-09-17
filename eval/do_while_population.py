"""Which functions does a `do { ... } while (...)` in the ROM-verified source belong to?

The helper's `do` ban forced a lowering that is not codegen-neutral (measured: matching -> 99.395 on
drawRaceSplitscreenSelectOption2Frame). This sizes the exposure: parse the reference tree, attribute
each `do {` to its enclosing function definition, and cross-tabulate against the knowledge base.

    python3 eval/do_while_population.py [--repo ~/decomp/sbk1] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFINITION = re.compile(r"^[A-Za-z_][A-Za-z0-9_ \t*]*?\b(?P<name>[A-Za-z_]\w*)\s*\([^;{]*\)\s*\{", re.M)
DO = re.compile(r"(?<![A-Za-z0-9_])do\s*\{")
DB = Path.home() / "decomp" / "kb-sbk1.sqlite"


def functions_with_do(source: str) -> list[str]:
    """Names of definitions whose brace-matched body contains a `do {`."""
    out = []
    for match in DEFINITION.finditer(source):
        start = source.index("{", match.end() - 1)
        depth = 0
        for index in range(start, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    body = source[start:index + 1]
                    if DO.search(body):
                        out.append(match.group("name"))
                    break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    found: dict[str, list[str]] = {}
    occurrences = 0
    for path in sorted((args.repo / "src").rglob("*.c")):
        text = path.read_text(errors="replace")
        occurrences += len(DO.findall(text))
        for name in functions_with_do(text):
            found.setdefault(name, []).append(str(path.relative_to(args.repo)))
    names = sorted(found)
    print(f"`do {{` occurrences: {occurrences}   functions containing one: {len(names)}")

    conn = sqlite3.connect(args.db)
    known = {r[0] for r in conn.execute("select name from functions")}
    exact = {r[0] for r in conn.execute(
        "select distinct f.name from functions f join attempts a on a.func_addr = f.addr "
        "where a.exact = 1")}
    attempted = {r[0] for r in conn.execute("select distinct func_addr from attempts")}
    attempted_names = {r[0] for r in conn.execute(
        "select distinct f.name from functions f join attempts a on a.func_addr = f.addr")}
    buckets = Counter()
    for name in names:
        if name not in known:
            buckets["not in KB"] += 1
        elif name in exact:
            buckets["already exact"] += 1
        elif name in attempted_names:
            buckets["attempted, not exact"] += 1
        else:
            buckets["never attempted"] += 1
    print("KB status of the do-bearing functions:", dict(buckets))
    live = [n for n in names if n in known and n not in exact and n in attempted_names]
    never = [n for n in names if n in known and n not in exact and n not in attempted_names]
    print(f"\nlive residue with a `do` in the key ({len(live)}):")
    print("   ", ", ".join(live[:30]))
    print(f"\nnever attempted with a `do` in the key ({len(never)}):")
    print("   ", ", ".join(never[:30]))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"occurrences": occurrences, "functions": names,
                                        "buckets": dict(buckets), "live": live, "never": never,
                                        "files": found}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
