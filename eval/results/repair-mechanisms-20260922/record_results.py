"""Fresh certificates for newly solved functions; preserve the global match set."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from probe import OUT, NATIVE, REPO, ROOT, isolate, score_logged, _attempt_to_verdict


def status(label):
    result = subprocess.run([sys.executable, "-m", "eval.status"], cwd=ROOT,
                            text=True, capture_output=True, check=True)
    (OUT / f"status-{label}.md").write_text(result.stdout)
    print("\n".join(result.stdout.splitlines()[:7]), flush=True)


def main():
    verified = json.loads((OUT / "public-verification.json").read_text())
    assert verified["complete"]
    if (OUT / "inventory-receipts.json").exists():
        raise SystemExit("preserve inventory receipts")
    status("before")
    results = []
    conn = sqlite3.connect(Path_home_db(), timeout=30)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    for row in verified["confirmations"]:
        name = row["function"]
        old = conn.execute("SELECT a.id FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                           "WHERE f.name=? AND a.exact=1 LIMIT 1", (name,)).fetchone()
        if old:
            results.append({"function": name, "status": "already-object-exact", "receipt_id": old[0]})
            continue
        source = (OUT / row["source_path"]).read_text()
        digest = hashlib.sha256(source.encode()).hexdigest()
        assert digest == row["source_sha256"]
        tier = "project-header-assisted" if '#include "game/' in source else "source-independent"
        repo = isolate(REPO, NATIVE / "inventory" / name, name)
        attempt = score_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy="repair-mechanisms-20260922:" + tier, run_id="repair-mechanisms-inventory-20260922",
            action="independent-confirmation", model="deterministic-tools", run_kind="development-repair",
            prompt="Fresh recompile of a frozen compiler-search solution; no reference body.",
            extra={"training_eligible": False, "assistance_tier": tier,
                   "origin_private_receipt_id": row["receipt_id"],
                   "origin_private_database": str(NATIVE / "attempts.sqlite"),
                   "source_path": str(OUT / row["source_path"]), "integration_requested": False})
        verdict = _attempt_to_verdict(attempt)
        (OUT / f"{name}--inventory-verification.json").write_text(json.dumps(verdict, indent=2))
        entry = {"function": name, "receipt_id": attempt.receipt_id, "source_sha256": digest,
                 "exact": attempt.exact, "frontend_passed": (attempt.frontend or {}).get("passed"),
                 "assistance_tier": tier, "status": "new-object-exact"}
        results.append(entry)
        (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
        assert entry["exact"] and entry["frontend_passed"]
        assert attempt.verification["candidate_source_sha256"] == digest
        print(json.dumps(entry), flush=True)
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
    (OUT / "inventory-ratchet.json").write_text(json.dumps({"before": len(before), "after": len(after),
        "removed": sorted(before - after), "added": sorted(after - before)}, indent=2))
    conn.close()
    status("after")


def Path_home_db():
    from pathlib import Path
    return Path.home() / "decomp/kb-sbk1.sqlite"


if __name__ == "__main__":
    main()
