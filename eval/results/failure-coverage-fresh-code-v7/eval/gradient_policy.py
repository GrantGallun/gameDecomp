#!/usr/bin/env python3
"""Fit and report the local cross-function compiler-response policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from kb import attempts
from solver import exactness_gradient, principle_variants, transition_policy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-function", default="")
    parser.add_argument(
        "--function", default="",
        help="also rank the latest compiled residual for this function")
    parser.add_argument(
        "--attempt-id", type=int,
        help="query this exact attempt instead of the latest function row")
    parser.add_argument(
        "--scan", action="store_true",
        help="list latest nonexact functions with deterministic exactness work")
    parser.add_argument(
        "--scan-limit", type=int, default=24,
        help="maximum shortest-source residuals to inspect with --scan")
    parser.add_argument(
        "--scan-generator", choices=("full", "direct-byte-update"),
        default="full", help="candidate family to inspect with --scan")
    parser.add_argument(
        "--audit-run", default="",
        help="include attempt/edge/model-proposal integrity for this run id")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db.expanduser().resolve())
    attempts.ensure_lineage_schema(conn)
    policy = transition_policy.TransitionPolicy.from_db(
        conn, limit=args.limit)
    report = policy.summary(exclude_function=args.exclude_function)
    report["cross_validation"] = policy.cross_validate()
    if args.audit_run:
        attempt_count, compiled_count, exact_count = conn.execute(
            "SELECT count(*),sum(compiled),sum(coalesce(exact,0)=1) "
            "FROM attempts WHERE run_id=?", (args.audit_run,)).fetchone()
        edge_count = conn.execute(
            "SELECT count(*) FROM attempt_edges e JOIN attempts ch ON "
            "ch.id=e.child_attempt_id WHERE ch.run_id=?",
            (args.audit_run,)).fetchone()[0]
        missing_edges = conn.execute(
            "SELECT count(*) FROM attempts a WHERE a.run_id=? AND "
            "a.parent_attempt_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
            "attempt_edges e WHERE e.parent_attempt_id=a.parent_attempt_id "
            "AND e.child_attempt_id=a.id)", (args.audit_run,)).fetchone()[0]
        proposal_count = conn.execute(
            "SELECT count(*) FROM model_proposals WHERE run_id=?",
            (args.audit_run,)).fetchone()[0]
        report["run_audit"] = {
            "run_id": args.audit_run,
            "attempt_count": int(attempt_count),
            "compiled_attempt_count": int(compiled_count or 0),
            "exact_attempt_count": int(exact_count or 0),
            "explicit_child_edge_count": int(edge_count),
            "attempts_missing_declared_parent_edge": int(missing_edges),
            "model_proposal_count": int(proposal_count),
        }
    if args.scan:
        from eval import differential_repair_pilot as repair
        scan = []
        scan_rows = conn.execute(
                "WITH latest AS (SELECT func_addr,max(id) AS id FROM attempts "
                "WHERE compiled=1 AND coalesce(exact,0)=0 AND "
                "diff_summary IS NOT NULL GROUP BY func_addr), "
                "exact_funcs AS (SELECT DISTINCT func_addr FROM attempts "
                "WHERE exact=1) "
                "SELECT f.name,a.id,a.source_code,a.diff_summary,a.score "
                "FROM latest l JOIN attempts a ON a.id=l.id "
                "JOIN functions f ON f.addr=a.func_addr "
                "LEFT JOIN exact_funcs x ON x.func_addr=a.func_addr "
                "WHERE x.func_addr IS NULL "
                "ORDER BY f.name").fetchall()
        # Source-shape generation is intentionally bounded.  Shorter functions
        # are the useful wavefront-first audit and avoid turning this report
        # into a whole-corpus rewrite benchmark.
        scan_rows.sort(key=lambda row: (len(str(row[2])), str(row[0])))
        for row in scan_rows[:max(0, args.scan_limit)]:
            function, attempt_id, source, diff, score = row
            source, diff = str(source), str(diff or "")
            if args.scan_generator == "direct-byte-update":
                if not repair.register_or_local_order_only(diff):
                    continue
                variants = tuple(
                    variant for variant in
                    principle_variants.fused_byte_update_lookup(
                        source, str(function))
                    if transition_policy.action_family(
                        "deterministic-exactness-search", variant.label) ==
                    "direct-byte-update")
            else:
                variants = repair.deterministic_exactness_candidates(
                    source, str(function), diff, policy=policy)
            if not variants:
                continue
            state = transition_policy.residual_state(
                diff, source, score=float(score or 0.0))
            top = policy.estimate(
                variants[0].label, state,
                relation="deterministic-exactness-search",
                exclude_function=str(function))
            scan.append({
                "function": str(function), "attempt_id": int(attempt_id),
                "score": float(score or 0.0),
                "classification": state.classification,
                "candidate_count": len(variants),
                "top_family": top.action_family,
                "top_family_support_functions": top.support_functions,
                "top_family_exact_transitions": top.exact_transitions,
            })
        report["latest_residual_scan"] = sorted(
            scan, key=lambda item: (-item["candidate_count"],
                                    item["function"]))
    if args.function or args.attempt_id is not None:
        if args.attempt_id is not None:
            row = conn.execute(
                "SELECT a.id,a.source_code,a.diff_summary,a.score,a.exact,"
                "f.name FROM attempts a JOIN functions f ON "
                "f.addr=a.func_addr WHERE a.id=? AND a.compiled=1 AND "
                "a.diff_summary IS NOT NULL", (args.attempt_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT a.id,a.source_code,a.diff_summary,a.score,a.exact,"
                "f.name FROM attempts a JOIN functions f ON "
                "f.addr=a.func_addr WHERE f.name=? AND a.compiled=1 AND "
                "a.diff_summary IS NOT NULL ORDER BY a.id DESC LIMIT 1",
                (args.function,)).fetchone()
        if row is None:
            raise ValueError(
                f"no compiled residual found for "
                f"{args.attempt_id or args.function}")
        attempt_id, source, diff, score, exact, row_function = row
        query_function = str(row_function)
        if args.function and args.function != query_function:
            raise ValueError(
                f"attempt {attempt_id} belongs to {query_function}, not "
                f"{args.function}")
        state = transition_policy.residual_state(
            str(diff or ""), str(source), score=float(score or 0.0),
            exact=bool(exact))
        from eval import differential_repair_pilot as repair
        variants = repair.deterministic_exactness_candidates(
            str(source), query_function, str(diff or ""), policy=None)
        history = list(exactness_gradient.receipt_history(
            conn, int(attempt_id), str(source)))
        seen = {str(item.get("source_sha256")) for item in history
                if item.get("source_sha256")}
        novel_variants = [variant for variant in variants
                          if hashlib.sha256(
                              variant.source.encode()).hexdigest() not in seen]
        grouped: dict[str, dict] = {}
        for variant in variants:
            family = transition_policy.action_family(
                "deterministic-exactness-search", variant.label)
            item = grouped.setdefault(family, {
                "family": family, "candidate_count": 0,
                "example_label": variant.label})
            item["candidate_count"] += 1
        ranked = []
        for item in grouped.values():
            estimate = policy.estimate(
                item["example_label"], state,
                relation="deterministic-exactness-search",
                exclude_function=query_function)
            item["estimate"] = estimate.to_dict()
            ranked.append(item)
        ranked.sort(
            key=lambda item: tuple(item["estimate"]["rank_key"]),
            reverse=True)
        report["query"] = {
            "function": query_function,
            "attempt_id": int(attempt_id),
            "source_sha256": hashlib.sha256(str(source).encode()).hexdigest(),
            "score": float(score or 0.0),
            "exact": bool(exact),
            "state": state.to_dict(),
            "generated_candidate_count": len(variants),
            "prior_exactness_experiment_count": len(history),
            "novel_candidate_count": len(novel_variants),
            "ranked_available_families": ranked,
            "leave_one_function_out": True,
        }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
