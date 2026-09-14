#!/usr/bin/env python3
"""Compile-and-rank typed source rebindings from differential evidence.

This is a zero-LLM experiment.  It starts from a frozen differential repair
receipt, maps the next dynamic memory divergence back to candidate C access
sites, enumerates bounded typed rebindings, and retains a child only when the
ordinary semantic suite improves without regressing its verified prefix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import differential_repair_pilot as repair
from eval import differential_wavefront
from solver import semantic_gradient, workspace


SCHEMA_VERSION = 1
KIND = "typed-semantic-gradient-pilot"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _node(census: dict, function: str) -> dict:
    matches = [row for row in census.get("dag", {}).get("nodes", [])
               if row.get("function") == function]
    if len(matches) != 1:
        raise ValueError(
            f"expected one census node for {function}, found {len(matches)}")
    return matches[0]


def _semantic_progress_key(results, attempt) -> tuple:
    """Logic-first ordering; static similarity cannot break a failed tie."""
    key = repair.behavior_key(results, attempt)
    return (key[0], key[1], key[2], key[3], key[5], key[4])


def run(*, repo: Path, db: Path, census_path: Path, parent_receipt: Path,
        output: Path, best_source_out: Path, function: str,
        max_waves: int = 4, max_variants: int = 32) -> dict:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    node = _node(census, function)
    cases = differential_wavefront.cases_from_node(node)
    call_arities = {
        str(name): int(arity) for name, arity in
        (node.get("abi", {}).get("provisional_call_arities") or {}).items()
    }
    return_registers = tuple(str(name) for name in
        (node.get("abi", {}).get("function", {}).get("return_registers") or []))
    parent = json.loads(parent_receipt.read_text(encoding="utf-8"))
    if parent.get("config", {}).get("function") != function:
        raise ValueError("parent receipt function does not match requested function")
    source_path = Path(parent["result"]["best_source_path"])
    source = source_path.read_text(encoding="utf-8")
    parent_attempt_id = int(parent["result"]["best_attempt"]["attempt_id"])

    ws = workspace.bootstrap(repo, function)
    target_assembly = (ws / "target_object_dump_normalized.s").read_text(
        errors="replace")
    run_id = f"semantic-gradient-{time.time_ns()}-{function}"
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "function": function,
        "max_waves": max_waves,
        "max_variants_per_wave": max_variants,
        "selection": (
            "verified semantic prefix and semantic observables only; neutral "
            "causal choices require a beam; byte score is excluded until pass"),
        "target_source_used": False,
        "model_calls": 0,
    }
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    try:
        root_tag = f"{function}_semantic_gradient_root_{time.time_ns()}"
        active_attempt = workspace.score(
            ws, repo, root_tag, source, conn=conn, func=function,
            strategy="semantic-gradient-root", model="", run_id=run_id,
            parent_attempt_id=parent_attempt_id,
            relation="semantic-gradient-root",
            action="fresh verification of frozen parent best",
            run_kind=KIND, run_config=config)
        if not active_attempt.compiled:
            raise RuntimeError(
                f"semantic-gradient root stopped compiling: "
                f"{active_attempt.compiler_stderr}")
        active_source = source
        active_assembly = (ws / f"{root_tag}_object_dump_normalized.s").read_text(
            errors="replace")
        active_results = repair._evaluate(
            target_assembly, active_assembly, cases=cases,
            call_arities=call_arities, return_registers=return_registers)
        seen = {_sha(source)}
        rows: list[dict] = []
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "run_id": run_id,
            "created_at": int(time.time()),
            "status": "running",
            "config": config,
            "inputs": {
                "census": str(census_path),
                "parent_receipt": str(parent_receipt),
                "parent_attempt_id": parent_attempt_id,
                "source_sha256": _sha(source),
            },
            "root": {
                "attempt": repair._attempt_summary(active_attempt),
                "behavior_key": list(repair.behavior_key(
                    active_results, active_attempt)),
                "differential": repair._differential_summary(active_results),
            },
            "waves": rows,
        }
        _atomic_json(output, receipt)
        termination = "wave limit"
        for wave in range(1, max_waves + 1):
            if active_attempt.exact:
                termination = "byte exact"
                break
            if all(result.status == "passed" for result in active_results):
                termination = "all observed semantic cases pass"
                break
            constraints = semantic_gradient.infer_constraints(active_results)
            missing_reads = semantic_gradient.infer_missing_reads(active_results)
            value_mismatches = semantic_gradient.infer_value_mismatches(
                active_results)
            generated = list(semantic_gradient.rebinding_variants(
                active_source, constraints, max_variants=max_variants))
            output_location = semantic_gradient.divergent_output(active_results)
            if output_location is not None and len(generated) < max_variants:
                generated.extend(semantic_gradient.dependency_injection_variants(
                    active_source, missing_reads, *output_location,
                    max_variants=max_variants - len(generated)))
            variants = []
            wave_seen: set[str] = set()
            for variant in generated:
                digest = _sha(variant.source)
                if digest in seen or digest in wave_seen:
                    continue
                wave_seen.add(digest)
                variants.append(variant)
            wave_row = {
                "wave": wave,
                "constraints": [row.__dict__ for row in constraints],
                "missing_reads": [row.__dict__ for row in missing_reads],
                "value_mismatches": [
                    semantic_gradient.value_mismatch_dict(row)
                    for row in value_mismatches],
                "value_distance": semantic_gradient.suite_value_distance(
                    active_results),
                "divergent_output": list(output_location)
                    if output_location is not None else None,
                "candidate_count": len(variants),
                "candidates": [],
                "accepted": None,
            }
            rows.append(wave_row)
            _atomic_json(output, receipt)
            if not variants:
                termination = "no actionable source access for divergence"
                break

            best = None
            active_key = _semantic_progress_key(active_results, active_attempt)
            for index, variant in enumerate(variants, 1):
                seen.add(_sha(variant.source))
                tag = (f"{function}_semantic_gradient_{wave}_{index}_"
                       f"{time.time_ns()}")
                started = time.time()
                child = workspace.score(
                    ws, repo, tag, variant.source, conn=conn, func=function,
                    strategy="typed-semantic-gradient", model="", run_id=run_id,
                    iteration=wave, parent_attempt_id=active_attempt.receipt_id,
                    relation="typed-access-rebind", action=variant.label,
                    feedback="; ".join(row.evidence for row in constraints),
                    run_kind=KIND, run_config=config,
                    extra={"wave": wave, "variant": index})
                candidate_row = {
                    "variant": index,
                    "label": variant.label,
                    "source_sha256": _sha(variant.source),
                    "attempt": repair._attempt_summary(child),
                    "wall_ms": int((time.time() - started) * 1000),
                    "accepted_for_next_wave": False,
                }
                wave_row["candidates"].append(candidate_row)
                if child.compiled:
                    assembly = (ws / f"{tag}_object_dump_normalized.s").read_text(
                        errors="replace")
                    results = repair._evaluate(
                        target_assembly, assembly, cases=cases,
                        call_arities=call_arities,
                        return_registers=return_registers)
                    behavior = repair.behavior_key(results, child)
                    key = _semantic_progress_key(results, child)
                    prefix = repair.preserves_verified_prefix(
                        active_results, results)
                    candidate_row.update({
                        "behavior_key": list(behavior),
                        "semantic_progress_key": list(key),
                        "verified_prefix_preserved": prefix,
                        "differential": repair._differential_summary(results),
                    })
                    if prefix and key > active_key and (
                            best is None or key > best[0]):
                        best = (key, child, variant.source, results, index)
                _atomic_json(output, receipt)

            if best is None:
                termination = "typed semantic gradient stalled"
                break
            _key, active_attempt, active_source, active_results, index = best
            wave_row["accepted"] = index
            wave_row["candidates"][index - 1]["accepted_for_next_wave"] = True
            _atomic_text(best_source_out, active_source)
            _atomic_json(output, receipt)
        else:
            termination = "wave limit"

        _atomic_text(best_source_out, active_source)
        receipt.update({
            "status": "complete",
            "termination_reason": termination,
            "completed_at": int(time.time()),
            "result": {
                "best_attempt": repair._attempt_summary(active_attempt),
                "best_source_path": str(best_source_out),
                "best_source_sha256": _sha(active_source),
                "behavior_key": list(repair.behavior_key(
                    active_results, active_attempt)),
                "differential": repair._differential_summary(active_results),
                "semantic_cases_passed": sum(
                    row.status == "passed" for row in active_results),
                "all_semantic_cases_passed": all(
                    row.status == "passed" for row in active_results),
                "exact": bool(active_attempt.exact),
            },
        })
        _atomic_json(output, receipt)
        return receipt
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--parent-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--best-source-out", type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--max-waves", type=int, default=4)
    parser.add_argument("--max-variants", type=int, default=32)
    args = parser.parse_args()
    result = run(
        repo=args.repo, db=args.db, census_path=args.census,
        parent_receipt=args.parent_receipt, output=args.output,
        best_source_out=args.best_source_out, function=args.function,
        max_waves=args.max_waves, max_variants=args.max_variants)
    print(json.dumps({
        "status": result["status"],
        "termination_reason": result["termination_reason"],
        "best_attempt": result["result"]["best_attempt"],
        "semantic_cases_passed": result["result"]["semantic_cases_passed"],
        "all_semantic_cases_passed": result["result"][
            "all_semantic_cases_passed"],
        "exact": result["result"]["exact"],
    }, indent=2))


if __name__ == "__main__":
    main()
