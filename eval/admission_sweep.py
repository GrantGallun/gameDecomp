"""The C89/target-linkage fix applied to every STORED candidate in the admission bucket.

No model, no GPU: this takes the source the pipeline already has on disk for each never-compiled
function and asks whether the fix alone admits it. It is the cheap, complete version of the
question the live re-run samples, and it cannot be affected by a stalled generation.

Paired by construction: the same stored source is compiled raw and again after
`c89.to_c89` + `c89.public_definition`, so the difference is attributable to the rewrite and
nothing else.

Population: functions with at least one attempt whose stored source parses as C, and no compiling
attempt OUTSIDE this rerun session -- `run_id like 'admission-rerun%'` is excluded so the population
is the historical bucket, not what today's run has already drained.

Usage:
    python3 -m eval.admission_sweep --db ... --repo ... --out ...
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import admission_triage                                    # noqa: E402
from solver import c89, workspace                                     # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = "/home/grant/decomp/sbk1"


def best_stored_source(conn, addr: int) -> tuple[int, str] | None:
    """The most recent attempt whose source actually contains a C function definition."""
    rows = conn.execute(
        "select id, source_code from attempts where func_addr = ? and source_code is not null "
        "and length(source_code) > 0 order by id desc", (addr,)).fetchall()
    for rid, code in rows:
        if workspace.FUNC_RE.search(code or "") if hasattr(workspace, "FUNC_RE") else True:
            return rid, code
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=Path(DEFAULT_REPO))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db, timeout=120)
    population = admission_triage.never_compiled(conn)
    population.sort(key=lambda item: item["name"])
    if args.limit:
        population = population[: args.limit]
    print("population (never compiled historically, libultra excluded): %d" % len(population),
          flush=True)

    rows = []
    for index, item in enumerate(population, 1):
        func = item["name"]
        found = best_stored_source(conn, item["addr"])
        row = {"function": func, "addr": item["addr"]}
        if not found:
            row["status"] = "no-stored-source"
            rows.append(row)
            print("[%d/%d] %-44s no stored source" % (index, len(population), func), flush=True)
            continue
        rid, code = found
        row["source_attempt_id"] = rid
        normalized = c89.public_definition(c89.to_c89(code), func)
        row["c89_changed"] = normalized != code
        try:
            ws = workspace.bootstrap(args.repo, func)
        except Exception as exc:                                    # noqa: BLE001
            row["status"] = f"bootstrap-failed: {type(exc).__name__}"
            rows.append(row)
            print("[%d/%d] %-44s bootstrap failed" % (index, len(population), func), flush=True)
            continue
        for variant, source in (("raw", code), ("c89", normalized)):
            if variant == "c89" and not row["c89_changed"]:
                row[f"{variant}_outcome"] = "identical-to-raw"
                continue
            try:
                att = workspace.score(ws, args.repo, func, source)
            except Exception as exc:                                # noqa: BLE001
                row[f"{variant}_outcome"] = f"raised {type(exc).__name__}"
                continue
            row[f"{variant}_compiled"] = bool(att.compiled)
            row[f"{variant}_score"] = float(att.score)
            row[f"{variant}_exact"] = bool(att.exact)
        row["status"] = "ok"
        rows.append(row)
        print("[%d/%d] %-44s raw=%-5s c89=%-5s %s%s" % (
            index, len(population), func, row.get("raw_compiled"), row.get("c89_compiled"),
            ("c89_score=%.2f " % row["c89_score"]) if row.get("c89_compiled") else "",
            ("EXACT" if (row.get("raw_exact") or row.get("c89_exact")) else "")), flush=True)

    raw_only = [r["function"] for r in rows if r.get("raw_compiled")]
    c89_only = [r["function"] for r in rows if r.get("c89_compiled") and not r.get("raw_compiled")]
    exact = [r["function"] for r in rows if r.get("raw_exact") or r.get("c89_exact")]
    summary = {
        "population": len(rows),
        "compiled_raw": len(raw_only),
        "compiled_only_after_c89": len(c89_only),
        "c89_attributable_functions": c89_only,
        "now_exact": len(exact),
        "exact_functions": exact,
        "c89_changed_source": sum(1 for r in rows if r.get("c89_changed")),
        "status_counts": dict(collections.Counter(r.get("status") for r in rows)),
    }
    (args.out / "sweep-rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (args.out / "sweep-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
