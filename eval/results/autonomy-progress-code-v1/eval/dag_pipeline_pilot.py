#!/usr/bin/env python3
"""Run the staged differential pipeline over a frozen callgraph DAG panel.

This is a zero-generation census.  It recompiles every frozen root, executes
the coverage/semantic stages where the current MIPS runner is capable, and
records the first honest stopping stage for each function.  Non-leaf results
remain provisional until exact callee execution or side-effect models exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from eval import agentrepair, callgraph, callgraph_shape, logic_first, matched
from solver import (cfg, logic, evidence_schedule, mips_differential as differential,
                    project_headers, refine, residual, workspace)


SCHEMA_VERSION = 1


def _split_parameters(text: str) -> list[str]:
    text = text.strip()
    if not text or text == "void":
        return []
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        current.append(char)
    if current:
        result.append("".join(current).strip())
    return result


def _binary_register_destination(instruction) -> str | None:
    destination = differential._written_register(instruction)
    if destination is not None:
        return destination
    operands = instruction.operands
    if not operands:
        return None
    if instruction.opcode in {"mtc1", "ctc1"} and len(operands) == 2:
        return operands[1].strip().lstrip("$")
    if (instruction.opcode.startswith(("cvt.", "mov.", "add.", "sub.",
                                       "mul.", "div.", "neg.", "abs."))):
        return operands[0].strip().lstrip("$")
    return None


def _binary_abi_info(function: str, assembly: str) -> dict:
    """Infer a diagnostic entry/return register panel from object code.

    This is deliberately not promoted to authoritative type evidence.  It
    lets static/no-header functions reach the differential debugger while the
    receipt continues to say ``known=false`` and ``source=binary-inferred``.
    """
    try:
        program = differential.Program.parse(function, assembly)
    except (ValueError, differential.UnsupportedInstruction) as exc:
        return {
            "known": False, "source": "unavailable", "prototype": "",
            "parameters": [], "arity": None, "return_registers": [],
            "mutable_scalar_registers": [], "pointer_registers": [],
            "floating_entry_pairs": [],
            "issues": [f"project header missing and binary ABI inference failed: {exc}"],
        }

    written: set[str] = set()
    entry_general: set[str] = set()
    pointer_registers: set[str] = set()
    floating_entry: set[str] = set()
    register_pattern = re.compile(r"(?<![\w$])\$?(a[0-3]|f(?:1[2-5]))\b")
    pointer_pattern = re.compile(r"\([^)]*\b(a[0-3])\s*\)")
    for instruction in program.instructions:
        destination = _binary_register_destination(instruction)
        for operand in instruction.operands:
            for register in register_pattern.findall(operand):
                if register not in written:
                    if register.startswith("a"):
                        entry_general.add(register)
                    else:
                        floating_entry.add(register)
            for register in pointer_pattern.findall(operand):
                if register not in written:
                    pointer_registers.add(register)
        if destination:
            written.add(destination)
            if destination.startswith("f") and \
                    instruction.opcode.endswith(".d"):
                written.add(f"f{int(destination[1:]) + 1}")

    return_registers: list[str] = []
    for terminal in (row for row in program.instructions
                     if row.opcode == "jr" and row.operands and
                     row.operands[0].strip().lstrip("$") == "ra"):
        candidates = []
        if terminal.index + 1 < len(program.instructions):
            candidates.append(program.instructions[terminal.index + 1])
        for prior in reversed(program.instructions[:terminal.index]):
            if cfg.has_delay_slot(prior.opcode):
                break
            if prior.opcode == "nop":
                continue
            # Only the immediately adjacent value producer is strong enough
            # return evidence without a source prototype.  A v0 literal used
            # much earlier as a store value or address is not thereby a C
            # return value merely because the register survives to `jr ra`.
            candidates.append(prior)
            if prior.opcode != "nop":
                break
        for candidate in candidates:
            destination = _binary_register_destination(candidate)
            if destination == "v0":
                return_registers = ["v0"]
                break
            if destination == "f0":
                return_registers = (["f0", "f1"]
                                    if candidate.opcode.endswith(".d") else
                                    ["f0"])
                break

    double_pairs = []
    for base in (12, 14):
        if (f"f{base}" in floating_entry or f"f{base + 1}" in floating_entry):
            uses_double = any(
                instruction.opcode.endswith(".d") and any(
                    operand.strip().lstrip("$") == f"f{base}"
                    for operand in instruction.operands[1:])
                for instruction in program.instructions)
            if uses_double:
                double_pairs.append(f"f{base}")

    pointer_registers &= entry_general
    scalar_registers = sorted(entry_general - pointer_registers)
    return {
        "known": False,
        "source": "binary-inferred",
        "prototype": "",
        "parameters": [f"binary entry register {name}"
                       for name in sorted(entry_general)],
        "arity": None,
        "return_registers": return_registers,
        "mutable_scalar_registers": scalar_registers,
        "pointer_registers": sorted(pointer_registers),
        "floating_entry_pairs": double_pairs,
        "issues": [],
    }


def prototype_info(repo: Path, function: str, assembly: str = "") -> dict:
    declarations = project_headers.declarations(repo, function)
    if not declarations:
        return (_binary_abi_info(function, assembly) if assembly else {
            "known": False, "source": "unavailable", "prototype": "",
            "parameters": [], "arity": None, "return_registers": [],
            "mutable_scalar_registers": [], "pointer_registers": [],
            "floating_entry_pairs": [],
            "issues": ["project header declaration not found"],
        })
    declaration = declarations[0]
    match = re.search(
        rf"^(.*?)\b{re.escape(function)}\s*\((.*)\)$",
        declaration.prototype)
    if not match:
        return {
            "known": False, "prototype": declaration.prototype,
            "parameters": [], "arity": None, "return_registers": [],
            "mutable_scalar_registers": [],
            "issues": ["project header declaration could not be parsed"],
        }
    return_type = match.group(1).strip()
    parameters = _split_parameters(match.group(2))
    issues: list[str] = []
    double_return = bool(re.search(r"\bdouble\b", return_type))
    floating_return = double_return or bool(re.search(r"\bfloat\b", return_type))
    return {
        "known": True,
        "source": "project-header",
        "header": declaration.include,
        "prototype": declaration.prototype,
        "parameters": parameters,
        "arity": len(parameters),
        "return_registers": (
            [] if re.search(r"\bvoid\s*$", return_type) else
            ["f0", "f1"] if double_return else
            ["f0"] if floating_return else ["v0"]),
        "mutable_scalar_registers": [
            f"a{index}" for index, parameter in enumerate(parameters[:4])
            if "*" not in parameter
        ],
        "pointer_registers": [
            f"a{index}" for index, parameter in enumerate(parameters[:4])
            if "*" in parameter
        ],
        "floating_entry_pairs": [],
        "issues": issues,
    }


def _call_contracts(repo: Path, calls: list[str]) -> tuple[dict[str, int], dict]:
    arities: dict[str, int] = {}
    contracts = {}
    for callee in calls:
        info = prototype_info(repo, callee)
        info["arity_known"] = False
        info["arity_source"] = "unresolved"
        contracts[callee] = info
        arity = info.get("arity")
        if isinstance(arity, int) and 0 <= arity <= 16:
            arities[callee] = arity
            info["arity_known"] = True
            info["arity_source"] = "project-header"
        elif callee in differential.MODE16_CALL_ARITIES:
            # These contracts were recovered and checked while building the
            # differential runner.  Treat them as explicit evidence rather
            # than silently falling back to four arguments.
            arities[callee] = differential.MODE16_CALL_ARITIES[callee]
            info["arity_known"] = True
            info["arity_source"] = "differential-runner-contract"
        else:
            # This permits a diagnostic execution, but the row remains
            # explicitly non-authoritative because the ABI is unresolved.
            arities[callee] = 4
    return arities, contracts


def _seed_cases(function: str, abi: dict) -> tuple[differential.TestCase, ...]:
    if function == "updateRacePlayerMode16AerialTrick":
        return differential.mode16_cases()
    values = (0, 1, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF)
    scalar = tuple(str(name) for name in abi["mutable_scalar_registers"])
    pointer_names = tuple(abi.get("pointer_registers", ()))
    pointers = tuple(
        (name, differential.ARG_POINTER_BASES[name])
        for name in pointer_names if name != "a0")
    floating_values = (0.0, 1.0, -1.0, 1.5, 65536.0)
    floating_pairs = tuple(abi.get("floating_entry_pairs", ()))
    return tuple(
        differential.TestCase(
            f"boundary-{index}", 0xD600 + index,
            entry_registers=(
                tuple((name, value) for name in scalar) + pointers +
                tuple(register_value
                      for base in floating_pairs
                      for register_value in (
                          (base, differential._float64_words(
                              floating_values[index])[0]),
                          (f"f{int(base[1:]) + 1}",
                           differential._float64_words(
                               floating_values[index])[1])))))
        for index, value in enumerate(values)
    )


def _run_statuses(runs: tuple[differential.RunResult, ...] | list) -> dict:
    counts = Counter(run.status for run in runs)
    errors = Counter(run.error for run in runs if run.error)
    return {
        "statuses": dict(sorted(counts.items())),
        "errors": [
            {"error": error, "count": count}
            for error, count in errors.most_common(8)
        ],
    }


def _differential_summary(rows: list[differential.DifferentialResult]) -> dict:
    counts = Counter(row.status for row in rows)
    return {
        "cases": len(rows),
        "statuses": dict(sorted(counts.items())),
        "first_failures": [
            {"case": row.case, "status": row.status,
             "first_divergence": row.first_divergence,
             "reasons": list(row.reasons)}
            for row in rows if row.status != "passed"
        ][:5],
    }


def _ordered_panel(manifest: dict) -> tuple[list[dict], list[dict]]:
    rows = {row["function"]: row for row in manifest["cluster"]}
    names = set(rows)
    dependencies = {name: set() for name in names}
    edges = []
    for edge in manifest.get("binary_call_edges", []):
        caller, callee = edge["caller"], edge["callee"]
        if caller in names and callee in names:
            dependencies[caller].add(callee)
            edges.append({"caller": caller, "callee": callee})
    levels, groups = evidence_schedule.levels(dependencies)
    ordered = []
    for name in sorted(names, key=lambda item: (
            levels.get(item, 0), rows[item].get("instruction_count") or 10**9,
            item)):
        ordered.append({
            **rows[name], "dag_level": levels.get(name, 0),
            "dependency_component": groups[name],
            "panel_dependencies": sorted(dependencies[name]),
        })
    return ordered, edges


def _stop_stage(*, attempt: workspace.Attempt,
                target_report: differential.CoverageReport | None,
                candidate_report: differential.CoverageReport | None,
                target_runs: tuple[differential.RunResult, ...],
                candidate_runs: list[differential.RunResult],
                rows: list[differential.DifferentialResult], calls: list[str],
                unresolved_contracts: list[str], input_issues: list[str],
                unsettled_callees: list[str]) -> tuple[str, str]:
    if not attempt.compiled:
        return "compilation", attempt.compiler_stderr[:800]
    if attempt.exact:
        return "complete", "byte oracle exact=true"
    if not any(run.status in differential.COMPLETED_STATUSES
               for run in target_runs):
        detail = next((run.error for run in target_runs if run.error),
                      "no target execution completed")
        return "target_execution", detail
    if input_issues:
        return "input_abi_model", "; ".join(input_issues)
    if unresolved_contracts:
        return "call_abi_contracts", (
            "unresolved call ABI: " + ", ".join(unresolved_contracts))
    if not any(run.status in differential.COMPLETED_STATUSES
               for run in candidate_runs):
        detail = next((run.error for run in candidate_runs if run.error),
                      "no candidate execution completed")
        return "candidate_execution", detail
    if rows and any(row.status == "failed" for row in rows):
        qualifier = "provisional non-leaf" if calls else "covered leaf"
        coverage = ("; target coverage remains partial" if
                    target_report is None or not target_report.complete else "")
        return "semantic_repair", f"{qualifier} behavior differs{coverage}"
    if target_report is None or not target_report.complete:
        return "target_coverage", (
            f"{len(target_report.missing_instructions) if target_report else '?'} "
            "instructions and "
            f"{len(target_report.unresolved_branch_edges) if target_report else '?'} "
            "conditional edges remain unresolved")
    if candidate_report is None or not candidate_report.complete:
        return "candidate_coverage", (
            f"{len(candidate_report.missing_instructions) if candidate_report else '?'} "
            "instructions and "
            f"{len(candidate_report.unresolved_branch_edges) if candidate_report else '?'} "
            "conditional edges remain unresolved")
    if calls:
        detail = (
            "observed cases pass with opaque call hooks, but real callee side "
            "effects are not executed"
            + ("; unsettled callees: " + ", ".join(unsettled_callees)
               if unsettled_callees else
               "; exact callees still need recursive execution wiring"))
        return "callee_side_effect_models", detail
    if rows and all(row.status == "passed" for row in rows):
        return "byte_exactness", "covered leaf semantics pass; object is non-exact"
    return "semantic_repair", "covered leaf behavior differs"


def _record_node(receipt: dict, node: dict, output: Path) -> None:
    receipt["dag"]["nodes"].append(node)
    nodes = receipt["dag"]["nodes"]
    receipt["aggregate"] = {
        "functions": len(nodes),
        "compiled": sum(bool(row["attempt"]["compiled"]) for row in nodes),
        "exact": sum(bool(row["attempt"]["exact"]) for row in nodes),
        "authoritative_semantic": sum(bool(row["semantic_authoritative"]) and
            (row["stop_stage"] == "byte_exactness" or bool(row["attempt"]["exact"])) for row in nodes),
        "authoritative_semantic_capable": sum(bool(row["semantic_authoritative"]) for row in nodes),
        "stop_stages": dict(sorted(Counter(row["stop_stage"] for row in nodes).items())),
    }
    agentrepair._atomic_json(output, receipt)


def run(*, repo: Path, db: Path, sets: Path, manifest_path: Path,
        output: Path, max_cases: int, max_steps: int = 2_000) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_digest") != logic_first._digest(manifest):
        raise ValueError("logic-first manifest digest is invalid")
    ordered, panel_edges = _ordered_panel(manifest)
    for frozen in ordered:
        agentrepair._refuse_frozen_heldout(sets, frozen["function"])

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    callees_by_name, _callers = callgraph.edges(conn)
    known_functions = {str(row[0]) for row in conn.execute(
        "SELECT name FROM functions")}
    settled = set(matched.already_matched(conn, repo))
    run_id = f"dag-pipeline-census-{time.time_ns()}"
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": "dag-ordered-differential-pipeline-census",
        "manifest": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "max_cases_per_side": max_cases,
        "max_steps_per_case": max_steps,
        "generation_tokens": 0,
        "order": "binary callgraph leaves to callers",
        "semantic_policy": (
            "authoritative only for fully covered leaves with known ABI; "
            "non-leaf execution is diagnostic until callee side effects run"),
        "case_selection_policy": (
            "target-only coverage exploration; candidate replays retained "
            "target witnesses without candidate-selected inputs"),
    }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": config["kind"],
        "run_id": run_id,
        "created_at": int(time.time()),
        "config": config,
        "dag": {"edges": panel_edges, "nodes": []},
    }

    try:
        for ordinal, frozen in enumerate(ordered):
            name = frozen["function"]
            source = agentrepair._source_for_attempt(
                conn, int(frozen["attempt_id"]), name)
            source_sha = hashlib.sha256(source.encode()).hexdigest()
            if source_sha != frozen["source_sha256"]:
                raise ValueError(f"frozen source hash changed for {name}")
            ws = workspace.bootstrap(repo, name)
            tag = f"{name}_dag_census_{time.time_ns()}"
            attempt = workspace.score(
                ws, repo, tag, source, conn=conn, func=name,
                strategy="dag-pipeline-census-root", model="zero-model",
                run_id=run_id, iteration=ordinal,
                parent_attempt_id=int(frozen["attempt_id"]),
                relation="dag-pipeline-frozen-root",
                action="fresh DAG-ordered pipeline census",
                run_kind=config["kind"], run_config=config)
            if not attempt.compiled:
                # On a fresh workspace, a failed C build may never have
                # produced either object dump. Compilation is already the
                # decisive stopping stage; do not turn it into a file error.
                _record_node(receipt, {
                    "function": name, "dag_level": frozen["dag_level"],
                    "dependency_component": frozen["dependency_component"],
                    "panel_dependencies": frozen["panel_dependencies"],
                    "all_binary_callees": sorted(callees_by_name.get(name, set())),
                    "source_attempt_id": int(frozen["attempt_id"]), "source_sha256": source_sha,
                    "attempt": {"attempt_id": attempt.receipt_id, "compiled": False,
                                "exact": False, "weighted_progress_score": attempt.score,
                                "compiler_stderr": attempt.compiler_stderr},
                    "semantic_authoritative": False, "differential": _differential_summary([]),
                    "combined_target_coverage": None, "combined_candidate_coverage": None,
                    "candidate_exploration": None, "target_exploration": None,
                    "stop_stage": "compilation", "stop_reason": attempt.compiler_stderr[:800],
                }, output)
                continue
            target_assembly = workspace.semantic_assembly(
                (ws / "target_object_dump_normalized.s").read_text(
                    errors="replace"), ws / "target.o")
            candidate_path = ws / f"{tag}_object_dump_normalized.s"
            candidate_assembly = (
                workspace.semantic_assembly(
                    candidate_path.read_text(errors="replace"),
                    ws / f"{tag}.o")
                if attempt.compiled and candidate_path.exists() else "")
            packet = residual.build(
                attempt, target_asm=workspace.target_asm(ws, name),
                target_object=ws / "target.o",
                candidate_object=(ws / f"{tag}.o") if attempt.compiled else None)
            assessment = logic.compare(
                target_assembly, candidate_assembly, attempt=attempt)

            calls = project_headers.called_functions(target_assembly)
            call_arities, call_contracts = _call_contracts(repo, calls)
            unresolved_contracts = [
                callee for callee, contract in call_contracts.items()
                if not contract["arity_known"]
            ]
            target_abi = prototype_info(repo, name, target_assembly)
            seed_cases = _seed_cases(name, target_abi)
            mutable = tuple(target_abi["mutable_scalar_registers"])
            pointers = tuple(
                str(name) for name in target_abi.get("pointer_registers", ())
                if name != "a0")
            target_exploration = differential.explore_coverage(
                target_assembly, seed_cases, target_name=name,
                call_arities=call_arities,
                return_registers=tuple(target_abi["return_registers"]),
                mutable_entry_registers=mutable,
                pointer_entry_registers=pointers, max_cases=max_cases,
                max_steps=max_steps)
            valid_target_cases = tuple(
                case for case, result in zip(
                    target_exploration.cases, target_exploration.runs)
                if result.status in differential.COMPLETED_STATUSES)

            candidate_exploration = None
            rows: list[differential.DifferentialResult] = []
            target_report = target_exploration.report
            candidate_report = None
            candidate_runs: list[differential.RunResult] = []
            if attempt.compiled and valid_target_cases:
                candidate_exploration = {
                    "selection": "target-led replay only",
                    "selected_case_count": len(valid_target_cases),
                    "candidate_selected_case_count": 0,
                    "selected_cases": [asdict(case)
                                       for case in valid_target_cases],
                }
                rows = differential.run_suite(
                    target_assembly, candidate_assembly,
                    valid_target_cases,
                    target_name=name, candidate_name=f"{name}-candidate",
                    call_arities=call_arities,
                    return_registers=tuple(target_abi["return_registers"]),
                    max_steps=max_steps)
                target_program = differential.Program.parse(name, target_assembly)
                candidate_program = differential.Program.parse(
                    f"{name}-candidate", candidate_assembly)
                target_report = differential.coverage_report(
                    target_program, [row.target for row in rows])
                candidate_runs = [row.candidate for row in rows]
                candidate_report = differential.coverage_report(
                    candidate_program, candidate_runs)

            direct_known_callees = {
                callee for callee in callees_by_name.get(name, set())
                if callee in known_functions
            }
            unsettled_callees = sorted(direct_known_callees - settled)
            stop_stage, stop_reason = _stop_stage(
                attempt=attempt, target_report=target_report,
                candidate_report=candidate_report,
                target_runs=target_exploration.runs,
                candidate_runs=candidate_runs, rows=rows, calls=calls,
                unresolved_contracts=unresolved_contracts,
                input_issues=list(target_abi["issues"]),
                unsettled_callees=unsettled_callees)
            semantic_authoritative = bool(
                not calls and target_abi["known"] and
                not target_abi["issues"] and target_report.complete and
                candidate_report is not None and candidate_report.complete)
            if attempt.exact:
                settled.add(name)

            node = {
                "function": name,
                "dag_level": frozen["dag_level"],
                "dependency_component": frozen["dependency_component"],
                "panel_dependencies": frozen["panel_dependencies"],
                "all_binary_callees": sorted(callees_by_name.get(name, set())),
                "unsettled_callees": unsettled_callees,
                "source_attempt_id": int(frozen["attempt_id"]),
                "source_sha256": source_sha,
                "attempt": {
                    "attempt_id": attempt.receipt_id,
                    "compiled": attempt.compiled,
                    "exact": attempt.exact,
                    "weighted_progress_score": attempt.score,
                    "residual": packet.to_dict(),
                },
                "static_assessment": assessment.to_dict(),
                "abi": {
                    "function": target_abi,
                    "calls": call_contracts,
                    "provisional_call_arities": call_arities,
                },
                "target_exploration": target_exploration.to_dict(),
                "target_execution": _run_statuses(target_exploration.runs),
                "candidate_exploration": (
                    candidate_exploration if candidate_exploration else None),
                "candidate_execution": _run_statuses(candidate_runs),
                "combined_target_coverage": target_report.to_dict(),
                "combined_candidate_coverage": (
                    candidate_report.to_dict() if candidate_report else None),
                "differential": _differential_summary(rows),
                "semantic_authoritative": semantic_authoritative,
                "stop_stage": stop_stage,
                "stop_reason": stop_reason,
            }
            _record_node(receipt, node, output)
    finally:
        conn.close()
    receipt["completed_at"] = int(time.time())
    agentrepair._atomic_json(output, receipt)
    return receipt


def audit(*, db: Path, sets: Path, receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    run_id = str(receipt.get("run_id") or "")
    conn = sqlite3.connect(db, timeout=120)
    rows = conn.execute(
        "SELECT a.id,f.name,a.parent_attempt_id FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr WHERE a.run_id=? "
        "ORDER BY a.id", (run_id,)).fetchall()
    edges = conn.execute(
        "SELECT COUNT(*) FROM attempt_edges e JOIN attempts a "
        "ON a.id=e.child_attempt_id WHERE a.run_id=?", (run_id,)).fetchone()[0]
    conn.close()
    names = {str(row[1]) for row in rows}
    receipt_names = {str(row["function"])
                     for row in receipt.get("dag", {}).get("nodes", [])}
    heldout = logic_first._heldout(sets)
    return {
        "run_id": run_id,
        "attempts": len(rows),
        "lineage_edges": int(edges),
        "all_attempts_have_parents": all(row[2] is not None for row in rows),
        "receipt_names_match_database": names == receipt_names,
        "heldout_overlap": sorted(names & heldout),
        "clean": (len(rows) == len(receipt_names) == int(edges)
                  and all(row[2] is not None for row in rows)
                  and names == receipt_names and not names & heldout),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--repo", type=Path,
                            default=Path.home() / "decomp/sbk1")
    run_parser.add_argument("--db", type=Path,
                            default=Path.home() / "decomp/kb-sbk1.sqlite")
    run_parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--max-cases", type=int, default=1_000)
    run_parser.add_argument("--max-steps", type=int, default=2_000)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--db", type=Path,
                              default=Path.home() / "decomp/kb-sbk1.sqlite")
    audit_parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    audit_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = run(
            repo=args.repo, db=args.db, sets=args.sets,
            manifest_path=args.manifest, output=args.output,
            max_cases=args.max_cases, max_steps=args.max_steps)
        print(json.dumps({
            "run_id": result["run_id"],
            "aggregate": result.get("aggregate", {}),
            "nodes": [
                {"function": row["function"], "level": row["dag_level"],
                 "score": row["attempt"]["weighted_progress_score"],
                 "stop_stage": row["stop_stage"],
                 "reason": row["stop_reason"]}
                for row in result["dag"]["nodes"]
            ],
            "output": str(args.output),
        }, indent=2))
    else:
        print(json.dumps(audit(
            db=args.db, sets=args.sets, receipt_path=args.receipt), indent=2))


if __name__ == "__main__":
    main()
