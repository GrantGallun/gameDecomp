"""Check tightened guards against every state expanded in measured search."""
import hashlib
import importlib.machinery
import importlib.util
import json
import sqlite3
from probe import OUT, NATIVE, ROOT
from solver import representation_repairs as current


def main():
    archived = OUT / "representation-before-review.py.txt"
    loader = importlib.machinery.SourceFileLoader("measured_representation", str(archived))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    old = importlib.util.module_from_spec(spec)
    loader.exec_module(old)
    report = json.loads((OUT / "comparison/report.json").read_text())
    assert hashlib.sha256(archived.read_bytes()).hexdigest() == report["implementation_sha256"]["solver/representation_repairs.py"]
    ids = {r["parent_receipt_id"] for arm in report["arms"] for r in arm["attempts"] if r["parent_receipt_id"] is not None}
    ids |= {arm["attempts"][0]["receipt_id"] for arm in report["arms"]}
    public = json.loads((OUT / "public-verification.json").read_text())
    ids |= {r["parent_receipt_id"] for arm in public["public_actions"] for r in arm["attempts"] if r["parent_receipt_id"] is not None}
    conn = sqlite3.connect(f"file:{NATIVE / 'attempts.sqlite'}?mode=ro", uri=True)
    differences = []
    for receipt in sorted(ids):
        name, source, diff = conn.execute("SELECT f.name,a.source_code,a.diff_summary FROM attempts a "
            "JOIN functions f ON f.addr=a.func_addr WHERE a.id=?", (receipt,)).fetchone()
        for family in ("unsigned_float", "cursor_rebase", "residual_evidence"):
            args = (source, name) if family == "unsigned_float" else (source, name, diff or "")
            before, after = list(getattr(old, family)(*args)), list(getattr(current, family)(*args))
            if before != after:
                differences.append({"receipt_id": receipt, "function": name, "family": family,
                                    "old_candidates": len(before), "new_candidates": len(after)})
    result = {"expanded_states_checked": len(ids), "differences": differences,
        "measured_sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
        "reviewed_sha256": hashlib.sha256((ROOT / "solver/representation_repairs.py").read_bytes()).hexdigest(),
        "scope": "complete output streams of the three changed families at every measured search parent; no recompile implied"}
    (OUT / "review-equivalence.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert not differences
    conn.close()


if __name__ == "__main__":
    main()
