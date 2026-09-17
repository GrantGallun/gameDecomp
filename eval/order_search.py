"""Run the ordering pass over the functions the census says are reordered-only.

`patterns/catalog.py` `ordering-residual-states-the-statement-order` records that
`rewrites.statement_order_rewrites` yields SINGLE ADJACENT SWAPS from the baseline, while the
permutation that closes these functions can be a COMPOSITION of several swaps -- so depth 1 from the
baseline finding nothing is consistent with the class being reachable. The pass's own docstring warns
that the byte score can FALL even when the permutation is right, so this never ranks by score: it
enumerates `order:*` variants to a bounded depth and lets `att.exact` decide.

Population: the functions `eval/rename_census.py` measured as `reordered-only` (the residual is a
permutation of identical instruction shapes, so the register gradient is counting an order difference)
plus anything passed with `--functions`.

    python3 -m eval.order_search --out eval/results/order-search-<date> [--depth 2] [--cap N] [--jobs N]

Every compile is logged to the knowledge base with `strategy = "order-search:<label>"`.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.close_nearmiss import best_compiled_source                        # noqa: E402
from solver import rewrites, workspace                                      # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")


def best_diff(conn, name: str) -> str:
    """The diff that belongs to `best_compiled_source`. Source and diff must come from ONE attempt."""
    row = conn.execute(
        "select a.diff_summary from attempts a join functions f on f.addr = a.func_addr "
        "where f.name = ? and a.compiled = 1 and a.source_code is not null "
        "order by a.score desc limit 1", (name,)).fetchone()
    return (row[0] or "") if row else ""


def search_one(conn, repo: Path, name: str, *, depth: int, cap: int) -> dict:
    row: dict = {"function": name, "status": "ok"}
    found = best_compiled_source(conn, name)
    if not found:
        return dict(row, status="no-compiling-candidate")
    receipt, source, score = found
    diff = best_diff(conn, name)
    row.update(source_attempt_id=receipt, start_score=score)
    ws = workspace.bootstrap(repo, name)

    def compile_candidate(candidate: str, label: str):
        return workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                               strategy=f"order-search:{label}", iteration=0,
                               run_kind="order-search")

    base = compile_candidate(source, "baseline")
    row["baseline_compiled"] = bool(base.compiled)
    if base.exact:
        row["status"] = "already-exact"
        return row
    seen = {source}
    compiles = 0
    frontier = [(source, diff)]
    best_label, best_score = "baseline", score
    for level in range(1, depth + 1):
        nxt = []
        for parent_source, parent_diff in frontier:
            for rewrite in rewrites.statement_order_rewrites(parent_source, parent_diff, gate=False):
                candidate = rewrite(parent_source)
                if candidate in seen:
                    continue
                seen.add(candidate)
                if compiles >= cap:
                    break
                label = f"d{level}:{rewrite.label}"
                att = compile_candidate(candidate, label)
                compiles += 1
                if att.compiled and att.score and att.score > best_score:
                    best_label, best_score = label, att.score
                if att.exact:
                    row.update(exact=True, exact_source=candidate, best_label=label,
                               compiles=compiles, status="exact")
                    return row
                nxt.append((candidate, att.diff or ""))
            if compiles >= cap:
                break
        frontier = nxt
        if not frontier or compiles >= cap:
            break
    row.update(compiles=compiles, exact=False, best_label=best_label, best_score=best_score,
               variants=len(seen) - 1)
    return row


_WORKER = None
_WORKER_REPO = None


def _init(db: str, repo: str) -> None:
    global _WORKER, _WORKER_REPO
    _WORKER = sqlite3.connect(db, timeout=120)
    _WORKER_REPO = Path(repo)


def _run(name: str, opts: dict) -> dict:
    return search_one(_WORKER, _WORKER_REPO, name, **opts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--functions", default="",
                    help="comma-separated; default is the census's reordered-only set")
    ap.add_argument("--census", type=Path, default=None,
                    help="a rename_census state.json to take the population from")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--cap", type=int, default=80)
    ap.add_argument("--seconds", type=float, default=0.0)
    ap.add_argument("--jobs", type=int, default=1)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}

    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        census = args.census or (ROOT / "eval/results/rename-census-20260917/state.json")
        rows = json.loads(Path(census).read_text())
        names = sorted(n for n, r in rows.items()
                       if r.get("pairing") == "reordered-only" and not r.get("exact"))
        print(f"population from {census}: {len(names)} reordered-only", flush=True)
    print(f"candidates: {len(names)}", flush=True)

    started = time.time()
    opts = dict(depth=args.depth, cap=args.cap)
    if args.jobs <= 1:
        conn = sqlite3.connect(args.db, timeout=120)
        for name in names:
            if name in state:
                continue
            if args.seconds and (time.time() - started) > args.seconds:
                print("BUDGET reached", flush=True)
                break
            state[name] = search_one(conn, args.repo, name, **opts)
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            print(f"[{len(state)}/{len(names)}] {name:<46} exact={state[name].get('exact')} "
                  f"compiles={state[name].get('compiles')} {state[name].get('status')}", flush=True)
    else:
        pending = [n for n in names if n not in state]
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx, initializer=_init,
                                initargs=(args.db, str(args.repo))) as pool:
            futures = {pool.submit(_run, n, opts): n for n in pending}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    state[name] = future.result()
                except Exception as exc:                                    # noqa: BLE001
                    state[name] = {"function": name, "status": "worker-raised",
                                   "error": f"{type(exc).__name__}: {exc}"}
                state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
                print(f"[{len(state)}/{len(names)}] {name:<46} exact={state[name].get('exact')} "
                      f"compiles={state[name].get('compiles')} {state[name].get('status')}", flush=True)

    exact = sorted(n for n, r in state.items() if r.get("exact"))
    summary = {"population": len(names), "recorded": len(state), "exact": len(exact),
               "exact_functions": exact,
               "compiles": sum(r.get("compiles") or 0 for r in state.values()),
               "status_counts": dict(Counter(r.get("status") for r in state.values())),
               "best_label_counts": dict(Counter(r.get("best_label") for r in state.values()))}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
