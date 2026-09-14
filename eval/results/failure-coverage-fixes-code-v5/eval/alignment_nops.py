"""How many candidates are failing only on workspace alignment padding?

The reference project's own learnings document states it plainly:

    Object-relative alignment nops are padding, not logic. IDO may emit an
    alignment directive before an unreachable epilogue or the next function.
    A single-function workspace starts at a different section offset than the
    real multi-function object, so the same directive can produce a different
    number of trailing nops even when the live instructions match.

Our oracle compiles each candidate in an ISOLATED workspace. So for any
function whose residual is only trailing nops, the score is measuring our
harness rather than the candidate -- the candidate may already be correct and
we would never know. bootThreadMain is exactly this shape: one missing nop
after an infinite loop's dead epilogue, and sixteen C-level variants moved it
not at all, because nothing in C can move it.

This counts how widespread that is. A function listed here is NOT a match --
it is a function whose verdict our current harness cannot deliver.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import workspace

NOP = re.compile(r"^\s*nop\s*$")


def classify(diff: str) -> tuple[int, int, int]:
    """(nop-only differing lines, other differing lines, net nop delta)."""
    nops = others = delta = 0
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")) or line[:1] not in "+-":
            continue
        body = line[1:]
        if NOP.match(body):
            nops += 1
            delta += 1 if line[0] == "-" else -1
        else:
            others += 1
    return nops, others, delta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " where a.compiled = 1 group by f.addr order by best desc").fetchall()
        if r[0] not in done]

    pure, mixed = [], []
    for name, best in rows:
        src = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not src:
            continue
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, src[0])
        if not att.compiled or att.exact:
            continue
        nops, others, delta = classify(att.diff or "")
        if nops and not others:
            pure.append((name, att.score, nops, delta))
        elif nops:
            mixed.append((name, att.score, nops, others))

    print("RESIDUAL IS NOTHING BUT nop PADDING -- the candidate may already be")
    print("correct and our isolated workspace cannot tell us:")
    for name, score, nops, delta in pure:
        print(f"   {name[:46]:46} {score:7.3f}  {nops} nop line(s), "
              f"net {delta:+d}")
    if not pure:
        print("   (none)")

    print(f"\npartly nop padding ({len(mixed)}): score understated by an "
          f"unknown amount")
    for name, score, nops, others in mixed[:10]:
        print(f"   {name[:46]:46} {score:7.3f}  {nops} nop / {others} real")

    print(f"\npure alignment-blocked: {len(pure)}   partly affected: "
          f"{len(mixed)}")
    print("\nThese cannot be resolved by changing C. The reference project's"
          "\nguidance is to verify in the real translation unit rather than"
          "\nforcing workspace padding from the source.")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"pure": [{"function": n, "score": s, "nops": k, "delta": d}
                      for n, s, k, d in pure],
             "mixed": [{"function": n, "score": s, "nops": k, "other": o}
                       for n, s, k, o in mixed]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
