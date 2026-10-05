"""Append fresh, correctly tiered certificates to the normal attempt inventory.

Does not integrate C into a game TU, mutate inference, or mark functions matched.
All compiler work remains in the experiment's native isolated workspace.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import workspace

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/residual-repair-20260922"
DB = Path.home() / "decomp/kb-sbk1.sqlite"


def status(label):
    result = subprocess.run([sys.executable, "-m", "eval.status"], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    (OUT / f"status-{label}.md").write_text(result.stdout)
    print("\n".join(result.stdout.splitlines()[:8]), flush=True)


def main():
    status("before")
    results = []
    cases = [("Fdistort", "search-with-cursor", "project-header-assisted"),
             ("osEPiRawWriteIo", "literal-status-address", "source-independent"),
             ("osEPiRawReadIo", "literal-status-address", "source-independent")]
    with sqlite3.connect(DB, timeout=30) as conn:
        before = conn.execute("SELECT count(DISTINCT func_addr) FROM attempts WHERE exact=1").fetchone()[0]
        for name, label, tier in cases:
            code = (OUT / f"{name}--{label}.c").read_text()
            digest = hashlib.sha256(code.encode()).hexdigest()
            strategy = f"residual-repair-20260922:{tier}"
            old = conn.execute("SELECT id FROM attempts WHERE source_sha256=? AND strategy=?", (digest, strategy)).fetchone()
            if old:
                results.append({"function": name, "receipt_id": old[0], "status": "already-recorded"})
                continue
            repo = isolate(REPO, NATIVE / "recorded" / name, name)
            ws = repo / "nonmatchings" / name
            attempt = workspace.score(ws, repo, name, code, conn=conn, func=name,
                strategy=strategy, run_id="residual-repair-inventory-20260922",
                model="codex-assistant-authored-development", run_kind="development-repair",
                prompt="Recompile frozen development solution from eval/results/residual-repair-20260922; no reference body used.",
                extra={"training_eligible": False, "assistance_tier": tier,
                       "origin": str(OUT / f"{name}--{label}.c"), "integration_requested": False})
            boundary = (attempt.verification or {}).get("function_boundary") or {}
            verdict = _attempt_to_verdict(attempt)
            (OUT / f"{name}--inventory-verification.json").write_text(json.dumps(verdict, indent=2))
            row = {"function": name, "receipt_id": attempt.receipt_id, "source_sha256": digest,
                   "object_exact": attempt.exact, "function_exact": boundary.get("function_exact", False),
                   "boundary_schema": boundary.get("schema_version"), "strategy": strategy,
                   "frontend_passed": (attempt.frontend or {}).get("passed")}
            results.append(row)
            (OUT / "inventory-receipts.json").write_text(json.dumps(results, indent=2))
            assert row["frontend_passed"] and (row["object_exact"] or (row["function_exact"] and row["boundary_schema"] == 3)), row
            print(json.dumps(row), flush=True)
        after = conn.execute("SELECT count(DISTINCT func_addr) FROM attempts WHERE exact=1").fetchone()[0]
        assert after >= before, (before, after)
    status("after")


if __name__ == "__main__":
    main()
