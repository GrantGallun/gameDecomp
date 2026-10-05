"""Compose the two admissions that were measured separately and never together.

Measured today, in order:

  1. `header_admission` (declaring header, then `repair_chain`): 112 of 1633 drafts admitted, and its
     four HIGHEST scorers -- the most favourable sample in the run -- convert to 0 exact under the
     deterministic repair rungs. Admission buys scoreability, not matches.
  2. `m2c_placeholders.rewrite`: 122 drafts are refused for m2c's `?` type placeholder, and the
     placeholder is real -- `_Litob` dies at `? lldiv(...)` and nothing after it is judged. But
     rewriting it does NOT admit them either: the next errors are undeclared parameter types
     (`__osPackId *temp` parses as garbage because the type is unknown), which is exactly what stage 1
     owns.

The two failures are sequential on the same file, so the composition is the thing to measure: resolve
the placeholders so the file PARSES, then run the header/repair route so it TYPECHECKS. Neither was
given the other's output.

`base.c` is preserved as `base.m2c.c` before the resolved draft replaces it, so the record of what m2c
actually emitted survives, and the receipt names every function whose draft was rewritten.

    python3 -m eval.placeholder_admission [--limit N] [--rounds 3]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import header_admission as ha                                     # noqa: E402
from eval.repaired_admission import build_context                           # noqa: E402
from solver import m2c_placeholders as mp                                   # noqa: E402
from solver import workspace                                               # noqa: E402

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"
CENSUS = ROOT / "eval/results/m2c-placeholder-census-20260917.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--census", type=Path, default=CENSUS)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/placeholder-admission-20260917")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--no-write", action="store_true",
                    help="rewrite in memory only; leaves base.c alone (diagnostic mode)")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    receipt = args.out / "state.json"
    rows = json.loads(receipt.read_text()) if receipt.is_file() else {}

    names = sorted(json.loads(args.census.read_text())["placeholder_rows"])
    if args.limit:
        names = names[:args.limit]
    print(f"composing placeholder + admission on {len(names)} drafts", flush=True)

    conn = sqlite3.connect(str(args.db))
    ctx = build_context(conn, args.repo)
    for name in names:
        if name in rows and rows[name].get("status") not in {None, "raised"}:
            continue
        ws = workspace.bootstrap(args.repo, name)
        row: dict = {"function": name}
        try:
            original = (ws / "base.c").read_text(errors="replace")
            fixed, resolved = mp.rewrite(original)
            row.update(resolved=resolved[:10], rewrite_fired=fixed != original)
            if fixed == original:
                row["status"] = "no-placeholder-found"
            else:
                if not args.no_write:
                    backup = ws / "base.m2c.c"
                    if not backup.is_file():
                        backup.write_text(original, encoding="utf-8")
                    tmp = ws / "base.c.resolved"
                    tmp.write_text(fixed, encoding="utf-8")
                    os.replace(tmp, ws / "base.c")
                got = ha.run_one(conn, args.repo, name, rounds=args.rounds, context=ctx)
                row.update({k: v for k, v in got.items() if k != "function"})
                row["status"] = got.get("status", "?")
        except Exception as exc:                                          # noqa: BLE001
            row.update(status="raised", error=f"{type(exc).__name__}: {exc}")
        rows[name] = row
        receipt.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  {name:<34} {row['status']:<16} ph={len(row.get('resolved') or [])} "
              f"score={row.get('score')} stages={row.get('harvest_stages')}", flush=True)

    print(collections.Counter(r.get("status") for r in rows.values()))
    print(f"wrote {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
