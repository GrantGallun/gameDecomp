#!/usr/bin/env python3
"""Zero-LLM beam search over typed dynamic semantic gradients.

Unlike the greedy pilot, this controller may retain a bounded source edit that
improves dynamic value/load alignment without yet changing the final observable.
Such a state is labelled a causal frontier member, never a semantic improvement.
Every child still compiles and passes the ordinary prefix non-regression gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import differential_repair_pilot as repair
from eval import differential_wavefront
from solver import mips_differential, semantic_gradient, workspace


SCHEMA_VERSION = 1
KIND = "typed-semantic-gradient-beam"


@dataclass
class State:
    source: str
    attempt: workspace.Attempt
    results: list
    path: tuple[str, ...]


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


def _semantic_key(state: State) -> tuple:
    key = repair.behavior_key(state.results, state.attempt)
    return (key[0], key[1], key[2], key[3], key[5], key[4])


def _load_gap(results: list) -> int:
    return sum(
        len(target) + len(candidate)
        for result in results
        for target, candidate in [semantic_gradient.phase_load_delta(result)]
    )


def _causal_key(state: State) -> tuple:
    behavior = repair.behavior_key(state.results, state.attempt)
    return (
        behavior[0], behavior[1], behavior[2], behavior[3], behavior[5],
        -semantic_gradient.suite_value_distance(state.results),
        -_load_gap(state.results), behavior[4],
    )


def _slim_differential(results: list, frozen_count: int = 0) -> dict:
    summary = {
        "passed": sum(row.status == "passed" for row in results),
        "failed": sum(row.status == "failed" for row in results),
        "inconclusive": sum(row.status == "inconclusive" for row in results),
        "first_divergences": [
            row.first_divergence for row in results
            if row.status != "passed"
        ][:16],
    }
    if frozen_count and frozen_count < len(results):
        frozen = results[:frozen_count]
        holdout = results[frozen_count:]
        summary["frozen"] = {
            "passed": sum(row.status == "passed" for row in frozen),
            "failed": sum(row.status == "failed" for row in frozen),
            "inconclusive": sum(row.status == "inconclusive" for row in frozen),
        }
        summary["holdout"] = {
            "passed": sum(row.status == "passed" for row in holdout),
            "failed": sum(row.status == "failed" for row in holdout),
            "inconclusive": sum(row.status == "inconclusive" for row in holdout),
        }
    return summary


def _state_summary(state: State, frozen_count: int = 0) -> dict:
    mismatches = semantic_gradient.infer_value_mismatches(state.results)
    return {
        "attempt": repair._attempt_summary(state.attempt),
        "source_sha256": _sha(state.source),
        "semantic_key": list(_semantic_key(state)),
        "causal_key": list(_causal_key(state)),
        "value_distance": semantic_gradient.suite_value_distance(state.results),
        "load_gap": _load_gap(state.results),
        "path": list(state.path),
        "differential": _slim_differential(state.results, frozen_count),
        "value_mismatches": [
            semantic_gradient.value_mismatch_dict(row) for row in mismatches[:12]
        ],
    }


def _node(census: dict, function: str) -> dict:
    matches = [row for row in census.get("dag", {}).get("nodes", [])
               if row.get("function") == function]
    if len(matches) != 1:
        raise ValueError(
            f"expected one census node for {function}, found {len(matches)}")
    return matches[0]


def _top_states(states: list[State], width: int) -> list[State]:
    """Retain a deduplicated beam without holding every child's full traces."""
    ranked = sorted(
        states, key=lambda state: (
            _causal_key(state), _semantic_key(state),
            -len(state.path), _sha(state.source)), reverse=True)
    output = []
    seen = set()
    for state in ranked:
        digest = _sha(state.source)
        if digest in seen:
            continue
        seen.add(digest)
        output.append(state)
        if len(output) >= width:
            break
    return output


def _variants(state: State, maximum: int) -> tuple:
    constraints = semantic_gradient.infer_constraints(state.results)
    mismatches = semantic_gradient.infer_value_mismatches(state.results)
    missing = semantic_gradient.infer_missing_reads(state.results)
    output = semantic_gradient.divergent_output(state.results)
    generated = list(semantic_gradient.rebinding_variants(
        state.source, constraints, max_variants=min(16, maximum)))
    if output is not None and len(generated) < maximum:
        generated.extend(semantic_gradient.dag_guided_variants(
            state.source, mismatches, *output,
            max_variants=min(48, maximum - len(generated))))
    # The load-set fallback reaches stale/misbound leaves hidden below an
    # operation-shape mismatch. DAG-aligned choices remain ordered first.
    if output is not None and len(generated) < maximum:
        generated.extend(semantic_gradient.dependency_injection_variants(
            state.source, missing, *output,
            max_variants=maximum - len(generated)))
    unique = []
    seen = {state.source}
    for variant in generated:
        if variant.source in seen:
            continue
        seen.add(variant.source)
        unique.append(variant)
        if len(unique) >= maximum:
            break
    evidence = {
        "constraints": [row.__dict__ for row in constraints],
        "missing_reads": [row.__dict__ for row in missing],
        "value_mismatches": [
            semantic_gradient.value_mismatch_dict(row) for row in mismatches
        ],
        "divergent_output": list(output) if output is not None else None,
    }
    return tuple(unique), evidence


