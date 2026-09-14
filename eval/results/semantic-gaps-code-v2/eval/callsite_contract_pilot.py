"""Measure interprocedural callsite-contract coverage on a frozen cohort.

This is the deterministic gate before an LLM experiment.  It bootstraps each
parent's target assembly, aligns binary call instructions with evidence rows,
binds frozen exact callee contracts, and reports argument/return-use coverage.
No source candidate is generated or scored here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

from solver import callsite_contracts, refine, shaped_flywheel, workspace


def _names(args: argparse.Namespace) -> list[str]:
    names = list(args.function or [])
    if args.set:
        payload = json.loads(args.set.read_text(encoding="utf-8"))
        names.extend(str(row["function"]) for row in payload[args.split])
    return list(dict.fromkeys(names))


def _metadata(conn: sqlite3.Connection, name: str) -> dict[str, object]:
    row = conn.execute(
        "select addr, size, insn_count, is_leaf from functions "
        "where name=? limit 1", (name,)).fetchone()
    best = conn.execute(
        "select max(score) from attempts a join functions f "
        "on f.addr=a.func_addr where f.name=? and a.compiled=1",
        (name,)).fetchone()
    if row is None:
        return {"address": None, "size": None, "instruction_count": None,
                "is_leaf": None, "historical_best_score": 0.0}
    return {
        "address": f"0x{int(row[0]) & 0xFFFFFFFF:08X}",
        "size": row[1],
        "instruction_count": row[2],
        "is_leaf": bool(row[3]) if row[3] is not None else None,
        "historical_best_score": float((best or [0.0])[0] or 0.0),
    }


def _information_score(bundle: dict[str, object]) -> int:
    score = 0
    for row in bundle["callsites"]:
        contract = row.get("exact_callee_contract")
        if not contract:
            continue
        score += int(row["argument_resolution"]["resolved"])
        if contract["return"].get("class") != "void":
            score += 1
        if row.get("return_consumed"):
            score += 4
        score += min(2, len(row.get("bound_effects") or []))
    return score


def aggregate(rows: list[dict]) -> dict[str, object]:
    usable = [row for row in rows if not row.get("error")]
    mismatches = [{
        "function": row["function"],
        "binary_calls": row["contracts"]["coverage"]["binary_calls"],
        "evidence_calls": row["contracts"]["coverage"]["evidence_calls"],
    } for row in usable if row["contracts"]["coverage"]["binary_calls"]
        != row["contracts"]["coverage"]["evidence_calls"]]
    totals = {
        key: sum(int(row["contracts"]["coverage"][key]) for row in usable)
        for key in (
            "binary_calls", "evidence_calls", "aligned_calls",
            "resolved_targets", "exact_callee_contracts",
            "resolved_exact_arguments", "total_exact_arguments",
            "exact_calls_with_consumed_return",
        )
    }
    ranked = sorted(({
        "function": row["function"],
        "information_score": row["information_score"],
        "instruction_count": row["metadata"]["instruction_count"],
        "historical_best_score": row["metadata"]["historical_best_score"],
        "exact_callee_contracts": row["contracts"]["coverage"][
            "exact_callee_contracts"],
        "consumed_returns": row["contracts"]["coverage"][
            "exact_calls_with_consumed_return"],
    } for row in usable if row["information_score"] > 0),
        key=lambda item: (-int(item["consumed_returns"]),
                          -int(item["information_score"]),
                          int(item["instruction_count"] or 10**9),
                          str(item["function"])))
    return {
        "functions_requested": len(rows),
        "functions_measured": len(usable),
        "functions_failed": len(rows) - len(usable),
        **totals,
        "argument_resolution_rate": (
            totals["resolved_exact_arguments"]
            / totals["total_exact_arguments"]
            if totals["total_exact_arguments"] else None),
        "alignment_rate": (totals["aligned_calls"] / totals["binary_calls"]
                           if totals["binary_calls"] else None),
        "functions_with_call_count_mismatch": mismatches,
        "functions_with_consistent_call_counts": len(usable) - len(mismatches),
        "high_information_candidates": ranked,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--shaped-library", required=True, type=Path)
    parser.add_argument("--function", action="append")
    parser.add_argument("--set", type=Path)
    parser.add_argument("--split", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    names = _names(args)
    if not names:
        parser.error("provide at least one --function or --set")

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    library_path = args.shaped_library.expanduser().resolve()
    library = shaped_flywheel.load_library(library_path)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)

    rows = []
    for index, name in enumerate(names, 1):
        print(f"[{index}/{len(names)}] {name}", flush=True)
        metadata = _metadata(conn, name)
        try:
            ws = workspace.bootstrap(repo, name)
            asm = workspace.target_asm(ws, name)
            contracts = callsite_contracts.build(conn, name, asm, library)
        except Exception as exc:
            rows.append({
                "function": name, "metadata": metadata,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            print(f"  error: {rows[-1]['error']}", flush=True)
            continue
        score = _information_score(contracts)
        rows.append({
            "function": name,
            "metadata": metadata,
            "information_score": score,
            "contracts": contracts,
        })
        coverage = contracts["coverage"]
        print(
            f"  calls={coverage['binary_calls']} "
            f"exact-contracts={coverage['exact_callee_contracts']} "
            f"resolved-args={coverage['resolved_exact_arguments']}/"
            f"{coverage['total_exact_arguments']} "
            f"consumed-returns={coverage['exact_calls_with_consumed_return']} "
            f"info={score}", flush=True)

    receipt = {
        "schema_version": 1,
        "kind": "callsite_contract_coverage_pilot",
        "split": args.split,
        "shaped_library": {
            "path": str(library_path),
            "digest": library["digest"],
        },
        "rows": rows,
        "aggregate": aggregate(rows),
        "created_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    conn.close()
    print(f"receipt: {args.out}")
    print(json.dumps(receipt["aggregate"], indent=2))


if __name__ == "__main__":
    main()
