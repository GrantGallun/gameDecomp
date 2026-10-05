"""Read-only audit of every exact this experiment claims. Exits non-zero on any unbound claim.

For each function recorded in inventory-receipts.json: the search world that found it validates
(source-bound certificate + frontend pass, via search_replay.validate_world); the world's exact node
hash equals the confirmed and recorded source hash; the private confirmation receipt is exact; the
main-KB receipt is exact=1 for that function with that source. Also re-derives the headline paired
counts from the rows so RESULT.md numbers are recomputed, not copied.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze2.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.search_replay import load_world  # noqa: E402

NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
KB = json.loads((OUT / "freeze.json").read_text())["kb"]


def main():
    failures = []
    reports = {"stage1": json.loads((OUT / "report.json").read_text()),
               "stage2": json.loads((OUT / "report2.json").read_text())}
    dbs = {"stage1": sqlite3.connect(NATIVE / "attempts.sqlite"), "stage2": sqlite3.connect(NATIVE / "stage2/attempts.sqlite")}
    kb = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    inventory = json.loads((OUT / "inventory-receipts.json").read_text())
    for entry in inventory:
        name, sha = entry["function"], entry["source_sha256"]
        found = [(stage, r) for stage, rep in reports.items() for r in rep["rows"]
                 if r["function"] == name and r.get("exact") and r.get("best_source_sha256") == sha]
        if not found:
            failures.append(f"{name}: no search row produced the recorded source")
            continue
        for stage, row in found:
            world = load_world(row["world"])                         # raises on an unbound exact
            node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
            if not node["verdict"]["exact"] or node["source_sha256"] != sha:
                failures.append(f"{name}/{row['arm']}: world node not exact for recorded source")
        confirmations = [c for rep in reports.values() for c in rep["confirmations"]
                         if c["function"] == name and c["source_sha256"] == sha]
        if not confirmations or not all(c["exact"] for c in confirmations):
            failures.append(f"{name}: independent confirmation missing or not exact")
        for stage, rep in reports.items():
            for c in rep["confirmations"]:
                if c["function"] == name:
                    got = dbs[stage].execute("select exact, source_sha256 from attempts where id=?", (c["receipt_id"],)).fetchone()
                    if not got or got[0] != 1:
                        failures.append(f"{name}: {stage} confirmation receipt {c['receipt_id']} not exact in private DB")
        got = kb.execute("select a.exact, a.source_code from attempts a join functions f on f.addr=a.func_addr "
                         "where a.id=? and f.name=?", (entry["receipt_id"], name)).fetchone()
        if not got or got[0] != 1 or hashlib.sha256(got[1].encode()).hexdigest() != sha:
            failures.append(f"{name}: main-KB receipt {entry['receipt_id']} not exact for this source")
    ratchet = json.loads((OUT / "inventory-ratchet.json").read_text())
    if ratchet["removed"] or ratchet["after"] - ratchet["before"] != len([e for e in inventory if e["status"] == "new-object-exact"]):
        failures.append(f"ratchet mismatch: {ratchet}")

    def solved(r):
        return bool(r and r.get("exact")) and not r.get("baseline_exact")
    rows = {(r["function"], r["arm"]): r for rep in reports.values() for r in rep["rows"]}
    names = sorted({f for f, _a in rows})
    headline = {arm: sum(solved(rows.get((f, arm))) for f in names) for arm in ("control", "expanded", "routed", "prior")}
    errors = sum(x.get("error") is not None for r in rows.values() for x in r.get("receipts", []))
    print(json.dumps({"functions": len(names), "exact_by_arm": headline, "receipt_errors": errors,
                      "inventory_audited": len(inventory), "failures": failures}, indent=1))
    raise SystemExit(1 if failures or errors else 0)


if __name__ == "__main__":
    main()
