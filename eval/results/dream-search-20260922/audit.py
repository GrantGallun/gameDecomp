"""Read-only audit of pilot costs, source hashes, explicit edges and saved worlds."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.search_replay import load_world

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/dream-search-20260922"


def main():
    rows = []
    for name in ("pilot-v1", "pilot-v2", "evolution-v1", "adapted-v1"):
        report = json.loads((OUT / name / "report.json").read_text())
        conn = sqlite3.connect(f"file:{NATIVE / name / 'attempts.sqlite'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        attempts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM attempts")}
        for row in attempts.values():
            assert hashlib.sha256(row["source_code"].encode()).hexdigest() == row["source_sha256"]
            assert json.loads(row["sampling"])["training_eligible"] is False
        edges = list(conn.execute("SELECT * FROM attempt_edges"))
        for edge in edges:
            assert edge["parent_attempt_id"] in attempts and edge["child_attempt_id"] in attempts
            assert attempts[edge["child_attempt_id"]]["parent_attempt_id"] == edge["parent_attempt_id"]
        checked_worlds = 0
        for path in (OUT / name).glob("*.world.json"):
            # Old prototype contexts deliberately remain unupgraded and excluded.
            strict = name in {"evolution-v1", "adapted-v1"}
            world = load_world(path) if strict else json.loads(path.read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for node in nodes.values():
                receipt = attempts[node["verdict"]["receipt_id"]]
                assert node["source_sha256"] == receipt["source_sha256"]
                if node["parent"] is not None:
                    parent = attempts[node["parent_receipt_id"]]
                    assert parent["source_sha256"] == nodes[node["parent"]]["source_sha256"]
                    assert receipt["parent_attempt_id"] == parent["id"]
            checked_worlds += strict
        collection = sum(r["compiles"] for r in report["collection"])
        comparison = sum(r["compiles"] for r in report["comparison"])
        confirmations = len(report["confirmations"])
        assert collection + comparison + confirmations == len(attempts)
        rows.append({"run": name, "complete": bool(report.get("complete")), "collection": collection,
                     "comparison": comparison, "confirmations": confirmations, "attempts": len(attempts),
                     "explicit_edges": len(edges), "strictly_validated_worlds": checked_worlds})
        conn.close()
    print(json.dumps({"runs": rows, "total_compiles": sum(r["attempts"] for r in rows),
                      "all_sources_and_recorded_edges_valid": True}, indent=2))


if __name__ == "__main__":
    main()
