"""How many unmatched functions have a close, already-matched twin?

The bank's CONFIRMED medium-failures-are-structural ends by naming the only
remaining levers: "sibling mirroring, decomposition, or model capability".
solver/siblings.py implements the first one and every run this session set
siblings=False, so it has never been measured.

Chris Lewis, working the same problem by hand with agents, reports function
similarity scoring (ethteck's coddog, exact bounded Levenshtein rather than
embeddings) as a technique that WORKED, while reporting the same three failure
modes we measured independently -- large functions refused outright, struct
field offsets, and C89 declaration rules.

Before spending a GPU run on it, count how often a usable twin actually exists.
A lever that applies to two functions is not a lever.

CONTAMINATION NOTE: siblings.historical_filter restricts to functions matched
BEFORE the target in git history, which is the honest setting -- the reference
repo is 100% complete and a solver mid-project would not have all of it. This
tool reports both, because the gap between them IS the optimism.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import siblings


BAND_ORDER = ("0.90+", "0.75-0.90", "0.45-0.75", "<0.45", "none")


def similarity_band(score: float) -> str:
    return ("0.90+" if score >= 0.90 else
            "0.75-0.90" if score >= 0.75 else
            "0.45-0.75" if score >= 0.45 else "<0.45")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=0.0,
                    help="only functions whose best score is at least this")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ours", action="store_true",
                    help="restrict the sibling pool to functions WE have "
                         "matched. This is the honest cold-start number: a "
                         "real solver has its own matches, not the reference "
                         "project's finished repo. The --historical filter "
                         "cannot answer this -- it derives order from git "
                         "subjects that cover only 777 of ~2113 functions, "
                         "and none of the current targets.")
    ap.add_argument("--historical", action="store_true",
                    help="restrict to functions matched BEFORE the target in "
                         "the reference project's own history -- the honest "
                         "setting, since the reference repo is 100%% complete "
                         "and a mid-project solver would not have all of it")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)
    own_sources = siblings.verified_sources(conn) if args.ours else None

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]
    if args.limit:
        rows = rows[:args.limit]

    print(f"{len(rows)} unmatched functions with a logged attempt\n")
    print(f"{'function':44} {'best':>7} {'top sib':>8}  name")
    bands = {name: 0 for name in BAND_ORDER}
    results, errors = [], 0
    for name, best in rows:
        try:
            # An external review caught a real design error here: asking for
            # the top N from the FINISHED repo and filtering afterwards means a
            # legitimate sibling ranked below N unavailable reference functions
            # is reported as absent. The allowed pool has to constrain the
            # ranking, so rank everything and then restrict.
            got = siblings.find(repo, name, top=1,
                                min_score=0.0 if args.ours else 0.45,
                                timeout=600, historical=args.historical,
                                allowed=set(own_sources) if own_sources is not None
                                else None)
            got = got[:1]
        except Exception as exc:
            print(f"{name[:44]:44} {best:7.3f}  ERROR {type(exc).__name__}")
            errors += 1
            results.append({"function": name, "best": best,
                            "error": type(exc).__name__})
            continue
        if not got:
            bands["none"] += 1
            print(f"{name[:44]:44} {best:7.3f} {'--':>8}")
            results.append({"function": name, "best": best, "sibling": None})
            continue
        sname, score, _p = got[0]
        band = similarity_band(score)
        bands[band] += 1
        print(f"{name[:44]:44} {best:7.3f} {score:8.2f}  {sname[:34]}")
        results.append({"function": name, "best": best, "sibling": sname,
                        "similarity": score, "band": band})

    tot = sum(bands.values()) or 1
    print("\ntop-sibling similarity, over unmatched functions:")
    for k in BAND_ORDER:
        print(f"  {bands[k]:4}  ({100*bands[k]/tot:4.1f}%)  {k}")
    usable = bands["0.90+"] + bands["0.75-0.90"]
    print(f"\nfunctions with a twin at 0.75 or better: {usable} "
          f"({100*usable/tot:.1f}%)")
    if args.out:
        args.out.write_text(json.dumps({
            "pool": "ours" if args.ours else
                    "reference-history" if args.historical else "finished-reference",
            "bands": bands, "usable": usable, "errors": errors,
            "rows": results}, indent=1))
        print(f"wrote {args.out}")
    if rows and errors == len(rows):
        print("EXPERIMENT INVALID: every retrieval failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
