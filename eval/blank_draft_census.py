"""How many workspaces have NO draft at all, and why.

The header-admission run reported 15 functions failing with "Compiled object has no text symbols".
That message looks like a recipe bug. It is not: `base.c` for those is a 79-byte file containing an
include and the comment "file is blank because m2c failed to decompile function". There is no
hypothesis to repair, so no include and no harvest stage can ever admit them -- and every one of them
costs four compiles before the run finds that out.

This separates the strata so the next run can route them instead of compiling them:

    no-draft      base.c has no statements: m2c produced nothing, and the fix is a model call
    unparseable   base.c has statements but cfe rejects them even after the includes resolve

    python3 -m eval.blank_draft_census [--repo PATH] [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = Path.home() / "decomp/sbk1"
SKIP = ("#include", "//", "/*", "*", "*/", "#")


def statements(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith(SKIP)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/blank-draft-census-20260917.json")
    args = ap.parse_args(argv)

    root = args.repo / "nonmatchings"
    rows, m2c_failed, no_file = {}, [], []
    for d in sorted(root.iterdir()):
        base = d / "base.c"
        if not (d / "target.s").is_file():
            continue
        if not base.is_file():
            no_file.append(d.name)
            continue
        text = base.read_text(errors="replace")
        body = statements(text)
        rows[d.name] = {"bytes": len(text), "statements": len(body),
                        "m2c_failed_marker": "m2c failed" in text}
        if not body:
            m2c_failed.append(d.name)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"total": len(rows), "no_draft": m2c_failed,
                                    "no_base_file": no_file, "rows": rows},
                                   indent=2, sort_keys=True), encoding="utf-8")
    sizes = collections.Counter(min(r["statements"] // 5, 20) for r in rows.values())
    print(f"workspaces with target.s: {len(rows)}")
    print(f"  no base.c at all       : {len(no_file)}")
    print(f"  no-draft (no statements): {len(m2c_failed)}  "
          f"({100.0 * len(m2c_failed) / max(len(rows), 1):.1f}%)")
    print(f"  of those, carrying the literal 'm2c failed' marker: "
          f"{sum(1 for n in m2c_failed if rows[n]['m2c_failed_marker'])}")
    print("  statement-count buckets (x5, capped at 100+):",
          dict(sorted(sizes.items())))
    print(f"wrote {args.out}")
    print("sample no-draft:", m2c_failed[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
