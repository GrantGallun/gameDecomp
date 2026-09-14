#!/usr/bin/env python3
"""Coverage-guided semantic replay for the mode-16 development function."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from solver import mips_differential as differential


FUNCTION = "updateRacePlayerMode16AerialTrick"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _summary(rows: list[differential.DifferentialResult]) -> dict:
    return {
        "cases": len(rows),
        "passed": sum(row.status == "passed" for row in rows),
        "failed": sum(row.status == "failed" for row in rows),
        "inconclusive": sum(row.status == "inconclusive" for row in rows),
    }


def run(*, repo: Path, output: Path, max_cases: int,
        candidate_assembly: Path | None = None) -> dict:
    workspace = repo / "nonmatchings" / FUNCTION
    target_path = workspace / "target_object_dump_normalized.s"
    target = target_path.read_text(errors="replace")
    exploration = differential.explore_coverage(
        target, differential.mode16_cases(), target_name=FUNCTION,
        call_arities=differential.MODE16_CALL_ARITIES,
        max_cases=max_cases)

    receipt = {
        "schema_version": 1,
        "kind": "differential-coverage-pilot",
        "created_at": int(time.time()),
        "function": FUNCTION,
        "inputs": {
            "target_path": str(target_path),
            "target_sha256": _sha(target),
            "seed_case_count": len(differential.mode16_cases()),
            "max_cases": max_cases,
        },
        "policy": {
            "coverage_target": (
                "every structurally reachable target instruction and both "
                "outcomes of every conditional target branch"),
            "search": (
                "deterministic boundary values for executed player/global "
                "loads and scalar entry registers, plus seeded opaque-call "
                "return variation"),
            "uncovered_outcome_means": (
                "unresolved; it is not called infeasible without a proof"),
            "semantic_claim": (
                "agreement applies only to generated concrete cases and is "
                "not a proof of equivalence"),
        },
        "exploration": exploration.to_dict(),
    }

    if candidate_assembly is not None:
        candidate = candidate_assembly.read_text(errors="replace")
        candidate_exploration = differential.explore_coverage(
            candidate, exploration.cases, target_name="candidate",
            call_arities=differential.MODE16_CALL_ARITIES,
            max_cases=max_cases)
        rows = differential.run_suite(
            target, candidate, candidate_exploration.cases,
            target_name="target", candidate_name="candidate",
            call_arities=differential.MODE16_CALL_ARITIES)
        target_program = differential.Program.parse("target", target)
        candidate_program = differential.Program.parse("candidate", candidate)
        target_coverage = differential.coverage_report(
            target_program, [row.target for row in rows])
        candidate_coverage = differential.coverage_report(
            candidate_program, [row.candidate for row in rows])
        receipt["inputs"].update({
            "candidate_assembly_path": str(candidate_assembly),
            "candidate_assembly_sha256": _sha(candidate),
        })
        receipt["candidate"] = {
            "exploration": candidate_exploration.to_dict(),
            "combined_target_coverage": target_coverage.to_dict(),
            "combined_candidate_coverage": candidate_coverage.to_dict(),
            "summary": _summary(rows),
            "failures": [
                {"case": row.case, "status": row.status,
                 "first_divergence": row.first_divergence}
                for row in rows if row.status != "passed"
            ],
        }

    if candidate_assembly is None:
        receipt["status"] = (
            "coverage_complete" if exploration.report.complete
            else "coverage_incomplete")
    else:
        candidate_result = receipt["candidate"]
        both_covered = (
            candidate_result["combined_target_coverage"]["status"] ==
            "complete" and
            candidate_result["combined_candidate_coverage"]["status"] ==
            "complete")
        all_passed = (candidate_result["summary"]["passed"] ==
                      candidate_result["summary"]["cases"])
        receipt["status"] = (
            "both_covered_semantics_passed" if both_covered and all_passed
            else "coverage_or_semantics_incomplete")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path,
                        default=Path.home() / "decomp/sbk1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=5_000)
    parser.add_argument("--candidate-assembly", type=Path)
    args = parser.parse_args()
    receipt = run(
        repo=args.repo, output=args.output, max_cases=args.max_cases,
        candidate_assembly=args.candidate_assembly)
    print(json.dumps({
        "status": receipt["status"],
        "exploration": receipt["exploration"],
        "candidate": receipt.get("candidate"),
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
