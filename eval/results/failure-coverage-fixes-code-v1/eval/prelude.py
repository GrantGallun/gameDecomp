"""Do our candidates' own typedef preludes model the wrong translation unit?

From the reference project's DECOMPILATION_LEARNINGS.md:

    Rebuild a per-function workspace baseline from the project's own headers,
    not from an archived scratch prelude. The same function body scored 95.749%
    against a decomp.me-style typedef prelude and 98.851% against the real
    project headers in the same workspace with the same compiler. A prelude
    that merely compiles still models a different translation unit, so every
    ranking taken against it is measuring the wrong program.

Our candidates routinely open with exactly that:

    typedef unsigned char u8;
    typedef short s16;
    typedef long s32;

while also including common.h, which already defines those. If the finding
transfers, every score we have recorded on such a candidate is measuring a
slightly different program than the one the build would produce.

This is NOT a claim that scores go up. It is a claim that they become truthful.
Both directions are reported, and prevalence is measured before either.

    python3 -m eval.prelude --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import buildtypes, workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=40.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)
    known = buildtypes.type_names(repo)
    print(f"build-provided type names: {len(known)}\n")

    rows = [r for r in conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " where a.compiled = 1 group by f.addr having best >= ?"
        " order by best desc", (args.floor,)).fetchall()
        if r[0] not in done]

    carries = up = down = same = broke = 0
    results = []
    for name, best in rows:
        src = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not src:
            continue
        stripped, removed = buildtypes.strip_redeclarations(src[0], known)
        if not removed:
            continue                       # no prelude to remove
        carries += 1
        # Removing the prelude is only half the move. These candidates do not
        # include common.h at all -- the prelude IS their type source -- so
        # stripping it alone leaves the types undefined and the build fails.
        # The learnings entry is about using the REAL project headers instead
        # of a scratch prelude, so put the header in as the prelude comes out.
        if '#include "common.h"' not in stripped:
            stripped = '#include "common.h"' + chr(10) + stripped

        ws = workspace.bootstrap(repo, name)
        base = workspace.score(ws, repo, name, src[0])
        cand = workspace.score(ws, repo, name, stripped)
        if not base.compiled:
            continue
        if not cand.compiled:
            broke += 1
            print(f"  {name[:44]:44} {base.score:7.3f} -> BROKE "
                  f"(removed {len(removed)})")
            continue
        d = cand.score - base.score
        tag = ("EXACT" if cand.exact else f"{cand.score:7.3f}")
        if cand.exact or d > 0.0005:
            up += 1
        elif d < -0.0005:
            down += 1
        else:
            same += 1
        if abs(d) > 0.0005 or cand.exact:
            print(f"  {name[:44]:44} {base.score:7.3f} -> {tag} "
                  f"({d:+.3f})  removed {sorted(set(removed))[:4]}")
        results.append({"function": name, "before": base.score,
                        "after": cand.score, "exact": cand.exact,
                        "removed": sorted(set(removed))})

    print(f"\ncandidates carrying a redundant prelude: {carries} of {len(rows)}")
    print(f"  score improved : {up}")
    print(f"  score fell     : {down}")
    print(f"  unchanged      : {same}")
    print(f"  stopped compiling: {broke}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
