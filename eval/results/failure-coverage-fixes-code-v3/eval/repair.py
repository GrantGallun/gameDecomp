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
from miner import globals_layout
from solver import c89, structgen, tracefix, workspace


def observed_for(conn, func: str, objs=None) -> dict[int, int]:
    """offset -> width, from the evidence tier.

    Two sources, and the second is much larger. Parameter accesses are what
    THIS function shows. Global objects are what the WHOLE PROGRAM shows about
    the same bytes -- measured at 2.7x more offsets on the near-miss set, with
    individual fields observed by up to 161 functions -- and structgen.layout
    excludes them, so they have never reached any repair pass.
    """
    out: dict[int, int] = {}
    for _base, entries in (structgen.layout(conn, func) or {}).items():
        for off, width, _ty in entries:
            out[off] = width

    if objs is None:
        return out

    # Offsets of every global object this function touches, relative to that
    # object's own base -- which is how a candidate declaring a struct for it
    # would number its fields.
    row = conn.execute("select addr from functions where name = ?",
                       (func,)).fetchone()
    if not row:
        return out
    for (base,) in conn.execute(
            "select distinct base from evidence where kind = 'mem_access'"
            " and func_addr = ? and base like 'global:%'", (row[0],)):
        try:
            addr = int(str(base).split(":", 1)[1], 16)
        except (IndexError, ValueError):
            continue
        obj = globals_layout.for_address(objs, addr)
        if not obj:
            continue
        for f in obj.fields:
            out.setdefault(f.offset, f.width)
    return out


def passes(src: str, *, conn, func: str, repo: Path, ws: Path, objs=None):
    """Yield (label, code) for each repair worth trying, cheapest first."""
    obs = observed_for(conn, func, objs)
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

    # Proposals, not repairs. repad needs a declaration to say where it belongs;
    # when none does, guess the mapping by declaration order and let the ORACLE
    # decide. Several leading fields may be undeclared, so try a few starting
    # points -- a small enumeration whose cost is one compile each.
    seen = set()
    for base_label, base_src in (("", src), ("c89+", c)):
        for skip in range(0, 4):
            cand, ok = structgen.align_positional(base_src, obs, skip=skip)
            if ok and cand not in seen:
                seen.add(cand)
                yield f"{base_label}align(skip={skip})", cand
        # the general form: the candidate declares a SUBSET of the fields
        for greedy in ("first", "last"):
            cand, ok = structgen.align_subsequence(base_src, obs, greedy)
            if ok and cand not in seen:
                seen.add(cand)
                yield f"{base_label}subseq({greedy})", cand


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=90.0)
    ap.add_argument("--globals", action="store_true",
                    help="also feed global object layouts to repad")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    rows = conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()

    # A function matched only on disk still has sub-100 attempts logged, so
    # max(score) < 100 lists it as a near miss when it is already solved.
    objs = globals_layout.objects(conn) if args.globals else None
    if objs:
        print(f"global objects available to repad: {len(objs)}"
              f"  ({sum(len(o.fields) for o in objs)} fields)")
    done = matched_mod.already_matched(conn)
    hidden = [n for n, _b in rows if n in done]
    rows = [(n, b) for n, b in rows if n not in done]
    print(f"{len(rows)} non-exact functions at or above {args.floor}")
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
                                  ws=ws, objs=objs):
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
