#!/usr/bin/env python3
"""Audit candidate sources on a target-derived semantic stress panel.

The panel is selected without looking at candidate behavior.  It retains
boundary mutations for every dynamically observed target input even when a
mutation adds no instruction or branch coverage, so this is a holdout check
for arithmetic/value overfitting rather than another coverage search.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from eval import differential_repair_pilot as repair
from solver import mips_differential as differential
from solver import semantic_gradient, workspace


SCHEMA_VERSION = 1
KIND = "semantic-stress-audit"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _node(census: dict, function: str) -> dict:
    rows = [row for row in census.get("dag", {}).get("nodes", [])
            if row.get("function") == function]
    if len(rows) != 1:
        raise ValueError(
            f"expected one census node for {function}, found {len(rows)}")
    return rows[0]


def _cases_from_rows(rows: list[dict]) -> tuple[differential.TestCase, ...]:
    return tuple(differential.TestCase(
        name=str(row["name"]), seed=int(row["seed"]),
        player_writes=tuple(tuple(int(value) for value in write)
                            for write in row.get("player_writes", [])),
        global_writes=tuple(
            (str(write[0]), int(write[1]), int(write[2]))
            for write in row.get("global_writes", [])),
        entry_registers=tuple(
            (str(register[0]), int(register[1]))
            for register in row.get("entry_registers", [])),
        call_returns=tuple(
            (str(call[0]), int(call[1]), int(call[2]))
            for call in row.get("call_returns", [])),
    ) for row in rows)


def _cases_from_node(node: dict) -> tuple[differential.TestCase, ...]:
    rows = (node.get("candidate_exploration") or {}).get(
        "selected_cases") or []
    return _cases_from_rows(rows)


def _summary(rows: list[differential.DifferentialResult],
             frozen_count: int, *, target_program=None,
             candidate_program=None) -> dict:
    frozen = rows[:frozen_count]
    holdout = rows[frozen_count:]

    def statuses(panel: list[differential.DifferentialResult]) -> dict:
        counts = Counter(row.status for row in panel)
        return {key: counts.get(key, 0)
                for key in ("passed", "failed", "inconclusive")}

    result = {
        "all": statuses(rows),
        "frozen": statuses(frozen),
        "holdout": statuses(holdout),
        "first_holdout_failures": [
            {
                "case": row.case,
                "status": row.status,
                "first_divergence": row.first_divergence,
                "reasons": list(row.reasons),
            }
            for row in holdout if row.status != "passed"
        ][:20],
    }
    if target_program is not None:
        result["target_coverage"] = differential.coverage_report(
            target_program, [row.target for row in rows]).to_dict()
    if candidate_program is not None:
        result["candidate_coverage"] = differential.coverage_report(
            candidate_program, [row.candidate for row in rows]).to_dict()
    return result


def run(*, repo: Path, db: Path, census_path: Path, output: Path,
        function: str, candidate_paths: tuple[Path, ...],
        max_cases: int = 512,
        source_parent_attempt_id: int | None = None,
        panel_cases: tuple[differential.TestCase, ...] | None = None,
        frozen_count: int | None = None,
        panel_metadata: dict | None = None) -> dict:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    node = _node(census, function)
    frozen_cases = _cases_from_node(node)
    call_arities = {
        str(name): int(arity) for name, arity in
        (node.get("abi", {}).get("provisional_call_arities") or {}).items()
    }
    function_abi = node.get("abi", {}).get("function", {})
    return_registers = tuple(str(name) for name in
        (function_abi.get("return_registers") or []))
    mutable_registers = tuple(str(name) for name in
        (function_abi.get("mutable_scalar_registers") or []))
    pointer_registers = tuple(
        str(name) for name in
        (function_abi.get("pointer_registers") or []) if name != "a0")
    ws = workspace.bootstrap(repo, function)
    target_assembly = workspace.semantic_assembly(
        (ws / "target_object_dump_normalized.s").read_text(
            errors="replace"), ws / "target.o")
    if panel_cases is None:
        panel = differential.build_semantic_stress_panel(
            target_assembly, frozen_cases, target_name=function,
            call_arities=call_arities, return_registers=return_registers,
            mutable_entry_registers=mutable_registers,
            pointer_entry_registers=pointer_registers, max_cases=max_cases)
        panel_frozen_count = len(frozen_cases)
        panel_receipt = panel.to_dict()
        cases = panel.cases
    else:
        panel_frozen_count = (len(panel_cases) if frozen_count is None else
                              frozen_count)
        cases = panel_cases
        panel_receipt = dict(panel_metadata or {})
        panel_receipt.setdefault("selected_case_count", len(panel_cases))
        panel_receipt.setdefault(
            "selected_cases", [asdict(case) for case in panel_cases])
    run_id = f"semantic-stress-{time.time_ns()}-{function}"
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "function": function,
        "max_cases": max_cases,
        "model_calls": 0,
        "target_source_used": False,
        "source_parent_attempt_id": source_parent_attempt_id,
        "selection": (
            "target-only dimension-balanced boundary mutations; candidate "
            "behavior does not select cases"),
    }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "run_id": run_id,
        "created_at": int(time.time()),
        "status": "running",
        "config": config,
        "inputs": {
            "census": str(census_path),
            "candidate_paths": [str(path) for path in candidate_paths],
        },
        "panel": panel_receipt,
        "candidates": [],
    }
    _atomic_json(output, receipt)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    try:
        for index, candidate_path in enumerate(candidate_paths):
            source = candidate_path.read_text(encoding="utf-8")
            tag = f"{function}_semantic_stress_{index}_{time.time_ns()}"
            attempt = workspace.score(
                ws, repo, tag, source, conn=conn, func=function,
                strategy=KIND, model="", run_id=run_id,
                parent_attempt_id=source_parent_attempt_id,
                relation="semantic-stress-holdout",
                action="audit fixed source on target-derived stress panel",
                run_kind=KIND, run_config=config,
                extra={"candidate_path": str(candidate_path)})
            row = {
                "candidate_path": str(candidate_path),
                "source_sha256": _sha(source),
                "attempt": repair._attempt_summary(attempt),
            }
            if attempt.compiled:
                assembly = workspace.semantic_assembly(
                    (ws / f"{tag}_object_dump_normalized.s").read_text(
                        errors="replace"), ws / f"{tag}.o")
                results = differential.run_suite(
                    target_assembly, assembly, cases,
                    target_name=function, candidate_name=candidate_path.stem,
                    call_arities=call_arities,
                    return_registers=return_registers)
                row["differential"] = _summary(
                    results, panel_frozen_count,
                    target_program=differential.Program.parse(
                        function, target_assembly),
                    candidate_program=differential.Program.parse(
                        candidate_path.stem, assembly))
                row["semantic_certificate"] = repair.semantic_certificate(
                    target_assembly, assembly, source, cases, results,
                    call_arities=call_arities, return_registers=return_registers)
                row["value_distance"] = \
                    semantic_gradient.suite_value_distance(results)
                row["value_mismatches"] = [
                    semantic_gradient.value_mismatch_dict(mismatch)
                    for mismatch in
                    semantic_gradient.infer_value_mismatches(results)[:16]
                ]
            receipt["candidates"].append(row)
            _atomic_json(output, receipt)
    finally:
        conn.close()
    receipt["status"] = "complete"
    receipt["completed_at"] = int(time.time())
    _atomic_json(output, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument("--max-cases", type=int, default=512)
    parser.add_argument(
        "--panel-receipt", type=Path,
        help="reuse the target-only stress_panel from an m2c semantic-seed "
             "receipt instead of rebuilding a weaker panel from the census")
    args = parser.parse_args()
    panel_cases = None
    frozen_count = None
    panel_metadata = None
    if args.panel_receipt:
        seed_receipt = json.loads(
            args.panel_receipt.expanduser().resolve().read_text(
                encoding="utf-8"))
        panel_metadata = seed_receipt.get("stress_panel") or {}
        panel_cases = _cases_from_rows(
            panel_metadata.get("selected_cases") or [])
        if not panel_cases:
            raise ValueError("panel receipt contains no stress-panel cases")
        frozen_count = int(
            (seed_receipt.get("coverage_exploration") or {}).get(
                "selected_case_count") or len(panel_cases))
    result = run(
        repo=args.repo, db=args.db, census_path=args.census,
        output=args.output, function=args.function,
        candidate_paths=tuple(args.candidate), max_cases=args.max_cases,
        panel_cases=panel_cases, frozen_count=frozen_count,
        panel_metadata=panel_metadata)
    print(json.dumps({
        "status": result["status"],
        "panel_cases": result["panel"]["selected_case_count"],
        "candidates": [
            {
                "path": row["candidate_path"],
                "compiled": row["attempt"]["compiled"],
                "exact": row["attempt"]["exact"],
                "differential": row.get("differential"),
            }
            for row in result["candidates"]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
