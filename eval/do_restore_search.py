"""Offer each candidate its `do { ... } while (...)` spelling back, and let the oracle decide.

`solver.rewrites.do_while_restore_rewrites` inverts the lowering the removed ban used to force. The
lowering is not codegen-neutral -- on drawRaceSplitscreenSelectOption2Frame the ROM-verified body with
`do` compiles byte-exact and the lowered form of that same body scores 99.395 -- so a candidate that
was lowered may be one edit away from exact, in a direction no other generator proposes.

The population is the functions whose ROM-verified source contains `do` (`eval/do_while_population.py`:
236 functions, of which 54 are live residue in the knowledge base). For each, the best COMPILING
non-exact attempt is taken and each `for(;;)+break` site is restored on its own. Everything is logged;
the object comparison is the only acceptance.

    python3 -m eval.do_restore_search --out eval/results/do-restore-<date> [--functions a,b] [--seconds S]
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

from solver import rewrites, signals, workspace                              # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")
RECOVERY_MARKERS = ("history-recovery", "historical-provenance", "symbol-restoration",
                    "historical-seed", "score_repo_function")


def best_attempt(conn, name: str):
    row = conn.execute(
        "select a.id, a.source_code, a.strategy, a.score from attempts a "
        "join functions f on f.addr = a.func_addr "
        "where f.name = ? and a.compiled = 1 and coalesce(a.exact, 0) = 0 "
        "order by a.score desc, a.id limit 1", (name,)).fetchone()
    return (int(row[0]), row[1] or "", row[2] or "", float(row[3] or 0.0)) if row else None


def run_one(conn, repo: Path, name: str) -> dict:
    row: dict = {"function": name}
    found = best_attempt(conn, name)
    if not found:
        return dict(row, status="no-compiling-candidate")
    receipt, source, strategy, score = found
    variants = rewrites.do_while_restore_rewrites(source, "")
    row.update(source_attempt_id=receipt, origin=strategy, start_score=score,
               tier=("recovered" if any(m in strategy for m in RECOVERY_MARKERS) else "capability"),
               sites=len(variants))
    if not variants:
        return dict(row, status="no-restorable-site")
    ws = workspace.bootstrap(repo, name)
    try:
        target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")
    except OSError:
        target = ""
    attempts = []
    for variant in variants:
        candidate = variant(source)
        att = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                              strategy=f"do-restore:{variant.label}", iteration=0,
                              run_kind="do-restore", parent_attempt_id=receipt,
                              relation="do-restore")
        profile = ({a: int(getattr(signals.analyse(att.diff or "", att.score or 0.0,
                                                   bool(att.exact), bool(att.compiled)), a))
                    for a in AXES} if att.compiled else {})
        attempts.append({"label": variant.label, "compiled": bool(att.compiled),
                         "exact": bool(att.exact), "score": att.score, "profile": profile})
        if att.exact:
            break
    row.update(attempts=attempts, compiled=any(a["compiled"] for a in attempts),
               exact=any(a["exact"] for a in attempts),
               best_score=max((a["score"] or 0.0) for a in attempts) if attempts else None)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--functions", default="")
    ap.add_argument("--population", type=Path,
                    default=ROOT / "eval/results/do-while-population-20260917.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}

    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        names = json.loads(Path(args.population).read_text())["live"]
    if args.limit:
        names = names[:args.limit]
    print(f"population: {len(names)} live functions whose key contains `do`", flush=True)

    conn = sqlite3.connect(args.db, timeout=120)
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print("BUDGET reached", flush=True)
            break
        state[name] = run_one(conn, args.repo, name)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        row = state[name]
        print(f"[{len(state)}/{len(names)}] {name:<44} sites={row.get('sites')} "
              f"compiled={row.get('compiled')} exact={row.get('exact')} "
              f"start={row.get('start_score')} best={row.get('best_score')} {row.get('status', '')}",
              flush=True)

    exact = sorted(n for n, r in state.items() if r.get("exact"))
    summary = {"population": len(names), "recorded": len(state),
               "exact": len(exact), "exact_functions": exact,
               "exact_by_tier": dict(Counter(r.get("tier") for r in state.values() if r.get("exact"))),
               "compiled": sum(1 for r in state.values() if r.get("compiled")),
               "improved": sum(1 for r in state.values()
                               if (r.get("best_score") or 0) > (r.get("start_score") or 0)),
               "status_counts": dict(Counter(r.get("status") for r in state.values() if r.get("status"))),
               "sites": sum(r.get("sites") or 0 for r in state.values())}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "exact_functions"}, indent=2))
    print("exact:", ", ".join(exact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
