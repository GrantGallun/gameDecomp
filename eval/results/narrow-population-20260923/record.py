"""Record the run's independently confirmed exacts that are not yet object-exact in the main KB. Ratchet-checked."""
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

NATIVE = Path.home() / "decomp/experiments/narrow-population-20260923"
REPO = Path.home() / "decomp/sbk1"


def main():
    report = json.loads((HERE / "report.json").read_text())
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
        repo = isolate(REPO, NATIVE / "inventory" / name, name)
        verdict = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy=f"narrow-population-20260923:{tier}", run_id="narrow-inventory-20260923",
            action="independent-confirmation", model="deterministic-repair-search", run_kind="population-search",
            prompt="Fresh recompile of a population-search exact (narrow-local mechanism); no reference body.",
            extra={"training_eligible": False, "assistance_tier": tier, "path": row.get("best_path_families")})
        assert verdict["exact"], name
        results.append({"function": name, "receipt_id": verdict["receipt_id"], "assistance_tier": tier,
                        "path": row.get("best_path_families"), "status": "new-object-exact"})
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"results": results, "before": len(before), "after": len(after)}
    (HERE / "inventory-receipts.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
