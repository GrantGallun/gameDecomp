"""Recompile every claimed byte-exact match and confirm the oracle agrees.

The headline number jumped from 44 to 87 inside one session while another
agent was working in the same tree. This project has mis-stated that number
twice before -- once reporting 7 for 34, once claiming a match that already
existed -- so the count is re-derived from the oracle rather than trusted from
a flag in a row.

This writes fresh compiler artifacts (not project source or historical DB rows).
It replays a deterministic latest exact attempt and requires an independent
object-section certificate. A normalized assembly match alone is insufficient.

    python3 -m eval.verify_all_exact
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from solver import workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    rows = conn.execute(
        "select f.name, a.source_code, a.score from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.exact = 1 and a.source_code is not null"
        " and a.id = (select max(b.id) from attempts b where b.func_addr=a.func_addr"
        " and b.exact=1 and b.source_code is not null) order by f.name").fetchall()
    conn.close()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} functions carry an exact=1 attempt\n")

    ok, bad, err = [], [], []
    for name, src, score in rows:
        try:
            ws = workspace.bootstrap(repo, name)
            att = workspace.score(ws, repo, f"{name}_recertify_{time.time_ns()}", src)
        except Exception as exc:                   # noqa: BLE001
            err.append((name, str(exc)[:70]))
            print(f"  {name:<48} ERROR {str(exc)[:40]}")
            continue
        if att.exact:
            ok.append(name)
        else:
            bad.append((name, att.score, att.compiled))
            print(f"  {name:<48} NOT EXACT (score={att.score:.3f}, "
                  f"compiled={att.compiled})")

    print("\n" + "=" * 66)
    print(f"reproduced exact : {len(ok)}")
    print(f"did NOT reproduce: {len(bad)}")
    print(f"errored          : {len(err)}")
    if bad:
        print("\nThese are counted as matches but do not reproduce:")
        for name, sc, comp in bad:
            print(f"   {name:<46} score={sc:.3f} compiled={comp}")
    return 1 if bad or err else 0


if __name__ == "__main__":
    raise SystemExit(main())
