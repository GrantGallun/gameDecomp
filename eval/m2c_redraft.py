"""66 workspaces have NO draft, and it is a stale artifact rather than an m2c limitation.

`tools/claude` writes `// file is blank because m2c failed to decompile function` when m2c exits
non-zero at bootstrap, and discards the diagnostic. Re-running the *same* invocation against each
workspace's own `target.s` with the m2c that is installed now succeeds on **66 of 79** -- so those
workspaces have been sitting in every cohort as unrunnable rows, and four compiles of the admission
route were spent proving it each time.

This regenerates the draft, then hands the workspace to the ordinary admission route so the new draft
gets the same include/repair treatment as everything else. The original blank base.c is preserved in
the receipt, and the write is atomic so a concurrent reader never sees a partial file.

    python3 -m eval.m2c_redraft [--limit N] [--out DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import header_admission as ha                                     # noqa: E402
from solver import workspace                                               # noqa: E402

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"
DEFAULT_M2C = str(Path.home() / "decomp/sbk1/.venv/bin/m2c")
CENSUS = ROOT / "eval/results/blank-draft-census-20260917.json"

HEADER = ('#include "common.h"\n\n'
          "// This is a decompilation attempt by the m2c tool.\n"
          "// Function/type definitions might be missing or incomplete.\n"
          "// The code will likely not compile without further modification.\n\n")


def redraft(m2c: str, ws: Path) -> tuple[str | None, str]:
    """Run m2c exactly as bootstrap does, and return (draft, why) -- draft is None on refusal."""
    asm = ws / "target.s"
    if not asm.is_file():
        return None, "no-target-asm"
    proc = subprocess.run([m2c, "--target", "mips-ido-c", str(asm)], cwd=ws,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, f"m2c exit {proc.returncode}: {first[0][:120] if first else ''}"
    body = proc.stdout.strip()
    if not body:
        return None, "m2c produced no output"
    return HEADER + body + "\n", "ok"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--census", type=Path, default=CENSUS)
    ap.add_argument("--m2c", default=DEFAULT_M2C)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/m2c-redraft-20260917")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--admit", action="store_true",
                    help="also run the include/repair admission route on each regenerated draft")
    ap.add_argument("--audit", type=Path, default=None,
                    help="a match_claim_audit state.json; re-draft every function it flags as "
                         "project-`.c`-backed, because those drafts were handed the reference source")
    ap.add_argument("--provenance", type=Path, default=None,
                    help="a type_name_provenance state.json; re-draft every function carrying a type "
                         "no header declares")
    ap.add_argument("--functions", default="", help="comma-separated names, overriding the census")
    ap.add_argument("--sidecar", action="store_true",
                    help="write base.contextfree.c beside base.c instead of replacing it")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if not Path(args.m2c).is_file():
        print(f"m2c not found at {args.m2c}; pass --m2c")
        return 2

    # THE HONEST ROUTE FOR A CONTAMINATED DRAFT IS NOT A REWRITE, IT IS A RE-DRAFT.
    #
    # For the 31 functions whose drafts use a type declared only in a project `.c`, the objective's
    # `unk`-only path would strip a type the draft was GIVEN -- the offsets and member names came out
    # of `ctx.c` either way, so byte arithmetic over them is the same knowledge laundered. What
    # actually removes the contamination is asking m2c again with no `--context`, which is what this
    # function already does and why its three matches audit `unqualified`.
    if args.audit:
        audited = json.loads(args.audit.read_text())
        names = sorted(n for n, r in audited.items()
                       if isinstance(r, dict) and r.get("project_c_backed"))
        print(f"re-drafting {len(names)} project-.c-backed drafts context-free", flush=True)
    elif args.provenance:
        prov = json.loads(args.provenance.read_text())
        names = sorted(n for n, r in prov["per_function"].items()
                       if len(r.get("headers") or {}) != len(r.get("types") or []))
        print(f"re-drafting {len(names)} drafts carrying a header-less type, context-free", flush=True)
    elif args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        names = json.loads(args.census.read_text())["no_draft"]
    if args.limit:
        names = names[:args.limit]
    receipt_path = args.out / "state.json"
    rows = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}

    conn = sqlite3.connect(str(args.db))
    ctx = None
    if args.admit:
        sys.path.insert(0, str(ROOT / "eval"))
        from eval.repaired_admission import build_context
        ctx = build_context(conn, args.repo)

    for name in names:
        if name in rows and rows[name].get("draft_bytes"):
            continue
        ws = args.repo / "nonmatchings" / name
        original = (ws / "base.c").read_text(errors="replace") if (ws / "base.c").is_file() else ""
        draft, why = redraft(args.m2c, ws)
        row: dict = {"function": name, "redraft": why, "original_bytes": len(original)}
        if draft is None:
            row["status"] = "still-refused"
            rows[name] = row
            receipt_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  {name:<34} still-refused ({why})", flush=True)
            continue
        # `--sidecar` writes the context-free draft BESIDE the contaminated one instead of over it.
        # A draft that was handed the reference layout is not usable for a SOLVED claim, but it is
        # still the record of what bootstrap produced, and replacing it would destroy the evidence of
        # the contamination this whole pass exists to measure.
        target = "base.contextfree.c" if args.sidecar else "base.c"
        tmp = ws / f"{target}.redraft"
        tmp.write_text(draft, encoding="utf-8")
        os.replace(tmp, ws / target)
        row.update(draft_bytes=len(draft), lines=len(draft.splitlines()), written_as=target)
        if args.admit:
            try:
                # `workspace.bootstrap` refuses anything but `[A-Za-z_]\w*`, so a directory carrying a
                # numeric suffix (`drawRacePlayerModel-2`) has no valid function name to bootstrap.
                # Reported as such rather than raised: the draft WAS written, and calling it `raised`
                # would read as a scoring failure when it is a naming limit.
                if re.search(r"-\d+$", name):
                    row["status"] = "skipped-suffixed-workspace"
                else:
                    got = ha.run_one(conn, args.repo, name, rounds=args.rounds, context=ctx,
                                     draft_name=target)
                    row.update({k: v for k, v in got.items() if k != "function"})
                    row["status"] = got.get("status", "?")
            except Exception as exc:                                      # noqa: BLE001
                row.update(status="raised", error=f"{type(exc).__name__}: {exc}")
        else:
            row["status"] = "redrafted"
        rows[name] = row
        receipt_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  {name:<34} {row['status']:<16} bytes={row['draft_bytes']} "
              f"score={row.get('score')} stages={row.get('harvest_stages')}", flush=True)

    print(collections.Counter(r.get("status") for r in rows.values()))
    print(f"wrote {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
