"""Do the enumerable admission blockers still block? The undefined-symbol class, measured.

`eval/results/admission-20260916/RESULT.md` measured the undefined-identifier failures as the most
tractable item in the bucket because they are enumerable: "1,196 occurrences across 44 functions...
each a missing declaration that is simply absent". Item 4 of its recommended order was to work that
list down.

The deterministic answer already exists -- `solver/compile_chain`'s undeclared-identifier rung
declares what IDO reports as undefined -- but it was never run against this population on its own.
`solver/workspace.py:470` is the reason to measure rather than assume: a fix that fires and a fix that
declines look identical from outside.

This takes the stored source of every function whose failures include an undefined identifier, runs
the ladder, and reports what the ORACLE says. No model, no GPU. Every compile is logged.

A SIDE EFFECT WORTH KNOWING, because it changes what the numbers mean. `eval/status.py` reports that
"(1,297 historical compiled attempt(s) predate persisted exact verdicts. They are treated as unknown,
never inferred from score.)" Recompiling a stored source here does not infer anything -- it asks the
oracle -- and 53 of the 171 baselines came back `exact=True`. So this run converted 53 functions from
UNKNOWN to VERIFIED byte-exact in the ledger.

That is bookkeeping, not capability: those sources were already byte-exact, and `eval/status`'s
`on_disk` set already counted most of them, which is why the headline moved by 2 rather than 53. Both
numbers are in the result file; report the second one as a correction to the ledger, never as new
decompilation. All 53 were re-verified independently afterwards by recompiling again in a fresh call.
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

from solver import compile_chain, workspace                           # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")
UNDEFINED = re.compile(r"'([A-Za-z_]\w*)' undefined")


def undefined_names(stderr: str) -> set[str]:
    return set(UNDEFINED.findall(stderr or ""))


def candidate_functions(conn) -> list[dict]:
    """Functions whose failed attempts report at least one undefined identifier."""
    per: dict[str, dict] = {}
    for addr, name, stderr in conn.execute(
            "select a.func_addr, f.name, a.compiler_stderr from attempts a "
            "join functions f on f.addr = a.func_addr "
            "where a.compiled = 0 and a.compiler_stderr is not null"):
        names = undefined_names(stderr)
        if not names:
            continue
        row = per.setdefault(name, {"function": name, "addr": addr, "symbols": set()})
        row["symbols"] |= names
    return sorted(per.values(), key=lambda r: -len(r["symbols"]))


def best_source(conn, addr: int) -> str | None:
    row = conn.execute(
        "select source_code from attempts where func_addr = ? and source_code is not null "
        "and length(source_code) > 0 order by id desc limit 1", (addr,)).fetchone()
    return row[0] if row else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=Path(DEFAULT_REPO))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=int, default=10)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db, timeout=120)
    population = candidate_functions(conn)
    if args.limit:
        population = population[: args.limit]
    print("functions whose failures name an undefined identifier: %d" % len(population), flush=True)

    rows = []
    for i, item in enumerate(population, 1):
        name, addr = item["function"], item["addr"]
        row = {"function": name, "symbols": sorted(item["symbols"])}
        source = best_source(conn, addr)
        if source is None:
            row["status"] = "no-stored-source"
            rows.append(row)
            continue
        try:
            ws = workspace.bootstrap(args.repo, name)
        except Exception as exc:                                      # noqa: BLE001
            row["status"] = f"bootstrap-failed: {type(exc).__name__}"
            rows.append(row)
            continue

        def score_child(label: str, code: str):
            att = workspace.score(ws, args.repo, name, code, conn=conn, func=name,
                                  strategy=f"symbols:{label}", run_kind="symbols")
            row.setdefault("chain", []).append(
                {"label": label, "compiled": bool(att.compiled), "exact": bool(att.exact),
                 "score": float(att.score),
                 "receipt_id": getattr(att, "receipt_id", None)})
            return att

        base = score_child("baseline", source)
        row["baseline_compiled"] = bool(base.compiled)
        row["baseline_undefined"] = sorted(undefined_names(base.compiler_stderr or ""))
        if not base.compiled:
            try:
                compile_chain.chain(name, "baseline", source, base, score_child,
                                    headers="", rounds=6, budget=args.budget)
            except Exception as exc:                                  # noqa: BLE001
                row["chain_error"] = f"{type(exc).__name__}: {exc}"
        admitted = [c for c in row.get("chain", []) if c["compiled"]]
        row["now_compiles"] = bool(admitted)
        row["admitted_by"] = admitted[0]["label"] if admitted else None
        row["status"] = "ok"
        rows.append(row)
        print("[%d/%d] %-44s vars=%3d baseline=%-5s admitted=%-5s %s"
              % (i, len(population), name[:44], len(item["symbols"]), row["baseline_compiled"],
                 row["now_compiles"], row["admitted_by"] or ""), flush=True)

    admitted = [r for r in rows if r.get("now_compiles")]
    symbols = collections.Counter()
    for r in rows:
        symbols.update(r["symbols"])
    summary = {
        "population": len(rows),
        "distinct_undefined_symbols": len(symbols),
        "total_occurrences": sum(symbols.values()),
        "admitted_by_the_ladder": len(admitted),
        "admitted_functions": sorted(r["function"] for r in admitted),
        "admitted_only_after_a_later_rung": sorted(
            r["function"] for r in admitted if r.get("admitted_by") != "baseline"),
        "top_symbols": symbols.most_common(25),
    }
    (args.out / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
