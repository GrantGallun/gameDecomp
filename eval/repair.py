"""Try to finish near-miss candidates deterministically. No model, no GPU.

The near-miss census showed the dominant residual is struct layout -- offsets
and access widths -- which repad owns. This applies the deterministic repair
passes to the best stored candidate of every function above a score floor and
reports what the ORACLE says, not what the score prints.

    python3 -m eval.repair --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1

Every result here is byte-exact or it is nothing: a repair that improves the
score without matching has not finished the function, and this prints the
residual so the next gap is visible rather than rounded away.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import c89, structgen, tracefix, workspace


def observed_for(conn, func: str) -> dict[int, int]:
    """offset -> width, from the evidence tier, flattened across parameters."""
    out: dict[int, int] = {}
    for _base, entries in (structgen.layout(conn, func) or {}).items():
        for off, width, _ty in entries:
            out[off] = width
    return out


def passes(src: str, *, conn, func: str, repo: Path, ws: Path):
    """Yield (label, code) for each repair worth trying, cheapest first."""
    obs = observed_for(conn, func)
    yield "baseline", src

    padded, changed = structgen.repad(src, obs)
    if changed:
        yield "repad", padded

    try:
        asm = workspace.target_asm(ws, func)
        traced, _log = tracefix.fix_calls(src, asm)
        if traced != src:
            yield "tracefix", traced
        both, ch2 = structgen.repad(traced, obs)
        if ch2 and both != padded:
            yield "tracefix+repad", both
    except Exception:
        pass

    c = c89.to_c89(src)
    if c != src:
        yield "c89", c
        cp, ch3 = structgen.repad(c, obs)
        if ch3:
            yield "c89+repad", cp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=90.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    rows = conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ? and best < 100.0"
        " order by best desc", (args.floor,)).fetchall()

    # A function matched only on disk still has sub-100 attempts logged, so
    # max(score) < 100 lists it as a near miss when it is already solved.
    done = matched_mod.already_matched(conn)
    hidden = [n for n, _b in rows if n in done]
    rows = [(n, b) for n, b in rows if n not in done]
    print(f"{len(rows)} functions between {args.floor} and byte-exact")
    if hidden:
        print(f"(excluded {len(hidden)} already matched elsewhere: "
              f"{', '.join(hidden[:4])})")
    print()
    wins, improved = [], []

    for name, best in rows:
        row = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not row:
            continue
        ws = workspace.bootstrap(repo, name)

        best_label, best_score, best_exact, best_code = "baseline", best, False, ""
        for label, code in passes(row[0], conn=conn, func=name, repo=repo,
                                  ws=ws):
            if label == "baseline":
                continue
            att = workspace.score(ws, repo, name, code)
            if att.exact:
                best_label, best_score, best_exact, best_code = (
                    label, att.score, True, code)
                break
            if att.compiled and att.score > best_score:
                best_label, best_score, best_code = label, att.score, code

        if best_exact:
            wins.append((name, best, best_label))
            print(f"  MATCH   {name[:46]:46} {best:7.3f} -> EXACT"
                  f"  via {best_label}")
        elif best_score > best + 0.0005:
            improved.append((name, best, best_score, best_label))
            print(f"  better  {name[:46]:46} {best:7.3f} ->"
                  f" {best_score:7.3f}  via {best_label}")
        else:
            print(f"  --      {name[:46]:46} {best:7.3f}  no repair applied")

    print(f"\n{'=' * 68}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, b, lab in wins:
        print(f"   {n}   {b:.3f} -> EXACT via {lab}")
    print(f"improved but still not matching: {len(improved)}")
    for n, b, s, lab in improved:
        print(f"   {n}   {b:.3f} -> {s:.3f} via {lab}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"matches": [{"function": n, "was": b, "via": l} for n, b, l in wins],
             "improved": [{"function": n, "was": b, "now": s, "via": l}
                          for n, b, s, l in improved]}, indent=1))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
