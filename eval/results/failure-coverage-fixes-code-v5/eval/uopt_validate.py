"""Does the uopt model reproduce IDO's own allocation? Check before using it.

The model in solver/uopt.py is half quoted specification and half assumption
(totalsave in particular). A model that cannot predict the allocation IDO
already produced must not be used to steer a search, so this validates it
against the only ground truth that is free of contamination: the functions we
have ALREADY matched, whose sources are ours and whose objects are byte-exact.

The law under test is the ordering one -- webs with higher `save` take
lower-indexed registers. That is checkable without knowing totalsave exactly,
because only the ORDER has to survive.

A concordance near 50% means the model carries no signal at all: with two webs
picked at random, it would be guessing which holds the lower register.

    python3 -m eval.uopt_validate --db ~/decomp/kb-sbk1.sqlite \\
        --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from pathlib import Path

from solver import asmlayer, uopt


def matched_sources(conn, root: str = "matched_recovered") -> dict[str, str]:
    """Sources for functions verified byte-exact -- ours, never the reference."""
    out: dict[str, str] = {}
    for path in glob.glob(f"{root}/*.c"):
        out[os.path.basename(path)[:-2]] = Path(path).read_text(errors="replace")
    try:
        for name, src in conn.execute(
                "select f.name, a.source_code from attempts a"
                " join functions f on f.addr = a.func_addr"
                " where a.exact = 1 and a.source_code is not null"
                " order by a.id desc"):
            out.setdefault(name, src)
    except sqlite3.OperationalError:
        pass                                  # older db without the receipt
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    sources = matched_sources(conn)
    print(f"verified-exact sources available: {len(sources)}\n")

    print(f"{'function':44} {'webs':>5} {'pairs':>6} {'concord':>8}")
    print("-" * 68)
    total_c = total_n = 0
    rows = []
    for name in sorted(sources):
        asm, err = asmlayer.compile_s(repo, sources[name])
        if not asm:
            continue
        ws = uopt.webs(asm)
        uopt.mark_precolored(ws, asm)
        if len(ws) < 2:
            continue
        c, n = uopt.rank_agreement(ws)
        if not n:
            continue
        total_c += c
        total_n += n
        rows.append({"function": name, "webs": len(ws), "pairs": n,
                     "concordant": c})
        print(f"{name[:44]:44} {len(ws):5} {n:6} {100*c/n:7.1f}%")

    if not total_n:
        print("\nno comparable pairs -- the model cannot be validated this way")
        return 1

    pct = 100 * total_c / total_n
    print(f"\noverall concordance: {total_c}/{total_n} = {pct:.1f}%")
    print("chance is 50%. A model at chance carries no signal and must not")
    print("steer a search.")
    if pct < 55:
        print("\nVERDICT: no usable signal. The ordering law as modelled does")
        print("not predict IDO's allocation -- most likely the totalsave")
        print("assumption, or webs reconstructed from post-coloring output.")
    elif pct < 70:
        print("\nVERDICT: weak signal. Better than chance but far from a")
        print("specification; not yet fit to rank candidates.")
    else:
        print("\nVERDICT: the ordering law reproduces here. Worth using.")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"concordant": total_c, "pairs": total_n, "pct": pct,
             "functions": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
