"""Record independently confirmed exacts in the main KB by a FRESH recompile there; ratchet-checked.

Mirrors repair-mechanisms-20260922/record_results.py. Tier by the same rule: a source that includes a
project `game/` header is project-header-assisted. `eval.status` applies its reference-type test on top.
Exposed functions (named in a 2026-09-22 Codex experiment) are recorded but labelled development.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze.json").read_text())
CODE = Path(FROZEN["code_root"])
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402

NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
REPO = Path.home() / "decomp/sbk1"


def main():
    reports = [dict(json.loads((OUT / name).read_text()), _db=str(NATIVE / db))
               for name, db in (("report.json", "attempts.sqlite"), ("report2.json", "stage2/attempts.sqlite"))
               if (OUT / name).exists()]
    exposed = set(json.loads((OUT / "exposed.json").read_text())["exposed"])
    if (OUT / "inventory-receipts.json").exists():
        raise SystemExit("preserve inventory receipts")
    rows = {(r["function"], r.get("best_source_sha256")): r
            for report in reports for r in report["rows"] if r.get("exact")}
    confirmations, seen = [], set()
    for report in reports:                    # one fresh inventory compile per function
        for c in report["confirmations"]:
            if c["function"] not in seen:
                seen.add(c["function"])
                confirmations.append(dict(c, _db=report["_db"]))
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for c in confirmations:
        name = c["function"]
        assert c["exact"], name
        old = conn.execute("SELECT a.id FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                           "WHERE f.name=? AND a.exact=1 LIMIT 1", (name,)).fetchone()
        if old:
            results.append({"function": name, "status": "already-object-exact", "receipt_id": old[0]})
            continue
        source = rows[(name, c["source_sha256"])]["exact_source"]
        digest = hashlib.sha256(source.encode()).hexdigest()
        assert digest == c["source_sha256"]
        tier = "project-header-assisted" if '#include "game/' in source else "source-independent"
        phase = "development" if name in exposed else "transfer"
        repo = isolate(REPO, NATIVE / "inventory" / name, name)
        verdict = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy=f"population-transfer-20260922:{tier}", run_id="population-transfer-inventory-20260922",
            action="independent-confirmation", model="deterministic-repair-search", run_kind="population-transfer",
            prompt="Fresh recompile of a frozen deterministic-search solution; no reference body.",
            extra={"training_eligible": False, "assistance_tier": tier, "phase": phase,
                   "origin_private_receipt_id": c["receipt_id"], "origin_private_database": c["_db"]})
        entry = {"function": name, "receipt_id": verdict["receipt_id"], "source_sha256": digest,
                 "exact": verdict["exact"], "assistance_tier": tier, "phase": phase, "status": "new-object-exact"}
        results.append(entry)
        (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
        assert entry["exact"], name
        print(json.dumps(entry), flush=True)
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
    (OUT / "inventory-ratchet.json").write_text(json.dumps({"before": len(before), "after": len(after),
        "removed": sorted(before - after), "added": sorted(after - before)}, indent=2))
    print(json.dumps({"before": len(before), "after": len(after)}))


if __name__ == "__main__":
    main()
