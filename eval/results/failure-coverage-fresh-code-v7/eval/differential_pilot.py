#!/usr/bin/env python3
"""Grounded mode-16 pilot for the differential function debugger."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from solver import mips_differential as differential


DEFAULT_FUNCTION = "updateRacePlayerMode16AerialTrick"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _function_body(text: str, function: str) -> str:
    start = text.find(f"glabel {function}")
    if start < 0:
        raise ValueError(f"glabel {function} is absent")
    end = text.find(f"endlabel {function}", start)
    return text[start:end if end >= 0 else None].strip()


def _summary(rows: list[differential.DifferentialResult]) -> dict:
    counts = {status: sum(row.status == status for row in rows)
              for status in ("passed", "failed", "inconclusive")}
    return {
        "cases": len(rows),
        **counts,
        "mean_target_instructions": round(
            sum(row.target.instruction_count for row in rows) / len(rows), 3),
        "mean_candidate_instructions": round(
            sum(row.candidate.instruction_count for row in rows) / len(rows), 3),
    }


def run(repo: Path, candidate_tag: str, output: Path, *,
        expected_source_sha256: str = "") -> dict:
    workspace = repo / "nonmatchings" / DEFAULT_FUNCTION
    target_path = workspace / "target_object_dump_normalized.s"
    raw_target_path = workspace / "target.s"
    candidate_path = workspace / f"{candidate_tag}_object_dump_normalized.s"
    source_path = workspace / f"{candidate_tag}.c"
    for path in (target_path, raw_target_path, candidate_path, source_path):
        if not path.exists():
            raise FileNotFoundError(path)
    target_assembly = target_path.read_text(errors="replace")
    raw_target_assembly = _function_body(
        raw_target_path.read_text(errors="replace"), DEFAULT_FUNCTION)
    candidate_assembly = candidate_path.read_text(errors="replace")
    source = source_path.read_text(errors="replace")
    if expected_source_sha256 and _sha(source) != expected_source_sha256:
        raise ValueError("candidate source digest does not match frozen receipt")

    cases = differential.mode16_cases()
    control = differential.run_suite(
        target_assembly, raw_target_assembly, cases,
        target_name="target-normalized", candidate_name="target-raw-control",
        call_arities=differential.MODE16_CALL_ARITIES)
    treatment = differential.run_suite(
        target_assembly, candidate_assembly, cases,
        target_name="target", candidate_name="audited-loto-candidate",
        call_arities=differential.MODE16_CALL_ARITIES)
    receipt = {
        "schema_version": 1,
        "kind": "mips-differential-function-pilot",
        "created_at": int(time.time()),
        "function": DEFAULT_FUNCTION,
        "candidate_tag": candidate_tag,
        "inputs": {
            "target_path": str(target_path),
            "target_sha256": _sha(target_assembly),
            "raw_target_path": str(raw_target_path),
            "raw_target_function_sha256": _sha(raw_target_assembly),
            "candidate_assembly_path": str(candidate_path),
            "candidate_assembly_sha256": _sha(candidate_assembly),
            "candidate_source_path": str(source_path),
            "candidate_source_sha256": _sha(source),
        },
        "policy": {
            "candidate_execution": "compiled MIPS normalized object assembly",
            "endianness": "big",
            "external_calls": "deterministic opaque hooks; calls have no memory side effects",
            "pass_gate": [
                "ordered call identities, normalized arguments, and call-time memory",
                "final non-stack memory",
                "declared return registers",
                "callee-saved ABI",
            ],
            "instruction_count": "runtime proxy only; not an equivalence gate",
            "finite_tests_are_not_proof": True,
            "unsupported_is_inconclusive": True,
        },
        "control": {
            "description": (
                "normalized target object dump versus independently formatted "
                "raw target function assembly"),
            "summary": _summary(control),
            "results": [row.to_dict() for row in control],
        },
        "candidate": {
            "description": "frozen v5 leave-one-TU-out assembly-shape winner",
            "summary": _summary(treatment),
            "results": [row.to_dict() for row in treatment],
        },
    }
    receipt["status"] = (
        "validated" if receipt["control"]["summary"]["passed"] == len(cases)
        and receipt["candidate"]["summary"]["failed"] == len(cases)
        else "inconclusive")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    parser.add_argument("--candidate-tag", required=True)
    parser.add_argument("--expected-source-sha256", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.repo, args.candidate_tag, args.output,
                  expected_source_sha256=args.expected_source_sha256)
    print(json.dumps({
        "status": receipt["status"],
        "control": receipt["control"]["summary"],
        "candidate": receipt["candidate"]["summary"],
        "first_divergences": [
            row["first_divergence"] for row in receipt["candidate"]["results"]
        ],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
