"""Audit measured compiler costs, saved sources, certificates and explicit parents."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/repair-mechanisms-20260922"
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def check(row):
    assert sha(row["source_code"]) == row["source_sha256"], row["id"]
    meta = json.loads(row["sampling"])
    assert meta["training_eligible"] is False
    if row["exact"]:
        cert = meta["verification"]
        assert cert["exact"] and cert["status"] == "object_sections_exact"
        assert cert["candidate_source_sha256"] == row["source_sha256"]
        assert cert["frontend"]["passed"]


def check_mmio():
    from solver.mmio_repair import propose
    from solver import workspace
    result = {}
    for phase, archived in (("mmio", "mmio-before-review.py.txt"),
                            ("mmio-reviewed", "mmio-reviewed-implementation.py.txt")):
        report = json.loads((OUT / phase / "report.json").read_text())
        assert report["complete"] and report["training_eligible"] is False
        for path, digest in report["implementation_sha256"].items():
            file = OUT / archived if path == "solver/mmio_repair.py" else ROOT / path
            assert hashlib.sha256(file.read_bytes()).hexdigest() == digest, file
        conn = sqlite3.connect(f"file:{NATIVE / phase / 'attempts.sqlite'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        attempts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM attempts")}
        for receipt in attempts.values():
            check(receipt)
        edges = list(conn.execute("SELECT * FROM attempt_edges"))
        for edge in edges:
            parent, child = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
            assert parent["func_addr"] == child["func_addr"]
            assert child["parent_attempt_id"] == parent["id"]
        assert {r["id"] for r in attempts.values() if r["parent_attempt_id"] is not None} == {
            e["child_attempt_id"] for e in edges}
        for row in report["rows"]:
            name = row["function"]
            source = (OUT / phase / f"{name}--initial.c").read_text()
            final = (OUT / phase / f"{name}--final.c").read_text()
            assembly = workspace.target_asm(NATIVE / phase / "public" / name / "nonmatchings" / name, name)
            # Verify the final guarded implementation emits exactly the compiled
            # source at every measured input, without claiming another compile.
            current = propose(source, name, assembly, big_endian_o32=True)
            assert current["source"] == final, (phase, name, bool(current["changes"]), sha(current["source"]), sha(final))
            assert bool(current["changes"]) == row["changed"]
            assert sha(final) == row["source_sha256"]
            assert row["calls"] == len(row["attempts"])
            for saved in row["attempts"]:
                assert attempts[saved["receipt_id"]]["source_sha256"] == saved["source_sha256"]
            assert attempts[row["attempts"][-1]["receipt_id"]]["source_sha256"] == sha(final)
        for confirmation in report["confirmations"]:
            row = attempts[confirmation["receipt_id"]]
            meta = json.loads(row["sampling"])
            cert = meta["verification"]
            boundary = cert["function_boundary"]
            assert cert["candidate_source_sha256"] == row["source_sha256"] == confirmation["source_sha256"]
            assert cert["frontend"]["passed"] and boundary["schema_version"] == 3 and boundary["function_exact"]
            assert boundary["kind"] == "rom_backed_function_extent" and boundary["requires_isolated_integration"]
            assert not cert["exact"] and not row["exact"] and not boundary["whole_rom_verified"]
            for item in boundary["inputs"].values():
                assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
        assert len(attempts) == report["total_compiles"] == sum(r["calls"] for r in report["rows"]) + len(report["confirmations"])
        result[phase] = {"compiles": len(attempts), "explicit_parent_edges": len(edges),
                         "function_exact_confirmations": len(report["confirmations"]),
                         "final_generator_outputs_equal": True}
        conn.close()
    result["final_implementation_sha256"] = hashlib.sha256((ROOT / "solver/mmio_repair.py").read_bytes()).hexdigest()
    return result


def main():
    conn = sqlite3.connect(f"file:{NATIVE / 'attempts.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM attempts")}
    for row in attempts.values():
        check(row)
    edges = list(conn.execute("SELECT * FROM attempt_edges"))
    for edge in edges:
        parent, child = attempts[edge["parent_attempt_id"]], attempts[edge["child_attempt_id"]]
        assert parent["func_addr"] == child["func_addr"]
        assert child["parent_attempt_id"] == parent["id"]
    assert {a["id"] for a in attempts.values() if a["parent_attempt_id"] is not None} == {
        e["child_attempt_id"] for e in edges}
    report = json.loads((OUT / "comparison/report.json").read_text())
    assert report["complete"]
    for row in report["arms"]:
        assert row["compiles"] == len(row["attempts"]) <= report["budget_per_arm_including_baseline"]
        source = (OUT / "comparison" / f"{row['function']}--{row['arm']}.c").read_text()
        assert sha(source) == row["source_sha256"] == attempts[row["final_receipt_id"]]["source_sha256"]
        assert row["exact"] == bool(attempts[row["final_receipt_id"]]["exact"])
        for saved in row["attempts"]:
            receipt = attempts[saved["receipt_id"]]
            assert saved["source_sha256"] == receipt["source_sha256"]
            assert saved["parent_receipt_id"] == receipt["parent_attempt_id"]
    public = json.loads((OUT / "public-verification.json").read_text())
    assert public["complete"]
    for row in public["public_actions"]:
        source = (OUT / f"{row['function']}--public.c").read_text()
        assert row["exact"] and sha(source) == row["source_sha256"]
        assert row["transcript"]["steps"][-1]["candidate_sha256"] == sha(source)
        assert attempts[row["receipt_id"]]["source_sha256"] == sha(source)
    for row in public["confirmations"]:
        assert sha((OUT / row["source_path"]).read_text()) == row["source_sha256"]
        receipt = attempts[row["receipt_id"]]
        assert receipt["exact"] and receipt["source_sha256"] == row["source_sha256"]
        assert receipt["parent_attempt_id"] == row["parent_receipt_id"]
    phases = {f"probe{i or ''}": len(json.loads((OUT / f"probe{i or ''}.json").read_text())) for i in (0, 2, 3, 4)}
    phases.update({"paired_search": sum(r["compiles"] for r in report["arms"]),
                   "public_action": sum(r["compiles"] for r in public["public_actions"]),
                   "independent_confirmation": len(public["confirmations"])})
    assert sum(phases.values()) == len(attempts)
    inventory = json.loads((OUT / "inventory-receipts.json").read_text())
    db = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    new = [r for r in inventory if r["status"] == "new-object-exact"]
    for row in new:
        receipt = dict(db.execute("SELECT * FROM attempts WHERE id=?", (row["receipt_id"],)).fetchone())
        check(receipt)
        assert receipt["exact"] and row["source_sha256"] == receipt["source_sha256"]
    mmio = check_mmio()
    summary = {"private_compiles": len(attempts), "phases": phases, "explicit_private_edges": len(edges),
               "new_inventory_compiles": len(new), "total_core_compiles": len(attempts) + len(new),
               "all_source_and_certificate_bindings_valid": True,
               "mmio": mmio,
               "total_all_compiles": len(attempts) + len(new) + mmio["mmio"]["compiles"] + mmio["mmio-reviewed"]["compiles"]}
    (OUT / "audit.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    db.close()
    conn.close()


if __name__ == "__main__":
    main()
