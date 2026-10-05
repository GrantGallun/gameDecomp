"""Does resolving m2c's `?` placeholder actually admit the 122 drafts that carry one?

The class is measured (122 drafts, 1,962 attempts with `Empty declaration specifiers`), the rewrite is
pure and testable (`solver.m2c_placeholders`), and the remaining question is empirical: does the file
compile once the placeholder is gone, or does the error merely move to the next line? Both answers are
useful -- the first is a new supply of candidates, the second says the class was never the blocker.

Every compile is logged with its strategy, and the receipt keeps the score, the placeholder count and
the residual error so the next step is chosen from the data rather than from the first example.

    python3 -m eval.placeholder_resolution_sweep [--limit N] [--variants]
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
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/placeholder-sweep-20260917")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--variants", action="store_true",
                    help="on a non-compiling result, vary one placeholder over the fallback set")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    receipt = args.out / "state.json"
    rows = json.loads(receipt.read_text()) if receipt.is_file() else {}

    census = json.loads(args.census.read_text())["placeholder_rows"]
    names = sorted(census)
    if args.limit:
        names = names[:args.limit]
    print(f"sweeping {len(names)} placeholder-bearing drafts", flush=True)

    conn = sqlite3.connect(str(args.db))
    for name in names:
        if name in rows and rows[name].get("status") not in {"raised", None}:
            continue
        ws = workspace.bootstrap(args.repo, name)
        row: dict = {"function": name, "census_placeholders": census[name]["placeholders"]}
        try:
            draft = (ws / "base.c").read_text(errors="replace")
            fixed, resolved = mp.rewrite(draft)
            row.update(resolved=resolved[:8], changed=fixed != draft)
            if fixed == draft:
                row["status"] = "declined-no-placeholder"
            else:
                att = workspace.score(ws, args.repo, name, fixed, conn=conn, func=name,
                                      strategy="placeholder-resolution", iteration=0,
                                      run_kind="placeholder-resolution")
                row.update(compiled=bool(att.compiled), exact=bool(att.exact), score=att.score)
                if att.exact:
                    row["status"] = "EXACT"
                elif att.compiled:
                    row["status"] = "compiled"
                else:
                    row["status"] = "still-not-compiling"
                    row["error"] = (att.compiler_stderr or "").strip().splitlines()[:2]
                    # The line numbers refer to a file the harness names `candidate.c`, which is not
                    # the draft on disk, so keep both the source we handed over and the whole stderr.
                    # Guessing at the offset between them is how a one-line fix gets misdiagnosed.
                    (args.out / f"{name}.c").write_text(fixed, encoding="utf-8")
                    (args.out / f"{name}.err").write_text(att.compiler_stderr or "", encoding="utf-8")
                    if args.variants:
                        for label, trial in mp.variants(fixed, limit=2):
                            v = workspace.score(ws, args.repo, name, trial, conn=conn, func=name,
                                                strategy=f"placeholder-resolution:{label}", iteration=0,
                                                run_kind="placeholder-resolution")
                            row.setdefault("variants", {})[label] = {"compiled": bool(v.compiled),
                                                                    "score": v.score}
                            if v.compiled:
                                row.update(status="compiled-by-variant" if not v.exact else "EXACT",
                                           variant=label, score=v.score)
                                break
        except Exception as exc:                                          # noqa: BLE001
            row.update(status="raised", error=f"{type(exc).__name__}: {exc}")
        rows[name] = row
        receipt.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  {name:<34} {row['status']:<24} ph={row['census_placeholders']} "
              f"score={row.get('score')} {row.get('variant') or ''}", flush=True)

    print(collections.Counter(r.get("status") for r in rows.values()))
    print(f"wrote {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
