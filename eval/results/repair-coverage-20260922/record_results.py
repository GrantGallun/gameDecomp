"""Recompile source-bound successes into the normal inventory, without TU integration."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import workspace
from logged_compile import score_logged

OUT = Path(__file__).resolve().parent
VERIFIED = OUT / "verified"
REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/repair-coverage-20260922"
DB = Path.home() / "decomp/kb-sbk1.sqlite"
STRATEGY = "repair-coverage-20260922:source-independent"


def status(label):
    result = subprocess.run([sys.executable, "-m", "eval.status"], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    (OUT / f"status-{label}.md").write_text(result.stdout)
    print("\n".join(result.stdout.splitlines()[:7]), flush=True)


def main():
    report = json.loads((VERIFIED / "verification.json").read_text())
    assert report["all_confirmed"] and len(report["confirmations"]) == 7
    for path, digest in report["implementation_sha256"].items():
        measured = VERIFIED / "verify-used.py" if path.endswith("/verify.py") else ROOT / path
        assert hashlib.sha256(measured.read_bytes()).hexdigest() == digest, path
    status("before")
    results = []
    with sqlite3.connect(DB, timeout=30) as conn:
        before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        for row in report["confirmations"]:
            name = row["function"]
            source_path = VERIFIED / f"{name}--wired-reconstruction.c"
            code = source_path.read_text()
            digest = hashlib.sha256(code.encode()).hexdigest()
            assert digest == row["source_sha256"]
            old = conn.execute("SELECT id FROM attempts WHERE source_sha256=? AND strategy=? AND exact=1",
                               (digest, STRATEGY)).fetchone()
            if old:
                results.append({"function": name, "receipt_id": old[0], "status": "already-recorded"})
                continue
            prior = conn.execute("SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                                 "WHERE f.name=? AND a.exact=1", (name,)).fetchone()[0]
            repo = isolate(REPO, NATIVE / "inventory" / name, name)
            attempt = score_logged(repo / "nonmatchings" / name, repo, name, code, conn=conn,
                strategy=STRATEGY, run_id="repair-coverage-inventory-20260922",
                action="independent-confirmation", model="deterministic-tools", run_kind="development-repair",
                prompt="Recompile the frozen target-assembly reconstruction; no reference function body used.",
                extra={"training_eligible": False, "assistance_tier": "source-independent",
                       "origin": str(source_path), "origin_private_receipt_id": row["receipt_id"],
                       "origin_private_database": str(NATIVE / "attempts.sqlite"),
                       "integration_requested": False})
            verdict = _attempt_to_verdict(attempt)
            (OUT / f"{name}--inventory-verification.json").write_text(json.dumps(verdict, indent=2))
            entry = {"function": name, "receipt_id": attempt.receipt_id, "source_sha256": digest,
                     "object_exact": attempt.exact, "frontend_passed": (attempt.frontend or {}).get("passed"),
                     "prior_exact_attempts": prior, "strategy": STRATEGY}
            results.append(entry)
            (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
            assert entry["object_exact"] and entry["frontend_passed"], entry
            assert attempt.verification["candidate_source_sha256"] == digest
            print(json.dumps(entry), flush=True)
        after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        assert before <= after
        (OUT / "inventory-ratchet.json").write_text(json.dumps({
            "before": len(before), "after": len(after), "removed": sorted(before - after),
            "added": sorted(after - before)}, indent=2))
    status("after")


if __name__ == "__main__":
    main()