def run(*, repo: Path, db: Path, census_path: Path, parent_receipt: Path,
        output: Path, best_source_out: Path, frontier_source_out: Path,
        function: str, max_depth: int = 5, beam_width: int = 8,
        max_variants_per_state: int = 48, max_compiles: int = 320,
        stress_cases: int = 0) -> dict:
    census = json.loads(census_path.read_text(encoding="utf-8"))
    node = _node(census, function)
    frozen_cases = differential_wavefront.cases_from_node(node)
    call_arities = {
        str(name): int(arity) for name, arity in
        (node.get("abi", {}).get("provisional_call_arities") or {}).items()
    }
    return_registers = tuple(str(name) for name in
        (node.get("abi", {}).get("function", {}).get("return_registers") or []))
    parent = json.loads(parent_receipt.read_text(encoding="utf-8"))
    if parent.get("config", {}).get("function") != function:
        raise ValueError("parent receipt function does not match")
    source = Path(parent["result"]["best_source_path"]).read_text(
        encoding="utf-8")
    parent_attempt_id = int(parent["result"]["best_attempt"]["attempt_id"])
    ws = workspace.bootstrap(repo, function)
    target_assembly = (ws / "target_object_dump_normalized.s").read_text(
        errors="replace")
    stress_panel = None
    if stress_cases:
        mutable_registers = tuple(str(name) for name in
            (node.get("abi", {}).get("function", {}).get(
                "mutable_scalar_registers") or []))
        pointer_registers = tuple(
            str(name) for name in
            (node.get("abi", {}).get("function", {}).get(
                "pointer_registers") or []) if name != "a0")
        stress_panel = mips_differential.build_semantic_stress_panel(
            target_assembly, frozen_cases, target_name=function,
            call_arities=call_arities, return_registers=return_registers,
            mutable_entry_registers=mutable_registers,
            pointer_entry_registers=pointer_registers,
            max_cases=stress_cases)
        cases = stress_panel.cases
    else:
        cases = frozen_cases
    run_id = f"semantic-gradient-beam-{time.time_ns()}-{function}"
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "function": function,
        "max_depth": max_depth,
        "beam_width": beam_width,
        "max_variants_per_state": max_variants_per_state,
        "max_compiles": max_compiles,
        "stress_cases": stress_cases,
        "model_calls": 0,
        "target_source_used": False,
        "selection": (
            "full semantic prefix gate; retain bounded neutral children by "
            "dynamic value-DAG/load distance; report semantic best separately"),
    }
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    try:
        root_tag = f"{function}_semantic_gradient_beam_root_{time.time_ns()}"
        root_attempt = workspace.score(
            ws, repo, root_tag, source, conn=conn, func=function,
            strategy="semantic-gradient-beam-root", model="", run_id=run_id,
            parent_attempt_id=parent_attempt_id,
            relation="semantic-gradient-beam-root",
            action="fresh verification of frozen parent best",
            run_kind=KIND, run_config=config)
        if not root_attempt.compiled:
            raise RuntimeError(
                f"beam root stopped compiling: {root_attempt.compiler_stderr}")
        root_assembly = (ws / f"{root_tag}_object_dump_normalized.s").read_text(
            errors="replace")
        root_results = repair._evaluate(
            target_assembly, root_assembly, cases=cases,
            call_arities=call_arities, return_registers=return_registers)
        root = State(source, root_attempt, root_results, ())
        frontier = [root]
        best_semantic = root
        best_causal = root
        seen = {_sha(source)}
        compile_count = 0
        depths: list[dict] = []
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
            "root": _state_summary(root, len(frozen_cases)),
            "depths": depths,
        }
        if stress_panel is not None:
            receipt["stress_panel"] = stress_panel.to_dict()
        _atomic_json(output, receipt)
        termination = "depth limit"
        for depth in range(1, max_depth + 1):
            depth_row = {
                "depth": depth,
                "parents": [state.attempt.receipt_id for state in frontier],
                "evidence": [],
                "candidates": [],
                "frontier": [],
            }
            depths.append(depth_row)
            pool = list(frontier)
            accepted_child = False
            for parent_state in frontier:
                variants, evidence = _variants(
                    parent_state, max_variants_per_state)
                depth_row["evidence"].append({
                    "parent_attempt_id": parent_state.attempt.receipt_id,
                    **evidence,
                })
                feedback = "; ".join(
                    [row.get("evidence", "")
                     for row in evidence["constraints"]] +
                    [row.get("evidence", "")
                     for row in evidence["value_mismatches"]])
                for variant in variants:
                    if compile_count >= max_compiles:
                        break
                    digest = _sha(variant.source)
                    if digest in seen:
                        continue
                    seen.add(digest)
                    compile_count += 1
                    tag = (f"{function}_semantic_gradient_beam_{depth}_"
                           f"{compile_count}_{time.time_ns()}")
                    started = time.time()
                    child = workspace.score(
                        ws, repo, tag, variant.source, conn=conn,
                        func=function, strategy="typed-semantic-gradient-beam",
                        model="", run_id=run_id, iteration=depth,
                        parent_attempt_id=parent_state.attempt.receipt_id,
                        relation="typed-semantic-gradient-beam",
                        action=variant.label, feedback=feedback,
                        run_kind=KIND, run_config=config,
                        extra={"depth": depth, "compile": compile_count})
                    row = {
                        "parent_attempt_id": parent_state.attempt.receipt_id,
                        "attempt": repair._attempt_summary(child),
                        "source_sha256": digest,
                        "label": variant.label,
                        "wall_ms": int((time.time() - started) * 1000),
                        "prefix_preserved": False,
                        "selected_for_frontier": False,
                    }
                    depth_row["candidates"].append(row)
                    if child.compiled:
                        assembly = (
                            ws / f"{tag}_object_dump_normalized.s").read_text(
                                errors="replace")
                        results = repair._evaluate(
                            target_assembly, assembly, cases=cases,
                            call_arities=call_arities,
                            return_registers=return_registers)
                        prefix = repair.preserves_verified_prefix(
                            parent_state.results, results)
                        state = State(
                            variant.source, child, results,
                            parent_state.path + (variant.label,))
                        row.update({
                            "prefix_preserved": prefix,
                            "semantic_key": list(_semantic_key(state)),
                            "causal_key": list(_causal_key(state)),
                            "value_distance":
                                semantic_gradient.suite_value_distance(results),
                            "load_gap": _load_gap(results),
                            "differential": _slim_differential(
                                results, len(frozen_cases)),
                        })
                        if prefix:
                            accepted_child = True
                            pool.append(state)
                            # Differential results retain complete instruction
                            # traces.  Prune as we stream so stress panels do
                            # not multiply those traces by every child in a
                            # depth before the beam is finally truncated.
                            pool = _top_states(pool, beam_width)
                            if _semantic_key(state) > _semantic_key(best_semantic):
                                best_semantic = state
                                _atomic_text(best_source_out, state.source)
                            if _causal_key(state) > _causal_key(best_causal):
                                best_causal = state
                                _atomic_text(frontier_source_out, state.source)
                    _atomic_json(output, receipt)
                if compile_count >= max_compiles:
                    break

            if not accepted_child:
                termination = "no compiling prefix-preserving children"
                break
            next_frontier = _top_states(pool, beam_width)
            selected_ids = {state.attempt.receipt_id for state in next_frontier}
            for row in depth_row["candidates"]:
                row["selected_for_frontier"] = (
                    row["attempt"]["attempt_id"] in selected_ids)
            depth_row["frontier"] = [
                _state_summary(state, len(frozen_cases))
                for state in next_frontier]
            _atomic_json(output, receipt)
            frontier = next_frontier
            semantic_passes = [
                state for state in frontier
                if all(row.status == "passed" for row in state.results)
            ]
            if semantic_passes:
                best_semantic = max(semantic_passes, key=_semantic_key)
                best_causal = max(semantic_passes, key=_causal_key)
                termination = ("byte exact" if best_semantic.attempt.exact
                               else "all observed semantic cases pass")
                break
            if compile_count >= max_compiles:
                termination = "compile budget"
                break

        _atomic_text(best_source_out, best_semantic.source)
        _atomic_text(frontier_source_out, best_causal.source)
        receipt.update({
            "status": "complete",
            "termination_reason": termination,
            "completed_at": int(time.time()),
            "compile_count": compile_count,
            "result": {
                "semantic_best": _state_summary(
                    best_semantic, len(frozen_cases)),
                "semantic_best_source": str(best_source_out),
                "causal_frontier_best": _state_summary(
                    best_causal, len(frozen_cases)),
                "causal_frontier_source": str(frontier_source_out),
                "all_semantic_cases_passed": all(
                    row.status == "passed" for row in best_semantic.results),
                "exact": bool(best_semantic.attempt.exact),
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
    parser.add_argument("--frontier-source-out", type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-variants-per-state", type=int, default=48)
    parser.add_argument("--max-compiles", type=int, default=320)
    parser.add_argument("--stress-cases", type=int, default=0)
    args = parser.parse_args()
    result = run(
        repo=args.repo, db=args.db, census_path=args.census,
        parent_receipt=args.parent_receipt, output=args.output,
        best_source_out=args.best_source_out,
        frontier_source_out=args.frontier_source_out,
        function=args.function, max_depth=args.max_depth,
        beam_width=args.beam_width,
        max_variants_per_state=args.max_variants_per_state,
        max_compiles=args.max_compiles, stress_cases=args.stress_cases)
    print(json.dumps({
        "status": result["status"],
        "termination_reason": result["termination_reason"],
        "compile_count": result["compile_count"],
        "semantic_best": result["result"]["semantic_best"]["attempt"],
        "causal_frontier_best": result["result"][
            "causal_frontier_best"]["attempt"],
        "all_semantic_cases_passed": result["result"][
            "all_semantic_cases_passed"],
        "exact": result["result"]["exact"],
    }, indent=2))


if __name__ == "__main__":
    main()
