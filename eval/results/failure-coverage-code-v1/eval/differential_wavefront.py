#!/usr/bin/env python3
"""Run causal differential repair over a frozen callgraph wavefront.

The input is an audited ``dag_pipeline_pilot`` census. Noncompiling roots first
receive bounded, zero-model compile intake and a fresh census. Every non-exact
node with at least one conclusive differential case is dispatched leaves-to-callers
to the generic differential repair controller.  Complete semantic coverage is
not an admission requirement: incomplete coverage is carried as explicit debt
and prevents an over-strong success claim, while already observed divergences
can still be repaired.

This command is the orchestration seam between the callgraph DAG and the
per-function debugger/repair worker.  Child repair runs retain their own full
attempt lineage and receipts; this wave receipt records routing and outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from eval import (agentrepair, differential_repair_pilot as repair,
                  compile_intake,
                  coverage_worker,
                  m2c_semantic_seed as semantic_seed,
                  semantic_stress_pilot as semantic_stress)
from solver import (llm, mips_differential as differential,
                    transition_policy, evidence_schedule, callsite_contracts)


SCHEMA_VERSION = 2
CENSUS_KIND = "dag-ordered-differential-pipeline-census"
WAVE_KIND = "differential-debugger-wavefront"


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    if value.get("kind") == WAVE_KIND:
        value['integration_queue'] = []
        value['whole_rom_verified'] = False
        for row in value.get('nodes', []):
            if row.get('exact'):
                category = 'function_exact_pending_integration'
            elif row.get('status') == 'error':
                category = 'worker_error'
            elif row.get('route') == 'exactness_repair':
                category = 'byte_residual'
            elif row.get('route') == 'skip':
                category = row.get('census_stop_stage') or 'not_admitted'
            elif row.get('all_observed_semantic_cases_passed'):
                category = 'observed_semantic_pass_byte_residual'
            else:
                category = 'semantic_or_coverage_debt'
            row.setdefault('failure_category', category)
            verification = row.get('verification') or {}
            source_path = Path(row['best_source']) if row.get('best_source') else None
            if (row.get('exact') and verification.get('exact') and source_path
                    and source_path.is_file() and
                    hashlib.sha256(source_path.read_text(encoding='utf-8').encode()).hexdigest()
                    == verification.get('candidate_source_sha256')):
                value['integration_queue'].append({
                    'function': row['function'], 'attempt_id': row.get('best_attempt_id'),
                    'source': str(source_path), 'verification': verification,
                    'status': 'requires_prepared_translation_unit_and_isolated_rom_gate'})
        evidence = dict(value.get("inherited_evidence") or {})
        evidence.update({r["function"]: r for r in value.get("nodes", [])})
        stale = evidence_schedule.stale_nodes(evidence)
        value["dependency_revalidation_queue"] = stale
        for row in value.get("nodes", []):
            if row["function"] in stale:
                row["semantic_settled"] = False
                row["dependency_stale"] = True
        if "aggregate" in value:
            value["aggregate"]["semantic_settled"] = sum(
                bool(row.get("semantic_settled")) for row in value.get("nodes", []))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def cases_from_rows(rows: list[dict]) -> tuple[differential.TestCase, ...]:
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


def cases_from_node(node: dict) -> tuple[differential.TestCase, ...]:
    exploration = node.get("candidate_exploration") or {}
    return cases_from_rows(exploration.get("selected_cases") or [])


def route_node(node: dict) -> tuple[bool, str]:
    attempt = node.get("attempt") or {}
    if not attempt.get("compiled"):
        return False, "candidate does not compile"
    if attempt.get("exact"):
        return False, "already byte exact"
    cases = cases_from_node(node)
    if not cases:
        return False, "no conclusive differential cases"
    statuses = (node.get("differential") or {}).get("statuses") or {}
    if not (int(statuses.get("passed", 0)) or
            int(statuses.get("failed", 0))):
        return False, "all differential cases are inconclusive"
    return True, "differential repair eligible"


def repair_lane(node: dict, *, exactness_only: bool = False) -> tuple[str, str]:
    """Execution support gates debugger use, never the independent byte oracle."""
    eligible, reason = route_node(node)
    attempt = node.get("attempt") or {}
    if not attempt.get("compiled") or attempt.get("exact"):
        return "skip", reason
    if exactness_only or not eligible:
        return "exactness_repair", ("explicit compiler-only search" if exactness_only
                                    else reason + "; compiler-only search remains available")
    return "differential_repair", reason


def current_coverage(report: dict) -> dict:
    """Old completeness claims must be recomputed after coverage-model fixes."""
    if (report.get("status") == "complete" and
            report.get("coverage_model_version") != differential.COVERAGE_MODEL_VERSION):
        return {**report, "status": "partial", "prior_status": "complete",
                "stale_coverage": True,
                "revalidation_reason": "coverage model changed; regenerate census"}
    return report


def _configuration(*, census_path: Path, census_sha256: str, model: str,
                   rounds: int, timeout: int, think: str, num_thread: int,
                   temperature: float, diagnosis_num_predict: int,
                   patch_num_predict: int, patch_retries: int,
                   compiler_retries: int, max_stalls: int, seed: int,
                   max_functions: int | None, cache_dir: Path | None,
                   parent_wave_path: Path | None,
                   parent_wave_sha256: str | None,
                   functions: tuple[str, ...] | None,
                   m2c_preflight: bool, m2c_stress_cases: int,
                   m2c_coverage_search_cases: int,
                   m2c_max_steps: int) -> dict:
    return {
        "census": str(census_path),
        "census_sha256": census_sha256,
        "coverage_model_version": differential.COVERAGE_MODEL_VERSION,
        "repair_prompt_version": repair.PROMPT_VERSION,
        "coverage_worker_version": 1,
        "candidate_frontier_width": 4,
        "model": model,
        "rounds_per_function": rounds,
        "timeout_seconds": timeout,
        "think": think,
        "num_thread": num_thread,
        "temperature": temperature,
        "diagnosis_num_predict": diagnosis_num_predict,
        "patch_num_predict": patch_num_predict,
        "patch_retries": patch_retries,
        "compiler_retries": compiler_retries,
        "max_stalls": max_stalls,
        "seed": seed,
        "max_functions": max_functions,
        "cache_dir": str(cache_dir) if cache_dir else None,
        "parent_wave": str(parent_wave_path) if parent_wave_path else None,
        "parent_wave_sha256": parent_wave_sha256,
        "functions": list(functions) if functions else None,
        "m2c_semantic_preflight": m2c_preflight,
        "m2c_stress_cases": m2c_stress_cases,
        "m2c_coverage_search_cases": m2c_coverage_search_cases,
        "m2c_max_steps_per_case": m2c_max_steps,
    }


def run(*, repo: Path, db: Path, sets: Path, census_path: Path,
        output: Path, model: str, endpoint: str, rounds: int, timeout: int,
        think: str, num_thread: int, temperature: float,
        diagnosis_num_predict: int, patch_num_predict: int,
        patch_retries: int, compiler_retries: int, max_stalls: int,
        seed: int, cache_dir: Path | None, max_functions: int | None = None,
        resume: bool = False, parent_wave_path: Path | None = None,
        functions: tuple[str, ...] | None = None,
        m2c_preflight: bool = True, m2c_stress_cases: int = 256,
        m2c_coverage_search_cases: int = 5000,
        m2c_max_steps: int = 2_000, frozen_guard=None,
        proposal_cutoff_override: int | None = None,
        compiler_response_policy_override=None, deterministic_only: bool = False,
        exactness_only: bool = False) -> dict:
    if rounds < 0:
        raise ValueError("rounds must be nonnegative")
    census = json.loads(census_path.read_text(encoding="utf-8"))
    if census.get("kind") != CENSUS_KIND:
        raise ValueError(f"not a differential DAG census: {census_path}")
    # The census already freezes the wavefront's within-level priority
    # (smaller/easier functions first). Preserve it instead of re-sorting
    # alphabetically; only verify that dependency levels never go backwards.
    nodes = [
        {**node,
         "combined_target_coverage": current_coverage(
             node.get("combined_target_coverage") or {}),
         "combined_candidate_coverage": current_coverage(
             node.get("combined_candidate_coverage") or {})}
        for node in census.get("dag", {}).get("nodes", [])]
    if functions:
        available = {str(row.get("function")) for row in nodes}
        missing = sorted(set(functions) - available)
        if missing:
            raise ValueError(
                f"functions absent from census: {', '.join(missing)}")
        selected = set(functions)
        nodes = [row for row in nodes
                 if str(row.get("function")) in selected]
    levels = [int(row.get("dag_level") or 0) for row in nodes]
    if levels != sorted(levels):
        raise ValueError("census nodes are not ordered leaves-to-callers")
    for node in nodes:
        agentrepair._refuse_frozen_heldout(sets, str(node["function"]))

    parent_wave = None
    parent_nodes: dict[str, dict] = {}
    parent_wave_sha256 = None
    if parent_wave_path is not None:
        parent_wave = json.loads(parent_wave_path.read_text(encoding="utf-8"))
        if parent_wave.get("kind") != WAVE_KIND:
            raise ValueError(f"not a differential wave receipt: {parent_wave_path}")
        if parent_wave.get("source_census_run_id") != census.get("run_id"):
            raise ValueError("parent wave and census use different source runs")
        for parent_node in parent_wave.get("nodes", []):
            name = str(parent_node.get("function") or "")
            if name in parent_nodes:
                raise ValueError(f"duplicate parent-wave function: {name}")
            parent_nodes[name] = parent_node
        parent_wave_sha256 = _sha_file(parent_wave_path)

    census_sha256 = _sha_file(census_path)
    config = _configuration(
        census_path=census_path, census_sha256=census_sha256, model=model,
        rounds=rounds, timeout=timeout, think=think, num_thread=num_thread,
        temperature=temperature,
        diagnosis_num_predict=diagnosis_num_predict,
        patch_num_predict=patch_num_predict, patch_retries=patch_retries,
        compiler_retries=compiler_retries, max_stalls=max_stalls, seed=seed,
        max_functions=max_functions, cache_dir=cache_dir,
        parent_wave_path=parent_wave_path,
        parent_wave_sha256=parent_wave_sha256, functions=functions,
        m2c_preflight=m2c_preflight, m2c_stress_cases=m2c_stress_cases,
        m2c_coverage_search_cases=m2c_coverage_search_cases,
        m2c_max_steps=m2c_max_steps)
    config.update(exactness_only=exactness_only, deterministic_only=deterministic_only,
                  exactness_beam_width=4, exactness_model_call_limit=rounds)
    if resume:
        if not output.exists():
            raise ValueError(f"resume receipt does not exist: {output}")
        receipt = json.loads(output.read_text(encoding="utf-8"))
        if receipt.get("configuration") != config:
            raise ValueError("resume configuration does not match receipt")
    else:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "kind": WAVE_KIND,
            "run_id": f"differential-wavefront-{time.time_ns()}",
            "created_at": int(time.time()),
            "source_census_run_id": census.get("run_id"),
            "parent_wave_run_id": (
                parent_wave.get("run_id") if parent_wave else None),
            "configuration": config,
            "policy": {
                "order": "callgraph leaves to callers",
                "admission": (
                    "every compiled non-exact node; compiler-only fallback when execution is inconclusive"),
                "coverage": (
                    "incomplete coverage is retained as debt, not a repair "
                    "admission blocker or a semantic-pass claim"),
                "terminal_authority": "byte oracle exact=true",
                "seed_priority": (
                    "target-derived m2c logic plus deterministic compile "
                    "context before model repair"),
            },
            "nodes": [],
            "inherited_evidence": parent_nodes,
            "status": "running",
        }
        _atomic_json(output, receipt)

    completed = {str(row["function"]) for row in receipt["nodes"]}
    eligible_seen = 0
    artifact_dir = output.with_suffix("").with_name(
        output.stem + "-artifacts")
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    # Fit once before the wave creates new attempts.  Each child query still
    # excludes all transitions from that child's function.
    compiler_response_policy = (compiler_response_policy_override if
        compiler_response_policy_override is not None else
        transition_policy.TransitionPolicy.from_db(conn))
    proposal_cutoff = conn.execute("select coalesce(max(id),0) from model_proposals").fetchone()[0] \
        if conn.execute("select 1 from sqlite_master where name='model_proposals'").fetchone() else 0
    receipt["proposal_cutoff"] = proposal_cutoff
    if proposal_cutoff_override is not None:
        proposal_cutoff = proposal_cutoff_override
        receipt["proposal_cutoff"] = proposal_cutoff
    receipt["compiler_response_policy"] = \
        compiler_response_policy.summary()
    _atomic_json(output, receipt)
    try:
        for ordinal, node in enumerate(nodes):
            function = str(node["function"])
            if function in completed:
                continue
            if max_functions is not None and eligible_seen >= max_functions:
                break
            if frozen_guard is not None:
                frozen_guard()
            intake = None
            node_census_path = census_path
            if not (node.get("attempt") or {}).get("compiled"):
                intake_path = artifact_dir / f"{ordinal:03d}-{_slug(function)}.compile-intake.json"
                intake = compile_intake.run(
                    repo=repo, db=db, sets=sets, conn=conn, node=node,
                    output=intake_path, max_cases=m2c_stress_cases,
                    max_steps=m2c_max_steps)
                if intake.get("node"):
                    node = intake["node"]
                    node_census_path = Path(intake["census"])
                if frozen_guard is not None:
                    frozen_guard()
            lane, route_reason = repair_lane(node, exactness_only=exactness_only)
            if intake is None and (node.get('attempt') or {}).get('exact'):
                lane, route_reason = 'exactness_reverify', 'reverify census exact claim for integration handoff'
            eligible = lane != "skip"
            row = {
                "function": function,
                "dag_level": int(node.get("dag_level") or 0),
                "dependencies": list(node.get("panel_dependencies") or []),
                "census_attempt_id": node.get("attempt", {}).get("attempt_id"),
                "census_stop_stage": node.get("stop_stage"),
                "target_coverage_complete": bool(
                    (node.get("combined_target_coverage") or {}).get("status")
                    == "complete"),
                "candidate_coverage_complete": bool(
                    (node.get("combined_candidate_coverage") or {}).get("status")
                    == "complete"),
                "semantic_authoritative_at_census": bool(
                    node.get("semantic_authoritative")),
                "route": lane,
                "route_reason": route_reason,
            }
            if intake is not None:
                row["compile_intake"] = {"status": intake["status"],
                    "receipt": str(intake_path), "model_calls": 0,
                    "best_attempt_id": intake.get("best_attempt_id"),
                    "score": intake.get("score"), "exact": intake.get("exact", False),
                    "wall_seconds": intake.get("wall_seconds", 0),
                    "error": intake.get("error")}
            current_evidence = {**parent_nodes, **{r["function"]: r for r in receipt["nodes"]}}
            row["dependency_fingerprints"] = evidence_schedule.pins(row["dependencies"], current_evidence)
            if intake is not None and intake.get("exact"):
                row.update(route="compile_intake", status="complete", exact=True,
                           best_attempt_id=intake["best_attempt_id"], best_score=intake["score"],
                           child_run_id=intake["run_id"], child_receipt=str(intake_path),
                           charged_tokens=0, semantic_settled=False,
                           termination_reason="compile intake produced independently certified exact object")
                winner = next((a for a in intake.get('attempts', [])
                               if a.get('receipt_id') == intake['best_attempt_id']), {})
                if winner.get('verification'):
                    exact_source = agentrepair._source_for_attempt(conn, intake['best_attempt_id'], function)
                    exact_path = artifact_dir / f'{ordinal:03d}-{_slug(function)}.best.c'
                    _atomic_text(exact_path, exact_source)
                    row.update(best_source=str(exact_path), verification=winner['verification'])
                receipt["nodes"].append(row)
                _atomic_json(output, receipt)
                continue
            if not eligible:
                receipt["nodes"].append(row)
                _atomic_json(output, receipt)
                continue
            if max_functions is not None and eligible_seen >= max_functions:
                break
            eligible_seen += 1
            attempt_id = int(node["attempt"]["attempt_id"])
            source_origin = "census"
            parent_node = parent_nodes.get(function)
            if intake is None and parent_node and parent_node.get("best_attempt_id") is not None:
                attempt_id = int(parent_node["best_attempt_id"])
                source_origin = "parent-wave-best"
            row.update({
                "source_origin": source_origin,
                "source_attempt_id": attempt_id,
                "parent_wave_best_score": (
                    parent_node.get("best_score") if parent_node else None),
                "parent_wave_best_attempt_id": (
                    parent_node.get("best_attempt_id")
                    if parent_node else None),
            })
            source = agentrepair._source_for_attempt(conn, attempt_id, function)
            slug = f"{ordinal:03d}-{_slug(function)}"
            source_path = artifact_dir / f"{slug}.root.c"
            child_receipt = artifact_dir / f"{slug}.json"
            best_source = artifact_dir / f"{slug}.best.c"
            _atomic_text(source_path, source)
            if lane in {"exactness_repair", "exactness_reverify"}:
                started = time.time()
                # Do not attach the census's semantic results to a changed source.
                row.update(semantic_settled=False, all_observed_semantic_cases_passed=False,
                           target_coverage_complete=False, candidate_coverage_complete=False,
                           semantic_validation="not run on compiler-only result")
                try:
                    result = agentrepair.run(
                        repo=repo, db=db, function=function, source=source,
                        source_parent_attempt_id=attempt_id, out=child_receipt,
                        best_source_out=best_source, model=model, endpoint=endpoint,
                        draws=1, depth=rounds, beam=4,
                        max_calls=0 if deterministic_only or lane == 'exactness_reverify' else rounds,
                        timeout=timeout, think=think, num_thread=num_thread,
                        temperature=temperature, num_predict=diagnosis_num_predict,
                        seed=seed + ordinal * 1000, cache_dir=cache_dir, verbose=True,
                        retained_frontier=tuple((parent_node or {}).get("retained_frontier", [])),
                        deterministic_budget=32 if rounds > 0 and lane != 'exactness_reverify' else 0)
                    final = result["result"]
                    row.update(status="complete", child_run_id=result["run_id"],
                        child_receipt=str(child_receipt), best_source=str(best_source),
                        best_attempt_id=final["best_attempt_id"],
                        best_score=final["best_residual"]["weighted_progress_score"],
                        exact=final["exact"], verification=final.get("verification"),
                        retained_frontier=final.get("frontier", []),
                        residual=final["best_residual"],
                        iterations=final["calls_attempted"],
                        incomplete_responses=final.get("incomplete_responses", 0),
                        invalid_patch_rounds=final.get("invalid_proposals", 0),
                        deterministic_candidates=final.get('deterministic_candidates', 0),
                        best_score_improved=final.get('best_score_improved', False),
                        accepted_improvements=int(final.get('best_score_improved', False)),
                        compiled_candidate_rounds=final["compiling_children"] + final.get('deterministic_compiling_children', 0),
                        charged_tokens=final["charged_tokens"],
                        termination_reason="certified exact" if final["exact"] else "bounded search parked",
                        failure_category=(None if final["exact"] else
                            "proposal_completion" if final.get("incomplete_responses") and not final["compiling_children"]
                            else "byte_residual"))
                except Exception as exc:
                    row.update(status="error", exact=False, child_receipt=str(child_receipt),
                               error=f"{type(exc).__name__}: {str(exc)[:2000]}",
                               failure_category="exactness_worker_error")
                row["wall_seconds"] = round(time.time() - started, 3)
                if frozen_guard is not None:
                    frozen_guard()
                receipt["nodes"].append(row)
                _atomic_json(output, receipt)
                continue
            cases = cases_from_node(node)
            call_arities = {
                str(name): int(arity) for name, arity in
                (node.get("abi", {}).get("provisional_call_arities") or
                 {}).items()
            }
            return_registers = tuple(str(name) for name in
                (node.get("abi", {}).get("function", {}).get(
                    "return_registers") or []))
            started = time.time()
            seeded = None
            if m2c_preflight:
                seed_receipt = artifact_dir / f"{slug}.m2c-seed.json"
                seed_source = artifact_dir / f"{slug}.m2c-seed.best.c"
                try:
                    seeded = semantic_seed.run(
                        repo=repo, db=db, census_path=node_census_path,
                        function=function, output=seed_receipt,
                        best_source_out=seed_source,
                        stress_cases=m2c_stress_cases,
                        coverage_search_cases=m2c_coverage_search_cases,
                        max_steps=m2c_max_steps,
                        source_parent_attempt_id=attempt_id)
                    seed_result = seeded["result"]
                    row["m2c_semantic_seed"] = {
                        "status": seeded["status"],
                        "run_id": seeded["run_id"],
                        "receipt": str(seed_receipt),
                        "compiled": bool(seed_result.get("compiled")),
                        "all_stress_cases_passed": bool(
                            seed_result.get("all_semantic_cases_passed")),
                        "exact": bool(seed_result.get("exact")),
                        "attempt_id": (seed_result.get("attempt") or {}).get(
                            "attempt_id"),
                        "score": (seed_result.get("attempt") or {}).get(
                            "score"),
                    }
                    counts = (seed_result.get("differential") or {}).get(
                        "all") or {}
                    seed_target_coverage = (
                        seed_result.get("differential") or {}).get(
                            "target_coverage") or {}
                    seed_candidate_coverage = (
                        seed_result.get("differential") or {}).get(
                            "candidate_coverage") or {}
                    if (seed_result.get("all_semantic_cases_passed") and
                            seed_target_coverage.get("status") == "complete" and
                            seed_candidate_coverage.get("status") ==
                            "complete"):
                        row.update({
                            "route": "m2c_semantic_seed",
                            "status": "complete",
                            "best_source": str(seed_source),
                            "iterations": 0,
                            "accepted_improvements": 0,
                            "invalid_patch_rounds": 0,
                            "compiled_candidate_rounds": 0,
                            "termination_reason": (
                                "m2c seed passed target-only stress panel"
                                if node.get("semantic_authoritative") else
                                "m2c seed passed full-coverage opaque-call "
                                "panel; observational non-leaf only"),
                            "charged_tokens": 0,
                            "semantic_cases": sum(int(value) for value in
                                                  counts.values()),
                            "semantic_cases_passed": int(counts["passed"]),
                            "all_observed_semantic_cases_passed": True,
                            "semantic_settled": bool(
                                node.get("semantic_authoritative")),
                            "exact": bool(seed_result["exact"]),
                            "best_score": float(
                                seed_result["attempt"]["score"]),
                            "best_attempt_id": seed_result["attempt"][
                                "attempt_id"],
                            "target_coverage_complete": (
                                seed_target_coverage.get("status") ==
                                "complete"),
                            "candidate_coverage_complete": (
                                seed_candidate_coverage.get("status") ==
                                "complete"),
                            "target_coverage": seed_target_coverage,
                            "candidate_coverage": seed_candidate_coverage,
                            "wall_seconds": round(time.time() - started, 3),
                        })
                        receipt["nodes"].append(row)
                        _atomic_json(output, receipt)
                        continue
                except Exception as exc:
                    row["m2c_semantic_seed"] = {
                        "status": "error",
                        "receipt": str(seed_receipt),
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
            # Partial target coverage is evidence debt, but it must not hide a
            # concrete mismatch on paths that did execute.  Source repair can
            # improve those observed paths even though it cannot discharge the
            # remaining target-side debt.  Only a partially covered panel whose
            # observed cases already pass belongs solely in coverage debugging.
            target_coverage = (
                ((seeded or {}).get("coverage_exploration") or {}).get(
                    "coverage") or
                (node.get("combined_target_coverage") or {}))
            statuses = (node.get("differential") or {}).get(
                "statuses") or {}
            concrete_failures = int(statuses.get("failed", 0)) > 0
            if target_coverage.get("status") != "complete":
                coverage_receipt = artifact_dir / f"{slug}.coverage.json"
                try:
                    covered = coverage_worker.run(
                        repo=repo, db=db, census_path=node_census_path,
                        output=coverage_receipt, function=function,
                        source_path=source_path, source_parent_attempt_id=attempt_id,
                        cases=cases, max_cases=max(len(cases), m2c_coverage_search_cases),
                        stress_cases=max(len(cases), m2c_stress_cases),
                        max_steps=m2c_max_steps)
                    candidate_audit = covered["candidates"][0]
                    if not candidate_audit["attempt"]["compiled"]:
                        raise RuntimeError("coverage worker candidate failed fresh compilation")
                    cases = cases_from_rows(covered["panel"]["selected_cases"])
                    checked = candidate_audit["differential"]
                    statuses = checked["all"]
                    concrete_failures = int(statuses.get("failed", 0)) > 0
                    target_coverage = checked["target_coverage"]
                    attempt_id = candidate_audit["attempt"]["attempt_id"]
                    row.update({"coverage_worker_receipt": str(coverage_receipt),
                                "coverage_worker_status": "complete",
                                "target_coverage": target_coverage,
                                "target_coverage_complete": target_coverage.get("status") == "complete",
                                "candidate_coverage": checked["candidate_coverage"],
                                "candidate_coverage_complete": checked["candidate_coverage"].get("status") == "complete",
                                "semantic_certificate": candidate_audit.get("semantic_certificate")})
                except Exception as exc:
                    row.update({"coverage_worker_status": "error",
                                "coverage_worker_error": f"{type(exc).__name__}: {exc}"})
            if (target_coverage.get("status") != "complete" and
                    not concrete_failures):
                row.update({
                    "route": "coverage_debugger",
                    "status": "coverage_debt",
                    "best_source": str(source_path),
                    "iterations": 0,
                    "accepted_improvements": 0,
                    "invalid_patch_rounds": 0,
                    "compiled_candidate_rounds": 0,
                    "termination_reason": (
                        "observed cases pass but target coverage is incomplete; "
                        "source repair cannot resolve target-side evidence debt"),
                    "charged_tokens": 0,
                    "semantic_cases": sum(int(value) for value in
                                          statuses.values()),
                    "semantic_cases_passed": int(statuses.get("passed", 0)),
                    "all_observed_semantic_cases_passed": False,
                    "semantic_settled": False,
                    "exact": False,
                    "best_score": float(
                        node["attempt"].get("weighted_progress_score", 0.0)),
                    "best_attempt_id": attempt_id,
                    "target_coverage_complete": False,
                    "target_coverage": target_coverage,
                    "wall_seconds": round(time.time() - started, 3),
                })
                receipt["nodes"].append(row)
                _atomic_json(output, receipt)
                continue

            # The target-directed preflight has strictly richer evidence than
            # the old census panel.  Carry its coverage witnesses and semantic
            # holdouts into both root verification and any later repair.
            preflight_rows = (
                ((seeded or {}).get("stress_panel") or {}).get(
                    "selected_cases") or [])
            preflight_cases = cases_from_rows(preflight_rows)
            if preflight_cases:
                cases = tuple(dict.fromkeys(cases + preflight_cases))
            coverage_case_count = int(
                ((seeded or {}).get("coverage_exploration") or {}).get(
                    "selected_case_count") or len(cases))

            # A complete target panel is enough to audit the existing root;
            # census authority is no longer required.  This is the common
            # case for a good hand/agent scaffold around an m2c type-recovery
            # failure, and it prevents byte repair from running before logic
            # has actually been adjudicated on the newly discovered paths.
            if m2c_preflight and preflight_cases:
                root_stress_receipt = artifact_dir / f"{slug}.root-stress.json"
                try:
                    stressed = semantic_stress.run(
                        repo=repo, db=db, census_path=node_census_path,
                        output=root_stress_receipt, function=function,
                        candidate_paths=(source_path,),
                        max_cases=m2c_stress_cases,
                        source_parent_attempt_id=attempt_id,
                        panel_cases=preflight_cases,
                        frozen_count=coverage_case_count,
                        panel_metadata=(seeded or {}).get("stress_panel"))
                    stress_row = stressed["candidates"][0]
                    stress_counts = (stress_row.get("differential") or {}).get(
                        "all") or {}
                    stress_pass = bool(
                        stress_counts.get("passed") and
                        not stress_counts.get("failed") and
                        not stress_counts.get("inconclusive") and
                        (stress_row.get("differential") or {}).get(
                            "target_coverage", {}).get("status") ==
                        "complete" and
                        (stress_row.get("differential") or {}).get(
                            "candidate_coverage", {}).get("status") ==
                        "complete")
                    row["root_semantic_stress"] = {
                        "status": stressed["status"],
                        "run_id": stressed["run_id"],
                        "receipt": str(root_stress_receipt),
                        "counts": stress_counts,
                        "attempt": stress_row.get("attempt"),
                    }
                    if stress_pass:
                        stress_attempt = stress_row["attempt"]
                        stress_differential = stress_row["differential"]
                        row.update({
                            "route": "root_semantic_stress",
                            "status": "complete",
                            "best_source": str(source_path),
                            "iterations": 0,
                            "accepted_improvements": 0,
                            "invalid_patch_rounds": 0,
                            "compiled_candidate_rounds": 0,
                            "termination_reason": (
                                "existing root passed target-only stress "
                                "panel after m2c compile-context failure"
                                if node.get("semantic_authoritative") else
                                "existing root passed full-coverage "
                                "opaque-call panel; observational non-leaf "
                                "only"),
                            "charged_tokens": 0,
                            "semantic_cases": sum(int(value) for value in
                                                  stress_counts.values()),
                            "semantic_cases_passed": int(
                                stress_counts["passed"]),
                            "all_observed_semantic_cases_passed": True,
                            "semantic_settled": bool(
                                node.get("semantic_authoritative")),
                            "exact": bool(stress_attempt["exact"]),
                            "best_score": float(stress_attempt["score"]),
                            "best_attempt_id": stress_attempt["attempt_id"],
                            "target_coverage_complete": True,
                            "candidate_coverage_complete": True,
                            "target_coverage": stress_differential[
                                "target_coverage"],
                            "candidate_coverage": stress_differential[
                                "candidate_coverage"],
                            "wall_seconds": round(
                                time.time() - started, 3),
                        })
                        receipt["nodes"].append(row)
                        _atomic_json(output, receipt)
                        continue
                except Exception as exc:
                    row["root_semantic_stress"] = {
                        "status": "error",
                        "receipt": str(root_stress_receipt),
                        "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    }
            try:
                result = repair.run(
                    repo=repo, db=db, source_path=source_path,
                    source_parent_attempt_id=attempt_id,
                    output=child_receipt, best_source_out=best_source,
                    model=model, endpoint=endpoint, rounds=rounds,
                    timeout=timeout, think=think, num_thread=num_thread,
                    temperature=temperature,
                    diagnosis_num_predict=diagnosis_num_predict,
                    patch_num_predict=patch_num_predict,
                    patch_retries=patch_retries,
                    compiler_retries=compiler_retries,
                    max_stalls=max_stalls, seed=seed + ordinal * 1000,
                    cache_dir=cache_dir, function=function, cases=cases,
                    call_arities=call_arities,
                    return_registers=return_registers,
                    compiler_response_policy=compiler_response_policy,
                    proposal_cutoff=proposal_cutoff,
                    deterministic_only=deterministic_only,
                    dependency_context=evidence_schedule.render_contracts(
                        row["dependencies"], current_evidence))
                final = result["result"]
                verification = final["best_attempt"].get("verification")
                row["verification"] = verification
                if (final.get("exact") and verification and verification.get("exact")
                        and best_source.is_file()):
                    verified_source = best_source.read_text(encoding="utf-8")
                    if repair._sha(verified_source) == verification.get("candidate_source_sha256"):
                        row["verified_callee_contract"] = callsite_contracts.contract_for_node(
                            function, {"trust": {"exact": True,
                                                "attempt_id": final["best_attempt"]["attempt_id"],
                                                "authority": verification["scope"]},
                                       "exact_source": verified_source})
                iteration_rows = result["iterations"]
                deterministic_rows = result.get("deterministic_exactness", [])
                row.update({
                    "status": result["status"],
                    "child_run_id": result["run_id"],
                    "child_receipt": str(child_receipt),
                    "best_source": str(best_source),
                    "iterations": len(iteration_rows),
                    "deterministic_candidates": len(deterministic_rows),
                    "recovered_proposals": len(result.get("proposal_recovery", [])),
                    "accepted_recoveries": sum(bool(r.get("accepted"))
                                               for r in result.get("proposal_recovery", [])),
                    "accepted_improvements": sum(
                        bool(item.get("accepted_for_next_round"))
                        for item in iteration_rows + deterministic_rows),
                    "invalid_patch_rounds": sum(
                        item.get("status") == "invalid"
                        for item in iteration_rows),
                    "compiled_candidate_rounds": sum(
                        bool((item.get("attempt") or {}).get("compiled"))
                        for item in iteration_rows + deterministic_rows),
                    "termination_reason": result["termination_reason"],
                    "charged_tokens": int(result["charged_tokens"]),
                    "semantic_cases": len(cases),
                    "semantic_cases_passed": int(
                        final["semantic_cases_passed"]),
                    "all_observed_semantic_cases_passed": bool(
                        final["all_semantic_cases_passed"]),
                    "semantic_settled": bool(
                        final["all_semantic_cases_passed"] and
                        final.get("target_coverage", {}).get("status") == "complete" and
                        final.get("candidate_coverage", {}).get("status") == "complete" and
                        node.get("semantic_authoritative")),
                    "target_coverage": final.get("target_coverage", {}),
                    "candidate_coverage": final.get("candidate_coverage", {}),
                    "target_coverage_complete": final.get("target_coverage", {}).get("status") == "complete",
                    "candidate_coverage_complete": final.get("candidate_coverage", {}).get("status") == "complete",
                    "semantic_certificate": final.get("semantic_certificate"),
                    "exact": bool(final["exact"]),
                    "best_score": float(final["best_attempt"]["score"]),
                    "best_attempt_id": final["best_attempt"]["attempt_id"],
                })
            except Exception as exc:
                row.update({
                    "status": "error",
                    "error": f"{type(exc).__name__}: {str(exc)[:2000]}",
                    "child_receipt": str(child_receipt),
                    "exact": False,
                })
            row["wall_seconds"] = round(time.time() - started, 3)
            receipt["nodes"].append(row)
            _atomic_json(output, receipt)
    finally:
        conn.close()

    routed = [row for row in receipt["nodes"]
              if row.get("route") in {
                  "differential_repair", "m2c_semantic_seed",
                  "root_semantic_stress", "coverage_debugger", "compile_intake", "exactness_repair", "exactness_reverify"}]
    repaired = [row for row in routed
                if row.get("route") == "differential_repair"]
    seeded = [row for row in routed
              if row.get("route") == "m2c_semantic_seed"]
    root_stressed = [row for row in routed
                     if row.get("route") == "root_semantic_stress"]
    coverage_debt = [row for row in routed
                     if row.get("route") == "coverage_debugger"]
    receipt["aggregate"] = {
        "nodes_recorded": len(receipt["nodes"]),
        "compile_intake_nodes": sum("compile_intake" in row for row in receipt["nodes"]),
        "compile_intake_failures": sum((row.get("compile_intake") or {}).get("status") in {
            "error", "draft_failed", "compile_failed"} for row in receipt["nodes"]),
        "compile_intake_wall_seconds": round(sum(float(
            (row.get("compile_intake") or {}).get("wall_seconds", 0)) for row in receipt["nodes"]), 3),
        "repair_nodes": len(repaired),
        "exactness_repair_nodes": sum(row.get("route") == "exactness_repair" for row in routed),
        "exactness_reverified_nodes": sum(row.get("route") == "exactness_reverify" for row in routed),
        "exactness_functions_improved": sum(bool(row.get('best_score_improved')) for row in routed),
        "m2c_semantic_seed_nodes": len(seeded),
        "root_semantic_stress_nodes": len(root_stressed),
        "coverage_debugger_nodes": len(coverage_debt),
        "errors": sum(row.get("status") == "error" for row in routed),
        "exact": sum(bool(row.get("exact")) for row in routed),
        "observed_semantic_pass": sum(
            bool(row.get("all_observed_semantic_cases_passed"))
            for row in routed),
        "full_coverage_semantic_pass": sum(
            bool(row.get("all_observed_semantic_cases_passed")) and
            bool(row.get("target_coverage_complete")) and
            bool(row.get("candidate_coverage_complete"))
            for row in routed),
        "semantic_settled": sum(bool(row.get("semantic_settled"))
                                for row in routed),
        "charged_tokens": sum(int(row.get("charged_tokens") or 0)
                              for row in routed),
        "accepted_improvements": sum(
            int(row.get("accepted_improvements") or 0) for row in routed),
        "invalid_patch_rounds": sum(
            int(row.get("invalid_patch_rounds") or 0) for row in routed),
        "incomplete_responses": sum(int(row.get("incomplete_responses") or 0) for row in routed),
        "compiled_candidate_rounds": sum(
            int(row.get("compiled_candidate_rounds") or 0) for row in routed),
        "wall_seconds": round(sum(float(row.get("wall_seconds") or 0)
                                  for row in routed) + sum(float(
            (row.get("compile_intake") or {}).get("wall_seconds", 0)) for row in receipt["nodes"]), 3),
    }
    receipt["status"] = (
        "complete" if len(completed | {
            str(row["function"]) for row in receipt["nodes"]}) == len(nodes)
        else "bounded_stop")
    receipt["completed_at"] = int(time.time())
    _atomic_json(output, receipt)
    return receipt


def audit(*, db: Path, sets: Path, receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    routed = [row for row in receipt.get("nodes", [])
              if row.get("route") in {
                  "differential_repair", "m2c_semantic_seed",
      "root_semantic_stress", "coverage_debugger", "exactness_repair", "exactness_reverify", "compile_intake"}]
    heldout = set()
    if sets.exists():
        # Use the same frozen-set loader as the admission checks so the audit
        # reports overlap explicitly rather than merely failing at launch.
        from eval import logic_first
        heldout = logic_first._heldout(sets)
    details = []
    conn = sqlite3.connect(db, timeout=120)
    try:
        for row in routed:
            seed = row.get("m2c_semantic_seed") or {}
            root_stress = row.get("root_semantic_stress") or {}
            run_id = str(row.get("child_run_id") or
                         root_stress.get("run_id") or
                         seed.get("run_id") or "")
            attempts = conn.execute(
                "SELECT a.id,f.name,a.parent_attempt_id,a.exact "
                "FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                "WHERE a.run_id=? ORDER BY a.id", (run_id,)).fetchall()
            edges = conn.execute(
                "SELECT COUNT(*) FROM attempt_edges e JOIN attempts a "
                "ON a.id=e.child_attempt_id WHERE a.run_id=?",
                (run_id,)).fetchone()[0]
            ids = {int(attempt[0]) for attempt in attempts}
            names = {str(attempt[1]) for attempt in attempts}
            function = str(row["function"])
            child_path = Path(str(
                row.get("child_receipt") or root_stress.get("receipt") or
                seed.get("receipt") or ""))
            child = (json.loads(child_path.read_text(encoding="utf-8"))
                     if child_path.exists() else {})
            iterations = child.get("iterations") or []
            compiler_result = (child.get('result') or {}) if row.get('route') in {
                'exactness_repair', 'exactness_reverify'} else None
            best_required = row.get("route") != "coverage_debugger"
            best_present = int(row.get("best_attempt_id") or -1) in ids
            clean = bool(
                attempts and len(attempts) == int(edges)
                and all(attempt[2] is not None for attempt in attempts)
                and names == {function}
                and (best_present or not best_required)
                and function not in heldout and child_path.exists())
            details.append({
                "function": function,
                "route": row.get("route"),
                "run_id": run_id,
                "attempts": len(attempts),
                "lineage_edges": int(edges),
                "all_attempts_have_parents": all(
                    attempt[2] is not None for attempt in attempts),
                "database_functions": sorted(names),
                "best_attempt_present": best_present,
                "best_attempt_required": best_required,
                "heldout_overlap": function in heldout,
                "child_receipt_exists": child_path.exists(),
                "iterations": (int(compiler_result.get('calls_attempted', 0))
                               if compiler_result is not None else len(iterations)),
                "accepted_improvements": (int(compiler_result.get('best_score_improved', False))
                    if compiler_result is not None else sum(
                    bool(item.get("accepted_for_next_round"))
                    for item in iterations)),
                "invalid_patch_rounds": (int(compiler_result.get('invalid_proposals', 0))
                    if compiler_result is not None else sum(
                    item.get("status") == "invalid"
                    for item in iterations)),
                "compiled_candidate_rounds": (int(compiler_result.get('compiling_children', 0))
                    + int(compiler_result.get('deterministic_compiling_children', 0))
                    if compiler_result is not None else sum(
                    bool((item.get("attempt") or {}).get("compiled"))
                    for item in iterations)),
                "clean": clean,
            })
    finally:
        conn.close()
    return {
        "wave_run_id": receipt.get("run_id"),
        "routed_nodes": len(routed),
        "repair_nodes": sum(row.get("route") == "differential_repair"
                            for row in routed),
        "m2c_semantic_seed_nodes": sum(
            row.get("route") == "m2c_semantic_seed" for row in routed),
        "root_semantic_stress_nodes": sum(
            row.get("route") == "root_semantic_stress" for row in routed),
        "coverage_debugger_nodes": sum(
            row.get("route") == "coverage_debugger" for row in routed),
        "attempts": sum(row["attempts"] for row in details),
        "lineage_edges": sum(row["lineage_edges"] for row in details),
        "iterations": sum(row["iterations"] for row in details),
        "accepted_improvements": sum(
            row["accepted_improvements"] for row in details),
        "invalid_patch_rounds": sum(
            row["invalid_patch_rounds"] for row in details),
        "compiled_candidate_rounds": sum(
            row["compiled_candidate_rounds"] for row in details),
        "heldout_overlap": sorted(
            row["function"] for row in details if row["heldout_overlap"]),
        "clean": bool(details) and all(row["clean"] for row in details),
        "nodes": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path,
                        default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--parent-wave", type=Path,
        help="start each function from a prior wave's retained best attempt")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--max-functions", type=int)
    parser.add_argument(
        "--functions", nargs="+",
        help="optional function-name subset; census/DAG order is preserved")
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--endpoint")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--exactness-only", action="store_true",
                        help="use the compiler-only repair lane even when differential execution is available")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--think", default="high")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.25)
    parser.add_argument("--diagnosis-num-predict", type=int, default=8000)
    parser.add_argument("--patch-num-predict", type=int, default=2000)
    parser.add_argument("--patch-retries", type=int, default=2)
    parser.add_argument("--compiler-retries", type=int, default=2)
    parser.add_argument("--max-stalls", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--no-m2c-semantic-preflight", action="store_true",
        help="disable the zero-LLM m2c logic/context stage")
    parser.add_argument("--m2c-stress-cases", type=int, default=256)
    parser.add_argument("--m2c-coverage-search-cases", type=int, default=5000)
    parser.add_argument("--m2c-max-steps", type=int, default=2_000)
    args = parser.parse_args()
    if args.max_functions is not None and args.max_functions < 1:
        parser.error("--max-functions must be positive")
    if args.audit_only:
        result = audit(
            db=args.db.expanduser().resolve(),
            sets=args.sets.expanduser().resolve(),
            receipt_path=args.output.expanduser().resolve())
        print(json.dumps(result, indent=2))
        return
    endpoint = args.endpoint or llm.host()
    parent_wave = (args.parent_wave.expanduser().resolve()
                   if args.parent_wave else None)
    result = run(
        repo=args.repo.expanduser().resolve(),
        db=args.db.expanduser().resolve(),
        sets=args.sets.expanduser().resolve(),
        census_path=args.census.expanduser().resolve(),
        output=args.output.expanduser().resolve(), model=args.model,
        endpoint=endpoint, rounds=args.rounds, timeout=args.timeout,
        think=args.think, num_thread=args.num_thread,
        temperature=args.temperature,
        diagnosis_num_predict=args.diagnosis_num_predict,
        patch_num_predict=args.patch_num_predict,
        patch_retries=args.patch_retries,
        compiler_retries=args.compiler_retries,
        max_stalls=args.max_stalls, seed=args.seed,
        cache_dir=(args.cache_dir.expanduser().resolve()
                   if args.cache_dir else None),
        max_functions=args.max_functions, resume=args.resume,
        parent_wave_path=parent_wave,
        functions=tuple(args.functions) if args.functions else None,
        m2c_preflight=not args.no_m2c_semantic_preflight,
        m2c_stress_cases=args.m2c_stress_cases,
        m2c_coverage_search_cases=args.m2c_coverage_search_cases,
        m2c_max_steps=args.m2c_max_steps, exactness_only=args.exactness_only)
    print(json.dumps({
        "status": result["status"],
        "aggregate": result["aggregate"],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
