"""m2c writes `?` where it cannot infer a type, and IDO refuses the whole translation unit for it.

Measured on `_Litob`: the regenerated draft is 132 lines and dies at line 12 with
`Syntax Error` + `Empty declaration specifiers`. Line 12 is `? lldiv(s32 *, s32, s32);` -- m2c's
placeholder for an unknown return type. ONE unresolved prototype blocks the entire file, so this is
not a hard codegen problem: it is a lexical defect with a small, enumerable set of plausible fixes
(`s32`, `void`, `u32`, `f32`), each of which the object can adjudicate.

The admission taxonomy already showed this class (`Syntax Error` + `Empty declaration specifiers` was
21 of 65 terminal failures) and it was read as "the draft does not parse", which is true but not
actionable. This measures how much of the corpus is behind a `?` so the repair can be sized before it
is written.

    python3 -m eval.m2c_placeholder_census [--db PATH] [--repo PATH]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"

# A `?` that cfe will read as a declaration specifier: line-initial, or after `extern `.
DECL = re.compile(r"^\s*(?:extern\s+)?\?\s*[A-Za-z_]", re.M)
UNKNOWN_LINE = re.compile(r"^\s*(?:extern\s+)?\?", re.M)
PLACEHOLDER_ERR = re.compile(r"Empty declaration specifiers", re.I)


def scan_tree(root: Path) -> dict:
    drafts, blocked = {}, []
    for d in sorted(root.iterdir()):
        base = d / "base.c"
        if not base.is_file():
            continue
        text = base.read_text(errors="replace")
        hits = UNKNOWN_LINE.findall(text)
        if hits:
            drafts[d.name] = {"placeholders": len(hits), "bytes": len(text)}
            blocked.append(d.name)
    return {"with_placeholder": len(blocked), "examples": blocked[:12], "rows": drafts}


def scan_attempts(conn: sqlite3.Connection) -> dict:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attempts)")}
    if "source" not in cols:
        return {"note": f"no source column; columns={sorted(cols)}"}
    out = collections.Counter()
    placeholders = 0
    for (src, strategy) in conn.execute("SELECT source, strategy FROM attempts"):
        if not src:
            continue
        if UNKNOWN_LINE.search(src):
            placeholders += 1
            out[strategy or "?"] += 1
    return {"attempts_with_placeholder": placeholders,
            "by_strategy_top": out.most_common(12)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/m2c-placeholder-census-20260917.json")
    args = ap.parse_args(argv)

    tree = scan_tree(args.repo / "nonmatchings")
    conn = sqlite3.connect(str(args.db))
    attempts = scan_attempts(conn)
    error_rows = 0
    for table in ("attempts",):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        text_cols = [c for c in ("error", "compiler_stderr", "stderr", "diagnostic") if c in cols]
        if not text_cols:
            continue
        for col in text_cols:
            error_rows += sum(1 for (t,) in conn.execute(f"SELECT {col} FROM {table}")
                              if t and PLACEHOLDER_ERR.search(t))
    report = {"drafts_blocked_by_placeholder": tree["with_placeholder"],
              "examples": tree["examples"], "placeholder_rows": tree["rows"],
              "attempts": attempts,
              "attempt_error_rows_with_placeholder_message": error_rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "placeholder_rows"}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
