"""Re-score the candidates the `do`-token ban refused before IDO ever saw them.

The ban lived in the matching helper (removed by `eval/remove_do_ban.py`). Its cost was invisible:
a refused candidate never reached the compiler, so it recorded a policy error instead of an object
comparison, and nothing counted it as a near miss. Measured on kb-sbk1.sqlite before the removal:

    845 attempts across 97 functions carry the refusal in `compiler_stderr`
     88 of those functions are still not exact

and the strategies are m2c and campaign-intake, not reference recovery -- the ban was refusing the
spelling m2c emits for a bottom-tested loop, which is also the spelling the reference project's own
ROM-verified source uses at 387 sites.

This compiles each such candidate AS WRITTEN, logs the attempt with its original provenance preserved
in the strategy string, and reports what the oracle says. Nothing is lowered first: that is the point
of the experiment. The candidate is still a proposal -- only the object comparison decides.

    python3 -m eval.do_ban_rerun --out eval/results/do-ban-rerun-<date> [--limit N] [--seconds S]
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

from solver import signals, workspace                                    # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")
REFUSAL = "%do-while loop%"
# A strategy that already declares itself reference-derived keeps that provenance, so the tier
# classifier still books it as recovered rather than as a solve.
RECOVERY_MARKERS = ("history-recovery", "historical-provenance", "symbol-restoration",
                    "historical-seed", "score_repo_function")


def candidates(conn, name: str, limit: int) -> list[tuple[int, str, str, float]]:
    return [(int(r[0]), r[1] or "", r[2] or "", float(r[3] or 0.0)) for r in conn.execute(
        "select a.id, a.source_code, a.strategy, a.score from attempts a "
        "join functions f on f.addr = a.func_addr "
        "where f.name = ? and a.compiler_stderr like ? and a.source_code is not null "
        "and length(a.source_code) > 0 order by a.score desc, a.id limit ?",
        (name, REFUSAL, limit))]


def run_one(conn, repo: Path, name: str, *, per_function: int) -> dict:
    row: dict = {"function": name}
    found = candidates(conn, name, per_function)
    if not found:
        return dict(row, status="no-refused-candidate")
    ws = workspace.bootstrap(repo, name)
    attempts = []
    for receipt, source, strategy, score in found:
        marker = ("recovered" if any(m in strategy for m in RECOVERY_MARKERS) else "capability")
        att = workspace.score(ws, repo, name, source, conn=conn, func=name,
                              strategy=f"do-ban-rerun:{strategy}", iteration=0,
                              run_kind="do-ban-rerun", parent_attempt_id=receipt,
                              relation="do-ban-rerun")
        profile = ({axis: int(getattr(signals.analyse(att.diff or "", att.score or 0.0,
                                                      bool(att.exact), bool(att.compiled)), axis))
                    for axis in AXES} if att.compiled else {})
        attempts.append({"receipt": receipt, "origin": strategy, "tier": marker,
                         "origin_score": score, "compiled": bool(att.compiled),
                         "exact": bool(att.exact), "score": att.score, "profile": profile})
        if att.exact:
            break
    row.update(attempts=attempts, compiled=any(a["compiled"] for a in attempts),
               exact=any(a["exact"] for a in attempts),
               tier=(attempts[0]["tier"] if attempts else None))
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--functions", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--per-function", type=int, default=2)
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
            "select distinct f.name from functions f join attempts a on a.func_addr = f.addr "
            "where a.compiler_stderr like ? and not exists "
            "(select 1 from attempts b where b.func_addr = f.addr and b.exact = 1) "
            "order by (select max(x.score) from attempts x where x.func_addr = f.addr "
            "and x.compiler_stderr like ?) desc", (REFUSAL, REFUSAL))]
    if args.limit:
        names = names[:args.limit]
    print(f"candidates: {len(names)} functions with a ban-refused, still-unmatched attempt", flush=True)

    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print("BUDGET reached", flush=True)
            break
        row = run_one(conn, args.repo, name, per_function=args.per_function)
        state[name] = row
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        first = (row.get("attempts") or [{}])[0]
        print(f"[{len(state)}/{len(names)}] {name:<44} compiled={row.get('compiled')} "
              f"exact={row.get('exact')} {first.get('origin_score')}->{first.get('score')} "
              f"{first.get('origin', '')[:40]}", flush=True)

    exact = sorted(n for n, r in state.items() if r.get("exact"))
    compiled = sorted(n for n, r in state.items() if r.get("compiled"))
    tiers = Counter(r.get("tier") for r in state.values() if r.get("exact"))
    summary = {"population": len(names), "recorded": len(state),
               "compiled": len(compiled), "exact": len(exact), "exact_functions": exact,
               "exact_by_tier": dict(tiers),
               "compiled_functions": compiled,
               "status_counts": dict(Counter(r.get("status") for r in state.values()))}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("exact_functions", "compiled_functions")}, indent=2))
    print("exact:", ", ".join(exact[:30]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
