#!/usr/bin/env python3
"""Preserve m2c logic while making a target-derived semantic seed compile."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import differential_repair_pilot as repair
from solver import m2c_adapter, mips_differential, workspace


KIND = "m2c-semantic-seed"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _json(path: Path, value: dict) -> None:
    _write(path, json.dumps(value, indent=2) + "\n")


def _node(census: dict, function: str) -> dict:
    rows = [row for row in census.get("dag", {}).get("nodes", [])
            if row.get("function") == function]
    if len(rows) != 1:
        raise ValueError(
            f"expected one census node for {function}, found {len(rows)}")
    return rows[0]


def _cases_from_node(node: dict) -> tuple[mips_differential.TestCase, ...]:
    rows = (node.get("candidate_exploration") or {}).get(
        "selected_cases") or []
    return tuple(mips_differential.TestCase(
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


def _differential(rows: list, frozen_count: int, *,
                  target_program=None, candidate_program=None) -> dict:
    frozen = rows[:frozen_count]
    holdout = rows[frozen_count:]

    def counts(panel: list) -> dict:
        return {status: sum(row.status == status for row in panel)
                for status in ("passed", "failed", "inconclusive")}

    result = {
        "all": counts(rows),
        "frozen": counts(frozen),
        "holdout": counts(holdout),
        "first_failures": [
            {"case": row.case, "status": row.status,
             "reasons": list(row.reasons),
             "first_divergence": row.first_divergence,
             "target_execution": {
                 "status": row.target.status,
                 "error": row.target.error,
                 "instruction_count": row.target.instruction_count,
             },
             "candidate_execution": {
                 "status": row.candidate.status,
                 "error": row.candidate.error,
                 "instruction_count": row.candidate.instruction_count,
             }}
            for row in rows if row.status != "passed"
        ][:16],
    }
    if target_program is not None:
        result["target_coverage"] = mips_differential.coverage_report(
            target_program, [row.target for row in rows]).to_dict()
    if candidate_program is not None:
        result["candidate_coverage"] = mips_differential.coverage_report(
            candidate_program, [row.candidate for row in rows]).to_dict()
    return result


def run(*, repo: Path, db: Path, census_path: Path, function: str,
        output: Path, best_source_out: Path,
        stress_cases: int = 512,
        coverage_search_cases: int = 5000,
        max_steps: int = 2_000,
        source_parent_attempt_id: int | None = None) -> dict:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    node = _node(census, function)
    frozen_cases = _cases_from_node(node)
    parent_attempt_id = (source_parent_attempt_id
                         if source_parent_attempt_id is not None else
                         node.get("attempt", {}).get("attempt_id"))
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
    raw_target = workspace.target_asm(ws, function)
    target = workspace.semantic_assembly(
        (ws / "target_object_dump_normalized.s").read_text(
            errors="replace"), ws / "target.o")
    target_program = mips_differential.Program.parse(function, target)
    draft = workspace.m2c_draft(ws)
    if not draft.strip():
        raise ValueError(f"m2c produced no draft for {function}")
    exploration = mips_differential.explore_coverage(
        target, frozen_cases, target_name=function,
        call_arities=call_arities, return_registers=return_registers,
        mutable_entry_registers=mutable_registers,
        pointer_entry_registers=pointer_registers,
        max_cases=coverage_search_cases, max_steps=max_steps)
    panel = mips_differential.build_semantic_stress_panel(
        target, exploration.cases, target_name=function,
        call_arities=call_arities, return_registers=return_registers,
        mutable_entry_registers=mutable_registers,
        pointer_entry_registers=pointer_registers,
        max_cases=max(stress_cases, len(exploration.cases)),
        max_steps=max_steps)
    run_id = f"m2c-semantic-seed-{time.time_ns()}-{function}"
    config = {
        "kind": KIND,
        "function": function,
        "stress_cases": stress_cases,
        "coverage_search_cases": coverage_search_cases,
        "max_steps_per_case": max_steps,
        "model_calls": 0,
        "target_source_used": False,
        "source_parent_attempt_id": parent_attempt_id,
        "selection": (
            "preserve m2c control/data flow; resolve only linker-backed "
            "absolute unknowns and add project header context; verify all "
            "target-only stress cases"),
    }
    receipt = {
        "schema_version": 1,
        "kind": KIND,
        "run_id": run_id,
        "created_at": int(time.time()),
        "status": "running",
        "config": config,
        "inputs": {
            "census": str(census_path),
            "m2c_source_sha256": _sha(draft),
        },
        "stress_panel": panel.to_dict(),
        "coverage_exploration": exploration.to_dict(),
        "variants": [],
    }
    _json(output, receipt)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    best = None
    try:
        variants = m2c_adapter.evidence_global_variants(
            conn, repo, function,
            m2c_adapter.variants(repo, function, raw_target, draft))
        for index, variant in enumerate(variants):
            tag = f"{function}_m2c_semantic_seed_{index}_{time.time_ns()}"
            attempt = workspace.score(
                ws, repo, tag, variant.source, conn=conn, func=function,
                strategy=(f"{KIND}:project-header" if "project" in variant.label else KIND),
                model="", run_id=run_id,
                parent_attempt_id=parent_attempt_id,
                relation="m2c-logic-preserving-adaptation",
                action=variant.label, run_kind=KIND, run_config=config,
                extra={"resolved_absolute_symbols": list(
                    variant.resolved_absolute_symbols),
                       "context_receipts": variant.context_receipts})
            row = {
                "label": variant.label,
                "source_sha256": _sha(variant.source),
                "resolved_absolute_symbols": [
                    {"name": name, "value": value}
                    for name, value in variant.resolved_absolute_symbols],
                "declared_globals": list(variant.declared_globals),
                "attempt": repair._attempt_summary(attempt),
            }
            state = None
            if attempt.compiled:
                candidate = workspace.semantic_assembly(
                    (ws / f"{tag}_object_dump_normalized.s").read_text(
                        errors="replace"), ws / f"{tag}.o")
                results = mips_differential.run_suite(
                    target, candidate, panel.cases,
                    target_name=function, candidate_name=variant.label,
                    call_arities=call_arities,
                    return_registers=return_registers)
                candidate_program = mips_differential.Program.parse(
                    variant.label, candidate)
                row["differential"] = _differential(
                    results, len(frozen_cases),
                    target_program=target_program,
                    candidate_program=candidate_program)
                row["behavior_key"] = list(
                    repair.behavior_key(results, attempt))
                state = (repair.behavior_key(results, attempt),
                         variant.source, attempt, results, row)
            receipt["variants"].append(row)
            if state is not None and (best is None or state[0] > best[0]):
                best = state
                _write(best_source_out, variant.source)
            _json(output, receipt)
    finally:
        conn.close()
    if best is None:
        result = {"compiled": False, "exact": False}
    else:
        result = {
            "compiled": True,
            "exact": bool(best[2].exact),
            "attempt": repair._attempt_summary(best[2]),
            "source_path": str(best_source_out),
            "source_sha256": _sha(best[1]),
            "behavior_key": list(best[0]),
            "differential": best[4]["differential"],
            "all_semantic_cases_passed": all(
                row.status == "passed" for row in best[3]),
        }
    receipt.update({
        "status": "complete",
        "completed_at": int(time.time()),
        "result": result,
    })
    _json(output, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--best-source-out", type=Path, required=True)
    parser.add_argument("--stress-cases", type=int, default=512)
    parser.add_argument("--coverage-search-cases", type=int, default=5000)
    parser.add_argument("--max-steps", type=int, default=2_000)
    args = parser.parse_args()
    result = run(
        repo=args.repo, db=args.db, census_path=args.census,
        function=args.function, output=args.output,
        best_source_out=args.best_source_out,
        stress_cases=args.stress_cases,
        coverage_search_cases=args.coverage_search_cases,
        max_steps=args.max_steps)
    print(json.dumps({
        "status": result["status"],
        "variants": len(result["variants"]),
        "result": result["result"],
    }, indent=2))


if __name__ == "__main__":
    main()
