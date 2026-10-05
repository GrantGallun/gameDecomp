"""Record a report's independently confirmed exacts that are not yet object-exact in the main KB. Ratchet-checked.

    python3 record.py REPORT.json NATIVE_DIR RUN_ID     (WSL)
"""
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402

REPO = Path.home() / "decomp/sbk1"


def main(report_path, native, run_id):
    report = json.loads(Path(report_path).read_text())
    native = Path(native)
    confirmed = {c["function"]: c for c in report["confirmations"] if c["exact"]}
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for row in report["rows"]:
        name = row["function"]
        if not row.get("exact") or row.get("baseline_exact") or name not in confirmed:
            continue
        if conn.execute("SELECT 1 FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1",
                        (name,)).fetchone():
            results.append({"function": name, "status": "already-object-exact"})
            continue
        source = row["exact_source"]
        tier = "project-header-assisted" if '#include "game/' in source else "source-independent"
        repo = isolate(REPO, native / "inventory" / name, name)
        verdict = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy=f"branch-shape-20260924:{tier}", run_id=run_id,
            action="independent-confirmation", model="deterministic-repair-search", run_kind="population-search",
            prompt="Fresh recompile of a population-search exact (branch_shape families); no reference body.",
            extra={"training_eligible": False, "assistance_tier": tier, "path": row.get("best_path_families")})
        assert verdict["exact"], name
        results.append({"function": name, "receipt_id": verdict["receipt_id"], "assistance_tier": tier,
                        "path": row.get("best_path_families"), "status": "new-object-exact"})
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"results": results, "before": len(before), "after": len(after)}
    (HERE / f"inventory-{Path(report_path).stem}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
