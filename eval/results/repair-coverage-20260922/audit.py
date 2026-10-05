"""Read-only source, certificate, lineage, and complete cost audit."""
import hashlib
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/repair-coverage-20260922"


def digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def check_attempt(row):
    assert digest(row["source_code"]) == row["source_sha256"], row["id"]
    meta = json.loads(row["sampling"])
    assert meta["training_eligible"] is False, row["id"]
    if row["exact"]:
        cert = meta["verification"]
        assert cert["exact"] and cert["status"] == "object_sections_exact"
        assert cert["candidate_source_sha256"] == row["source_sha256"]
        assert cert["frontend"]["passed"]


def main():
    conn = sqlite3.connect(f"file:{NATIVE / 'attempts.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM attempts")}
    for row in attempts.values():
        check_attempt(row)
    edges = list(conn.execute("SELECT * FROM attempt_edges"))
    for edge in edges:
        parent, child = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert child["parent_attempt_id"] == parent["id"]
        assert child["func_addr"] == parent["func_addr"]
    with_parent = {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None}
    assert with_parent == {e["child_attempt_id"] for e in edges}
    report = json.loads((OUT / "verified/verification.json").read_text())
    assert report["all_confirmed"]
    for path, sha in report["implementation_sha256"].items():
        measured = OUT / "verified/verify-used.py" if path.endswith("/verify.py") else OUT.parents[2] / path
        assert hashlib.sha256(measured.read_bytes()).hexdigest() == sha, path
    for arm in report["arms"]:
        source = (OUT / "verified" / f"{arm['function']}--{arm['arm']}.c").read_text()
        assert digest(source) == arm["source_sha256"]
        for row in arm["attempts"]:
            saved = attempts[row["receipt_id"]]
            assert row["source_sha256"] == saved["source_sha256"]
            assert row["exact"] == bool(saved["exact"])
        if arm["exact"]:
            assert arm["trace"]["steps"][-1]["candidate_sha256"] == digest(source)
            assert any(r["exact"] and r["source_sha256"] == digest(source) for r in arm["attempts"])
    for row in report["confirmations"]:
        saved = attempts[row["receipt_id"]]
        assert saved["exact"] and row["source_sha256"] == saved["source_sha256"]
        assert saved["parent_attempt_id"] == row["parent_receipt_id"]
        assert attempts[row["parent_receipt_id"]]["source_sha256"] == saved["source_sha256"]
    probe = json.loads((OUT / "probe.json").read_text())
    original = json.loads((OUT / "verification.json").read_text())
    failed_export = json.loads((OUT / "__ll_rem--confirmed.json").read_text())
    assert not failed_export["exact"] and not attempts[failed_export["receipt_id"]]["exact"]
    parts = {"exploratory": len(probe["rows"]),
             "original_comparison": sum(a["compiles"] for a in original["arms"]),
             "failed_export_confirmation": 1,
             "corrected_comparison": sum(a["compiles"] for a in report["arms"]),
             "corrected_confirmations": len(report["confirmations"])}
    assert sum(parts.values()) == len(attempts) == report["total_private_compiles_including_probe"]
    main_db = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    main_db.row_factory = sqlite3.Row
    inventory = json.loads((OUT / "inventory-receipts.json").read_text())
    for item in inventory:
        row = dict(main_db.execute("SELECT * FROM attempts WHERE id=?", (item["receipt_id"],)).fetchone())
        check_attempt(row)
        assert row["exact"] and item["source_sha256"] == row["source_sha256"]
    summary = {"private_compiles": len(attempts), "cost_breakdown": parts,
               "inventory_compiles": len(inventory), "total_compiles": len(attempts) + len(inventory),
               "explicit_private_edges": len(edges), "all_source_and_certificate_bindings_valid": True,
               "old_export_artifacts": "retained as failed harness evidence; excluded from success claims"}
    print(json.dumps(summary, indent=2))
    conn.close()
    main_db.close()


if __name__ == "__main__":
    main()
