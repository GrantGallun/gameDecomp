"""Where does a draft's type name come from, and does the answer route it to a header or to an `unk`?

The objective's specific block: `typedecl.plan` declines on `PlayerCommandState *var_s0;` -- the name is
found, the pool has the type with 60 offsets, `members_used` returns one name, and
`typepool.named_fields` returns None, after which the locals-only path `continue`s rather than place
one member among sixty offsets by source order. That refusal is correct and stays.

So the question is not how to make the refusal smarter, it is WHERE THE NAME CAME FROM. Measured
mechanically, 2026-09-17:

  `PlayerCommandState` IS declared, in `include/game/audio/audio_engine.h`, together with its
  prototype (`u8 *Fstop(PlayerCommandState *arg0, u8 *arg1);`).

and the reason m2c knew the name at all is that `tools/claude` runs `m2ctx.py` on the function's own
project source file before invoking m2c, so `--context ctx.c` hands m2c the real headers. The name is
not an invention and not a leak: it is the project's own declaration, reachable by `#include`.

That makes the routing decision a measurement rather than a judgement: for each unattributable name,
either a header declares it (header route) or nothing in the repository does (the name is then
unattributable from binary evidence, and the honest route is an `unk`-only declaration plus a rewrite
of the unresolved uses). This counts which, over the population that matters.

    python3 -m eval.type_name_provenance [--limit N] [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import header_admission as ha                                     # noqa: E402

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"


def statements(text: str) -> list[str]:
    skip = ("#include", "//", "/*", "*", "*/", "#")
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(skip)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/type-name-provenance-20260917.json")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    # `attempts` has no function-name column: identity is `func_addr`, joined to `functions.name`.
    attempted = {r[0] for r in conn.execute(
        "SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr = a.func_addr")}
    exact = {r[0] for r in conn.execute(
        "SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr = a.func_addr "
        "WHERE a.exact = 1")} if _has(conn, "attempts", "exact") else set()

    drafts, no_draft, compiling = {}, [], []
    for d in sorted((args.repo / "nonmatchings").iterdir()):
        if not (d / "target.s").is_file():
            continue
        name = d.name
        base = d / "base.c"
        if not base.is_file() or not statements(base.read_text(errors="replace")):
            no_draft.append(name)
            continue
        if name in attempted:
            continue
        drafts[name] = base.read_text(errors="replace")

    resolved, unattributable = collections.Counter(), collections.Counter()
    per_function, header_for = {}, {}
    for name, draft in drafts.items():
        wanted = ha.missing_types(draft)
        found = {}
        for t in wanted:
            h = header_for.get(t) if t in header_for else ha.declaring_header(args.repo, t)
            header_for[t] = h
            if h:
                resolved[t] += 1
                found[t] = h
            else:
                unattributable[t] += 1
        per_function[name] = {"types": wanted, "headers": found,
                              "all_resolved": bool(wanted) and len(found) == len(wanted),
                              "no_types": not wanted}

    report = {
        "never_attempted_with_statements": len(drafts),
        "never_attempted_no_draft": len(no_draft),
        "distinct_type_names": len(header_for),
        "types_with_a_declaring_header": len(resolved),
        "types_with_no_declaring_header": len(unattributable),
        "functions_all_types_resolved": sum(1 for v in per_function.values() if v["all_resolved"]),
        "functions_with_no_missing_types": sum(1 for v in per_function.values() if v["no_types"]),
        "functions_with_an_unattributable_type": sum(
            1 for v in per_function.values() if len(v["headers"]) != len(v["types"])),
        "top_unattributable": unattributable.most_common(25),
        "top_resolved": resolved.most_common(25),
        "per_function": per_function,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_function"}, indent=2))
    print(f"attempted functions already in the log: {len(attempted)}, exact: {len(exact)}")
    print(f"wrote {args.out}")
    return 0


def _has(conn, table: str, column: str) -> bool:
    return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


if __name__ == "__main__":
    raise SystemExit(main())
