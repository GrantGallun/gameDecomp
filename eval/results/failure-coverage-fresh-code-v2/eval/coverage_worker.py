"""Target-only coverage search followed by independent candidate replay."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from eval import semantic_stress_pilot as stress
from solver import mips_differential as differential, workspace


def run(*, repo: Path, db: Path, census_path: Path, output: Path,
        function: str, source_path: Path, source_parent_attempt_id: int,
        cases: tuple[differential.TestCase, ...] | None = None,
        max_cases: int = 512, stress_cases: int = 256,
        max_steps: int = 10000) -> dict:
    node = stress._node(json.loads(census_path.read_text(encoding="utf-8")), function)
    cases = cases or stress._cases_from_node(node)
    if max_cases < len(cases) or stress_cases < len(cases):
        raise ValueError("coverage budgets must retain every supplied regression case")
    abi = node.get("abi", {})
    arities = dict(abi.get("provisional_call_arities") or {})
    function_abi = abi.get("function") or {}
    returns = tuple(function_abi.get("return_registers") or ())
    mutable = tuple(function_abi.get("mutable_scalar_registers") or ())
    pointers = tuple(r for r in function_abi.get("pointer_registers", ()) if r != "a0")
    ws = workspace.bootstrap(repo, function)
    target = workspace.semantic_assembly(
        (ws / "target_object_dump_normalized.s").read_text(errors="replace"), ws / "target.o")
    exploration = differential.explore_coverage(
        target, cases, target_name=function, call_arities=arities,
        return_registers=returns, mutable_entry_registers=mutable,
        pointer_entry_registers=pointers, max_cases=max_cases, max_steps=max_steps)
    # Path witnesses alone miss value errors (masks, signs, overflow, aliases).
    panel = differential.build_semantic_stress_panel(
        target, exploration.cases, target_name=function, call_arities=arities,
        return_registers=returns, mutable_entry_registers=mutable,
        pointer_entry_registers=pointers,
        max_cases=max(stress_cases, len(exploration.cases)))
    result = stress.run(
        repo=repo, db=db, census_path=census_path, output=output,
        function=function, candidate_paths=(source_path,),
        source_parent_attempt_id=source_parent_attempt_id,
        panel_cases=panel.cases, frozen_count=len(cases),
        panel_metadata=panel.to_dict())
    result["coverage_worker"] = {
        "schema_version": 1, "target_only_selection": True, "model_calls": 0,
        "target_assembly_sha256": stress._sha(target),
        "max_cases": max_cases, "max_steps": max_steps,
        "exploration": exploration.to_dict(),
        "regression_cases": len(cases),
        "selected_cases": [asdict(case) for case in panel.cases],
    }
    stress._atomic_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("repo", "db", "census", "output", "source"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--source-parent-attempt-id", type=int, required=True)
    parser.add_argument("--max-cases", type=int, default=512)
    parser.add_argument("--stress-cases", type=int, default=256)
    args = parser.parse_args()
    result = run(repo=args.repo, db=args.db, census_path=args.census,
                 output=args.output, function=args.function, source_path=args.source,
                 source_parent_attempt_id=args.source_parent_attempt_id,
                 max_cases=args.max_cases, stress_cases=args.stress_cases)
    checked = result["candidates"][0].get("differential", {})
    coverage = checked.get("target_coverage", {})
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "differential": checked.get("all"),
                      "target_coverage": coverage.get("status"),
                      "target_instructions": coverage.get("covered_instruction_count"),
                      "target_instruction_total": coverage.get("reachable_instruction_count")}))


if __name__ == "__main__":
    main()
