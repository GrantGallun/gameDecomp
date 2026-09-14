"""Build governed finished-decomp teacher packets for logic-first targets."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair, logic_first
from solver import reference_teacher


def build(*, repo: Path, db: Path, baseline_path: Path, out_dir: Path,
          functions: list[str], max_examples: int = 6,
          max_source_chars: int = 16000) -> dict:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    available = {str(row["function"])
                 for row in baseline.get("functions", [])}
    missing = sorted(set(functions) - available)
    if missing:
        raise ValueError("targets absent from logic baseline: " + ", ".join(missing))
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 1,
        "kind": "finished-reference-teacher-packet-build",
        "created_at": int(time.time()),
        "baseline": str(baseline_path),
        "reference_repo": str(repo),
        "functions": [],
    }
    for function in functions:
        logic_packet = logic_first.module_packet(baseline, function)
        # Rank assembly siblings once, then apply the stronger TU exclusion to
        # the same frozen retrieval result. This keeps LOFO/LOTO comparable.
        similar = reference_teacher._similarity_rows(repo, function)
        row = {"function": function, "similarity_candidates": len(similar),
               "regimes": {}}
        for regime, suffix in (
                ("leave_one_function_out", "lofo"),
                ("leave_one_tu_out", "loto")):
            packet = reference_teacher.build_packet(
                repo=repo, conn=conn, logic_packet=logic_packet,
                regime=regime, max_examples=max_examples,
                max_source_chars=max_source_chars, similar=similar)
            path = out_dir / f"logic-first-reference-{function}-{suffix}-v1.json"
            agentrepair._atomic_json(path, packet)
            row["regimes"][regime] = {
                "path": str(path),
                "packet_digest": packet["packet_digest"],
                "examples": len(packet["examples"]),
                "matched_source_blocks": len(packet["matched_source_blocks"]),
                "example_functions": [
                    example["function"] for example in packet["examples"]],
                "target_tu_present": packet["contamination_audit"][
                    "target_tu_present"],
                "contamination_audit": packet["contamination_audit"],
            }
        receipt["functions"].append(row)
        agentrepair._atomic_json(
            out_dir / "logic-first-reference-teacher-v1-receipt.json", receipt)
    receipt["completed_at"] = int(time.time())
    receipt["aggregate"] = {
        "targets": len(receipt["functions"]),
        "packets": 2 * len(receipt["functions"]),
        "examples": sum(
            arm["examples"] for row in receipt["functions"]
            for arm in row["regimes"].values()),
        "matched_source_blocks": sum(
            arm["matched_source_blocks"] for row in receipt["functions"]
            for arm in row["regimes"].values()),
        "all_contamination_audits_pass": all(
            arm["contamination_audit"]["target_function_absent"]
            and arm["contamination_audit"]["target_definition_absent"]
            and arm["contamination_audit"]["normalized_target_duplicate_absent"]
            and arm["contamination_audit"]["target_tu_policy_satisfied"]
            for row in receipt["functions"]
            for arm in row["regimes"].values()),
    }
    agentrepair._atomic_json(
        out_dir / "logic-first-reference-teacher-v1-receipt.json", receipt)
    conn.close()
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--function", action="append", required=True)
    parser.add_argument("--max-examples", type=int, default=6)
    parser.add_argument("--max-source-chars", type=int, default=16000)
    args = parser.parse_args()
    receipt = build(
        repo=args.repo.expanduser().resolve(), db=args.db.expanduser().resolve(),
        baseline_path=args.baseline.expanduser().resolve(),
        out_dir=args.out_dir.expanduser().resolve(), functions=args.function,
        max_examples=args.max_examples,
        max_source_chars=args.max_source_chars)
    print(json.dumps(receipt["aggregate"], indent=2))


if __name__ == "__main__":
    main()
