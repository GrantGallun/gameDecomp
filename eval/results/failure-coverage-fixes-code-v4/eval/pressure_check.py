"""Does the candidate hold more values across calls than the target does?

The allocation residual has resisted two approaches, both of which tried to
predict which web gets which colour. This asks a structural question instead:
at each call site, how many caller-saved values must survive the call? A
candidate carrying one more than the target is holding a value the target
recomputes, which IS an extra web -- and unlike a priority prediction it names
a program point and suggests its own remedy.

Reports the profile difference per function. Proposes nothing: if the profiles
match everywhere, this line of attack is dead and should be recorded as such
rather than pursued.

    python3 -m eval.pressure_check --tier medium
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import diffrepair, liveness, signals, workspace


def tier_of(size, insn) -> str:
    n = insn or (size or 0) // 4
    return ("tiny" if n < 20 else "small" if n < 60 else
            "medium" if n < 150 else "large" if n < 300 else "huge")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--tier", default="")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    done = matched_mod.already_matched(conn, repo)
    rows = conn.execute(
        "select f.name, f.size, f.insn_count, max(a.score), a.source_code"
        " from attempts a join functions f on f.addr = a.func_addr"
        " where a.compiled = 1 and a.source_code is not null"
        " group by f.name").fetchall()

    targets = []
    for name, size, insn, best, src in rows:
        if name in done:
            continue
        if args.tier and tier_of(size, insn) != args.tier:
            continue
        if args.only and name not in args.only:
            continue
        targets.append((name, best, src))
    targets.sort(key=lambda r: -r[1])
    print(f"{len(targets)} functions\n")

    hdr = (f"{'function':<44}{'calls':>6}{'target live':>13}"
           f"{'cand live':>11}  verdict")
    print(hdr)
    print("-" * len(hdr))

    differ = same = skipped = 0
    for name, best, src in targets:
        try:
            ws = workspace.bootstrap(repo, name)
            att = workspace.score(ws, repo, name, src)
        except Exception:                          # noqa: BLE001
            continue
        if not att.compiled or not att.diff:
            continue
        tgt_stream, cand_stream = diffrepair._streams(att.diff)
        if not tgt_stream or not cand_stream:
            skipped += 1
            continue
        tp = liveness.pressure_profile("\n".join(tgt_stream))
        cp = liveness.pressure_profile("\n".join(cand_stream))
        if not tp and not cp:
            skipped += 1
            continue
        if len(tp) != len(cp):
            verdict = "different CALL COUNT"
            differ += 1
        elif tp == cp:
            verdict = "identical"
            same += 1
        else:
            delta = sum(c - t for t, c in zip(tp, cp))
            verdict = f"DIFFERS (candidate {delta:+d} live overall)"
            differ += 1
        print(f"{name:<44}{len(cp):>6}{str(tp):>13}{str(cp):>11}  {verdict}")

    print("-" * len(hdr))
    print(f"profiles differ: {differ}   identical: {same}   "
          f"unusable: {skipped}")
    if differ == 0:
        print("\nNo function holds a different number of values across its "
              "calls than the target.\nRegister pressure is NOT the "
              "difference; record this line as refuted rather than\nbuilding "
              "a generator on it.")
    else:
        print("\nWhere the candidate carries MORE live values than the "
              "target, it is holding a\nvalue the target recomputes. That is "
              "an extra web with a program point attached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
