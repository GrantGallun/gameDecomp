"""Are the disputed matches real, or is the primary oracle right to refuse?

Nine functions carry an exact verdict from tools/relocated_oracle.py that the
per-function build.sh oracle does not reproduce. Two readings, and they demand
opposite actions:

  the relocated oracle is too lax   -> the match count is inflated
  the primary oracle has a known
  false negative                    -> the count is right and normalize_asm.py
                                       is holding nine matches hostage

The primary oracle's own normalize_asm.py exists to rewrite jump-table symbols
to .rodata so the two sides compare, and its pattern requires TWO hex parts
(jtbl_<hex>_<hex>) while real symbols like jtbl_800E08BC have one. So the
false-negative reading is testable: if a disputed function's ENTIRE residual is
symbol naming, with no instruction differing, the code is byte-identical and
only the label differs.

This classifies each disputed residual. Nothing is written and no verdict is
changed; the count stays whatever the oracles say.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from solver import signals, workspace

SYMBOL = re.compile(r"%(?:hi|lo)\(([^)]*)\)")


def naming_only(a: str, b: str) -> bool:
    """True when two instructions differ ONLY in a relocation symbol name."""
    if SYMBOL.sub("%SYM", a) != SYMBOL.sub("%SYM", b):
        return False
    return SYMBOL.findall(a) != SYMBOL.findall(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--strategy", default="relocated-oracle-exact")
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    rows = conn.execute(
        "select f.name, a.source_code from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.exact = 1 and a.strategy = ? and a.source_code is not null"
        " group by f.name order by f.name", (args.strategy,)).fetchall()
    print(f"{len(rows)} functions carry a {args.strategy} verdict\n")

    naming, other = [], []
    for name, src in rows:
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, src)
        if att.exact:
            print(f"{name:<46} primary oracle AGREES")
            continue
        pairs, n_minus, n_plus = signals._pairs(att.diff or "")
        pure = bool(pairs) and all(naming_only(a, b) for a, b in pairs) \
            and n_minus == n_plus == len(pairs)
        (naming if pure else other).append((name, att.score, pairs))
        verdict = "SYMBOL NAMING ONLY" if pure else "REAL INSTRUCTION DIFF"
        print(f"{name:<46} {att.score:8.4f}  {len(pairs):2} pairs  {verdict}")
        if not pure:
            for a, b in pairs[:4]:
                print(f"      -{a:<40} +{b}")

    print("\n" + "=" * 70)
    print(f"symbol naming only    : {len(naming)}")
    print(f"real instruction diff : {len(other)}")
    if naming and not other:
        print("\nEvery disputed residual is a label, not code. The primary"
              "\noracle's normalize_asm.py handles jtbl_<hex>_<hex> and misses"
              "\njtbl_<hex>; widening that pattern would make the two oracles"
              "\nagree. That changes the match criterion, so it is the"
              "\nmaintainer's call, not an agent's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
