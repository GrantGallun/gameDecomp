"""How much of the emitting population's residual is a reordering misread as a register rename?

`solver.regalloc_signature.compare` aligns on register-free shape, so two same-shape instructions
whose registers differ are scored as register faults even when the two dumps hold the SAME
instructions in a different order. That ambiguity is decidable arithmetically: within one
shape-equal aligned block, `reordered = common - same_position` and `renames = length - common`,
and `reordered + renames == register_instructions`.

This census recompiles each function's best compiling attempt once, runs the same comparison the
search ranks on, and reports the split beside `signals.analyse`'s six axes. It is a measurement, not
a router: nothing here changes how a candidate is scored or which pass runs.

    python3 -m eval.rename_census --out eval/results/rename-census-<date> [--seconds N] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.close_nearmiss import AXES, best_compiled_source, profile_of      # noqa: E402
from solver import regalloc_signature, workspace                            # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")


def classify(report) -> str:
    """One label per residual, the split being the point of the census."""
    if report.exact_shape:
        return "no-register-difference"
    if report.renames == 0:
        return "reordered-only"
    if report.reordered == 0:
        return "renamed-only"
    return "both"


def measure_one(conn, repo: Path, name: str) -> dict:
    row: dict = {"function": name}
    found = best_compiled_source(conn, name)
    if not found:
        return dict(row, status="no-compiling-candidate")
    receipt, source, score = found
    ws = workspace.bootstrap(repo, name)
    att = workspace.score(ws, repo, name, source, conn=None)
    row.update(source_attempt_id=receipt, start_score=score, compiled=bool(att.compiled),
               exact=bool(att.exact))
    if not att.compiled:
        return dict(row, status="recompile-failed")
    dump_path = ws / f"{name}_object_dump_normalized.s"
    if not dump_path.exists():
        return dict(row, status="no-dump")
    report = regalloc_signature.compare((ws / "target_object_dump_normalized.s").read_text(errors="replace"),
                                        dump_path.read_text(errors="replace"))
    profile = profile_of(att.diff or "", score, bool(att.exact), True)
    row.update(status="ok", gradient=list(report.gradient), reordered=report.reordered,
               renames=report.renames, order_only=report.order_only, pairing=classify(report),
               signatures=dict(report.signatures), rename_signatures=dict(report.rename_signatures),
               profile=profile,
               dominant=max(profile, key=lambda axis: profile[axis]) if any(profile.values()) else "clean")
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--functions", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}

    conn = sqlite3.connect(args.db, timeout=120)
    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        names = [r[0] for r in conn.execute(
            "select f.name from functions f where exists "
            "(select 1 from attempts a where a.func_addr = f.addr and a.compiled = 1) "
            "and not exists (select 1 from attempts a where a.func_addr = f.addr and a.exact = 1) "
            "order by (select max(a.score) from attempts a where a.func_addr = f.addr "
            "and a.compiled = 1) desc")]
    if args.limit:
        names = names[:args.limit]
    print(f"population: {len(names)}  recorded: {sum(1 for n in names if n in state)}", flush=True)
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print(f"BUDGET: {args.seconds}s reached", flush=True)
            break
        row = measure_one(conn, args.repo, name)
        state[name] = row
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{len(state)}/{len(names)}] {name:<46} {row.get('status'):<22} "
              f"grad={row.get('gradient')} reordered={row.get('reordered')} "
              f"renames={row.get('renames')} {row.get('pairing', '')}", flush=True)

    rows = [r for r in state.values() if r.get("status") == "ok"]
    pairing = Counter(r["pairing"] for r in rows)
    by_dominant = Counter((r.get("dominant"), r["pairing"]) for r in rows)
    summary = {
        "population": len(names),
        "recorded": len(state),
        "measured": len(rows),
        "pairing_counts": dict(pairing),
        "pairing_of_register_dominant": {k: v for k, v in
                                         ((p, sum(1 for r in rows if r.get("dominant") == "regalloc"
                                                  and r["pairing"] == p))
                                          for p in sorted(pairing))},
        "dominant_by_pairing": {f"{d}|{p}": n for (d, p), n in sorted(by_dominant.items())},
        "reordered_only_functions": sorted(r["function"] for r in rows if r["pairing"] == "reordered-only"),
        "renamed_only_functions": sorted(r["function"] for r in rows if r["pairing"] == "renamed-only"),
        "axes_of_reordered_only": {a: sum(r["profile"][a] for r in rows if r["pairing"] == "reordered-only")
                                   for a in AXES},
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_functions")}, indent=2))
    print("reordered-only:", ", ".join(summary["reordered_only_functions"][:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
