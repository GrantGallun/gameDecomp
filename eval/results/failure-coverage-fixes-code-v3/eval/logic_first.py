"""Freeze and baseline a call-connected DEV cluster for logic-first recovery.

Selection is target-source-free and uses only existing DEV attempt provenance,
function metadata, and binary call edges.  The baseline recompiles each frozen
source, classifies its broad assembly shape, and keeps behavioral equivalence
explicitly untested unless the byte oracle reports exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from eval import agentrepair, callgraph, matched
from solver import logic, refine, residual, workspace


def _digest(value: dict) -> str:
    unsigned = dict(value)
    unsigned.pop("manifest_digest", None)
    return hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _heldout(sets: Path) -> set[str]:
    paths = sorted(sets.glob("*.json")) if sets.is_dir() else [sets]
    names: set[str] = set()
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        names.update(row["function"] for row in value.get("heldout", [])
                     if isinstance(row, dict)
                     and isinstance(row.get("function"), str))
    return names


def settled_functions(receipts: tuple[Path, ...]) -> set[str]:
    """Read prior wave receipts and return only evidence-backed settlements."""
    names: set[str] = set()
    for path in receipts:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = list(value.get("nodes", []))
        rows.extend((value.get("dag") or {}).get("nodes", []))
        for row in rows:
            target_coverage = (row.get("target_coverage") or
                               row.get("combined_target_coverage") or {})
            candidate_coverage = (row.get("candidate_coverage") or
                                  row.get("combined_candidate_coverage") or {})
            target_complete = bool(row.get("target_coverage_complete")) or \
                target_coverage.get("status") == "complete"
            candidate_complete = bool(row.get("candidate_coverage_complete")) or \
                candidate_coverage.get("status") == "complete"
            statuses = (row.get("differential") or {}).get("statuses") or {}
            observed_pass = bool(
                row.get("all_observed_semantic_cases_passed")) or bool(
                    statuses.get("passed") and
                    not statuses.get("failed") and
                    not statuses.get("inconclusive"))
            semantic_pass = bool(row.get("semantic_settled")) or (
                observed_pass and target_complete and candidate_complete)
            exact = bool(row.get("exact")) or bool(
                (row.get("attempt") or {}).get("exact"))
            if exact or semantic_pass:
                names.add(str(row["function"]))
        function = (value.get("config") or {}).get("function")
        for row in value.get("candidates", []):
            summary = row.get("differential") or {}
            counts = summary.get("all") or {}
            candidate_pass = bool(
                (row.get("attempt") or {}).get("compiled") and
                counts.get("passed") and not counts.get("failed") and
                not counts.get("inconclusive") and
                (summary.get("target_coverage") or {}).get("status") ==
                "complete" and
                (summary.get("candidate_coverage") or {}).get("status") ==
                "complete")
            if function and candidate_pass:
                names.add(str(function))
    return names


def candidate_rows(conn: sqlite3.Connection, blocked: set[str]) -> list[dict]:
    done = matched.already_matched(conn)
    rows = conn.execute(
        "SELECT f.name,f.tu_id,f.insn_count,a.id,a.source_code,a.score "
        "FROM attempts a JOIN functions f ON f.addr=a.func_addr "
        "WHERE a.compiled=1 AND COALESCE(a.exact,0)=0 "
        "AND a.source_code IS NOT NULL "
        "ORDER BY a.score DESC,a.id DESC").fetchall()
    selected = []
    seen: set[str] = set()
    for name, tu_id, insns, attempt_id, source, score in rows:
        name = str(name)
        if name in seen or name in blocked or name in done:
            continue
        seen.add(name)
        source = str(source)
        selected.append({
            "function": name,
            "tu_id": int(tu_id) if tu_id is not None else None,
            "instruction_count": int(insns) if insns is not None else None,
            "attempt_id": int(attempt_id),
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "stored_weighted_score": float(score),
        })
    return selected


def connected_cluster(rows: list[dict], edges: set[tuple[str, str]],
                      limit: int) -> tuple[list[dict], list[dict]]:
    """Choose a deterministic binary-call-connected candidate neighborhood."""
    by_name = {row["function"]: row for row in rows}
    adjacency: dict[str, set[str]] = defaultdict(set)
    directed = set()
    for caller, callee in edges:
        if caller not in by_name or callee not in by_name or caller == callee:
            continue
        adjacency[caller].add(callee)
        adjacency[callee].add(caller)
        directed.add((caller, callee))
    seeds = [name for name in by_name if adjacency.get(name)]
    if not seeds:
        # Once every remaining unresolved candidate is disconnected from every
        # other unresolved candidate, the connected wave is exhausted rather
        # than the recovery project. Advance across independent leaf roots in
        # one batch; the DAG census will still record each as its own component.
        ordered = sorted(
            rows,
            key=lambda row: (
                -row["stored_weighted_score"],
                row.get("instruction_count") or 10**9,
                row["function"],
            ))
        return ordered[:max(1, limit)], []

    def seed_key(name: str) -> tuple:
        row = by_name[name]
        same_tu = sum(by_name[other].get("tu_id") == row.get("tu_id")
                      for other in adjacency[name])
        insns = row.get("instruction_count") or 10**9
        return (same_tu, len(adjacency[name]), -insns,
                row["stored_weighted_score"], name)

    seed = max(seeds, key=seed_key)
    selected = [seed]
    selected_set = {seed}
    seed_tu = by_name[seed].get("tu_id")
    while len(selected) < max(1, limit):
        frontier = {neighbor for name in selected for neighbor in adjacency[name]
                    if neighbor not in selected_set}
        if not frontier:
            break

        def frontier_key(name: str) -> tuple:
            links = sum(name in adjacency[item] for item in selected)
            row = by_name[name]
            return (row.get("tu_id") == seed_tu, links,
                    len(adjacency[name]), row["stored_weighted_score"], name)

        choice = max(frontier, key=frontier_key)
        selected.append(choice)
        selected_set.add(choice)
    cluster_edges = [{"caller": caller, "callee": callee}
                     for caller, callee in sorted(directed)
                     if caller in selected_set and callee in selected_set]
    return [by_name[name] for name in selected], cluster_edges


def freeze(*, db: Path, sets: Path, out: Path, limit: int = 8,
           exclude_receipts: tuple[Path, ...] = ()) -> dict:
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    settled = settled_functions(exclude_receipts)
    rows = candidate_rows(conn, _heldout(sets) | settled)
    callees, _callers = callgraph.edges(conn)
    edge_set = {(caller, callee) for caller, values in callees.items()
                for callee in values}
    cluster, edges = connected_cluster(rows, edge_set, limit)
    if not cluster:
        raise ValueError("no call-connected eligible DEV candidates")
    manifest = {
        "schema_version": 1,
        "kind": "logic-first-connected-dev-cluster",
        "created_at": int(time.time()),
        "selection": {
            "target_source_inspected": False,
            "inputs": [
                "binary call edges", "function/TU metadata",
                "existing compiled non-exact DEV attempt provenance",
            ],
            "heldout_excluded_across_all_manifests": True,
            "already_exact_excluded": True,
            "prior_semantic_settlements_excluded": sorted(settled),
            "exclusion_receipts": [str(path) for path in exclude_receipts],
            "requested_size": limit,
            "eligible_candidates": len(rows),
            "frontier_mode": (
                "call-connected-component" if edges else
                "independent-leaf-batch"),
        },
        "cluster": cluster,
        "binary_call_edges": edges,
        "shortfall": max(0, limit - len(cluster)),
    }
    manifest["manifest_digest"] = _digest(manifest)
    agentrepair._atomic_json(out, manifest)
    conn.close()
    return manifest


def _assembly(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def _aggregate(rows: list[dict]) -> dict:
    stages: dict[str, int] = defaultdict(int)
    for row in rows:
        stages[row["assessment"]["stage"]] += 1
    metrics = ("call_sequence", "memory_effects", "control_structure",
               "opcode_sequence")
    return {
        "functions": len(rows),
        "compiled": sum(row["attempt"]["compiled"] for row in rows),
        "exact": sum(row["attempt"]["exact"] for row in rows),
        "stages": dict(sorted(stages.items())),
        "behaviorally_tested": sum(
            row["assessment"]["semantic_status"] != "not_tested"
            for row in rows),
        "mean_shape_metrics": {
            metric: round(sum(
                row["assessment"]["metrics"].get(metric, 0.0) for row in rows
            ) / len(rows), 6) if rows else 0.0
            for metric in metrics
        },
    }


def baseline(*, repo: Path, db: Path, sets: Path, manifest_path: Path,
             out: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_digest") != _digest(manifest):
        raise ValueError("logic-first manifest digest is invalid")
    for row in manifest.get("cluster", []):
        agentrepair._refuse_frozen_heldout(sets, row["function"])
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    run_id = f"logic-first-baseline-{time.time_ns()}"
    config = {
        "schema_version": 1,
        "kind": "logic-first-zero-model-baseline",
        "manifest": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "generation_tokens": 0,
        "semantic_claim_policy": (
            "assembly shape is evidence only; behavior remains untested unless "
            "the byte oracle is exact"),
    }
    receipt = {
        "schema_version": 1,
        "kind": config["kind"],
        "run_id": run_id,
        "created_at": int(time.time()),
        "config": config,
        "binary_call_edges": manifest.get("binary_call_edges", []),
        "functions": [],
    }
    for index, frozen in enumerate(manifest["cluster"]):
        name = frozen["function"]
        source = agentrepair._source_for_attempt(
            conn, int(frozen["attempt_id"]), name)
        if hashlib.sha256(source.encode()).hexdigest() != frozen["source_sha256"]:
            raise ValueError(f"frozen source hash changed for {name}")
        ws = workspace.bootstrap(repo, name)
        tag = f"{name}_logic_first_root_{time.time_ns()}"
        attempt = workspace.score(
            ws, repo, tag, source, conn=conn, func=name,
            strategy="logic-first-root-reverify", model="zero-model",
            run_id=run_id, iteration=index,
            parent_attempt_id=int(frozen["attempt_id"]),
            relation="logic-first-frozen-root",
            action="fresh logic-first baseline verification",
            run_kind=config["kind"], run_config=config)
        object_path = ws / f"{tag}.o" if attempt.compiled else None
        packet = residual.build(
            attempt, target_asm=workspace.target_asm(ws, name),
            target_object=ws / "target.o", candidate_object=object_path)
        target_assembly = _assembly(ws / "target_object_dump_normalized.s")
        if not target_assembly:
            target_assembly = workspace.target_asm(ws, name)
        candidate_assembly = _assembly(
            ws / f"{tag}_object_dump_normalized.s") if attempt.compiled else ""
        assessment = logic.compare(
            target_assembly, candidate_assembly, attempt=attempt)
        receipt["functions"].append({
            "function": name,
            "source_attempt_id": int(frozen["attempt_id"]),
            "attempt": {
                "attempt_id": attempt.receipt_id,
                "compiled": attempt.compiled,
                "exact": attempt.exact,
                "weighted_progress_score": attempt.score,
                "residual": packet.to_dict(),
            },
            "assessment": assessment.to_dict(),
        })
        receipt["aggregate"] = _aggregate(receipt["functions"])
        agentrepair._atomic_json(out, receipt)
    receipt["aggregate"] = _aggregate(receipt["functions"])
    receipt["completed_at"] = int(time.time())
    agentrepair._atomic_json(out, receipt)
    conn.close()
    return receipt


def audit(*, db: Path, sets: Path, receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("kind") != "logic-first-zero-model-baseline":
        raise ValueError("not a logic-first baseline receipt")
    run_id = str(receipt.get("run_id") or "")
    if not run_id:
        raise ValueError("logic-first receipt has no run id")
    conn = sqlite3.connect(db, timeout=120)
    rows = conn.execute(
        "SELECT a.id,f.name,a.parent_attempt_id,a.compiled,a.exact "
        "FROM attempts a JOIN functions f ON f.addr=a.func_addr "
        "WHERE a.run_id=? ORDER BY a.id", (run_id,)).fetchall()
    edges = conn.execute(
        "SELECT COUNT(*) FROM attempt_edges e JOIN attempts a "
        "ON a.id=e.child_attempt_id WHERE a.run_id=?", (run_id,)).fetchone()[0]
    names = {str(row[1]) for row in rows}
    receipt_names = {str(row["function"])
                     for row in receipt.get("functions", [])}
    result = {
        "run_id": run_id,
        "attempts": len(rows),
        "edges": int(edges),
        "all_attempts_have_parents": all(row[2] is not None for row in rows),
        "all_attempts_compiled": all(bool(row[3]) for row in rows),
        "exact": sum(bool(row[4]) for row in rows),
        "receipt_names_match_database": names == receipt_names,
        "heldout_overlap": sorted(names & _heldout(sets)),
        "clean": (
            len(rows) == len(receipt_names) == int(edges)
            and all(row[2] is not None for row in rows)
            and names == receipt_names
            and not (names & _heldout(sets))
        ),
    }
    conn.close()
    return result


def module_packet(receipt: dict, function: str) -> dict:
    """Build bounded shared binary context for a later logic-repair agent."""
    rows = {str(row["function"]): row for row in receipt.get("functions", [])}
    if function not in rows:
        raise ValueError(f"{function} is not in the logic-first receipt")
    packet = {
        "schema_version": 1,
        "kind": "logic-first-module-packet",
        "function": function,
        "policies": {
            "target_reference_c_available": False,
            "binary_profiles_are_evidence": True,
            "candidate_profiles_are_diagnostics": True,
            "semantic_equivalence_requires_separate_behavioral_evidence": True,
            "byte_exact_is_terminal_but_not_the_nonexact_search_rank": True,
        },
        "binary_call_edges": receipt.get("binary_call_edges", []),
        "functions": [{
            "function": name,
            "active_target": name == function,
            "stage": row["assessment"]["stage"],
            "semantic_status": row["assessment"]["semantic_status"],
            "shape_metrics": row["assessment"]["metrics"],
            "failed_gates": sorted(
                key for key, passed in row["assessment"]["gates"].items()
                if not passed),
            "target_profile": row["assessment"].get("target"),
        } for name, row in sorted(rows.items())],
    }
    packet["packet_digest"] = hashlib.sha256(json.dumps(
        packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--db", type=Path, required=True)
    freeze_parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    freeze_parser.add_argument("--out", type=Path, required=True)
    freeze_parser.add_argument("--limit", type=int, default=8)
    freeze_parser.add_argument(
        "--exclude-receipt", type=Path, action="append", default=[],
        help="prior wave receipt whose evidence-backed settlements are skipped")
    baseline_parser = subparsers.add_parser("baseline")
    baseline_parser.add_argument("--repo", type=Path, required=True)
    baseline_parser.add_argument("--db", type=Path, required=True)
    baseline_parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    baseline_parser.add_argument("--manifest", type=Path, required=True)
    baseline_parser.add_argument("--out", type=Path, required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--db", type=Path, required=True)
    audit_parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    audit_parser.add_argument("--receipt", type=Path, required=True)
    packet_parser = subparsers.add_parser("packet")
    packet_parser.add_argument("--receipt", type=Path, required=True)
    packet_parser.add_argument("--function", required=True)
    packet_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(
            db=args.db.expanduser().resolve(), sets=args.sets.expanduser().resolve(),
            out=args.out.expanduser().resolve(), limit=args.limit,
            exclude_receipts=tuple(
                path.expanduser().resolve() for path in args.exclude_receipt))
        summary = {
            "cluster_size": len(result["cluster"]),
            "edges": len(result["binary_call_edges"]),
            "shortfall": result["shortfall"],
            "manifest_digest": result["manifest_digest"],
        }
    elif args.command == "baseline":
        result = baseline(
            repo=args.repo.expanduser().resolve(), db=args.db.expanduser().resolve(),
            sets=args.sets.expanduser().resolve(),
            manifest_path=args.manifest.expanduser().resolve(),
            out=args.out.expanduser().resolve())
        summary = result["aggregate"]
    elif args.command == "audit":
        summary = audit(
            db=args.db.expanduser().resolve(), sets=args.sets.expanduser().resolve(),
            receipt_path=args.receipt.expanduser().resolve())
    else:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        result = module_packet(receipt, args.function)
        agentrepair._atomic_json(args.out.expanduser().resolve(), result)
        summary = {
            "function": result["function"],
            "module_functions": len(result["functions"]),
            "binary_call_edges": len(result["binary_call_edges"]),
            "packet_digest": result["packet_digest"],
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
