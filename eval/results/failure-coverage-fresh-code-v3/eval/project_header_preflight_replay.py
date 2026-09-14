"""Zero-token post-hoc replay of failed two-lane leaf candidates.

This does not alter the preregistered receipt.  It replays only its non-exact
Lane A rows through the newly added project-header/empty-return preflight.  If
preflight does not solve a row, the original cached generation may be reused;
no new model tokens are permitted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

from eval import abi_leaf_pilot as leaf_pilot
from solver import refine, structgen, workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    config = baseline["configuration"]
    failed = [row for row in baseline["lane_a_leaf_results"]
              if not row.get("exact")]
    run_id = f"project-header-preflight-{int(time.time())}"
    receipt = {
        "schema_version": 1,
        "kind": "project_header_preflight_replay",
        "run_id": run_id,
        "baseline_receipt": str(args.baseline),
        "methodology": {
            "post_hoc": True,
            "new_model_calls_allowed": False,
            "target_source_read": False,
            "replayed_scope": "only non-exact Lane A rows",
        },
        "input_failures": [row["function"] for row in failed],
        "results": [],
        "parent_result": {},
        "parent_struct_evidence": {},
        "inferred_transfer": [],
        "started_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    repo = args.repo.expanduser().resolve()
    conn = sqlite3.connect(args.db.expanduser().resolve(), timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    for row in failed:
        result = leaf_pilot.solve_leaf(
            repo, conn, "", {"function": row["function"]},
            model=str(config["model"]), timeout=900,
            think=str(config["think"]), num_thread=8,
            num_predict=int(config["leaf_num_predict"]),
            temperature=float(config["temperature"]),
            seed=int(row["seed"]), cache_dir=args.cache_dir,
            run_id=run_id, repair_candidates=40, layout_candidates=12,
            cache_only=True,
            cache_namespace="two-lane-leaf-harvest-v1",
            strategy_prefix="project-header-replay")
        receipt["results"].append(result)
        args.out.write_text(json.dumps(receipt, indent=2) + "\n",
                            encoding="utf-8")
        print(f"{row['function']}: "
              f"{'EXACT' if result.get('exact') else result.get('best_score')}",
              flush=True)

    parent_baseline = baseline.get("lane_b_parent_baseline", {})
    if parent_baseline and not parent_baseline.get("compiled"):
        parent = str(config["transfer_parent"])
        receipt["parent_struct_evidence"] = structgen.layout(conn, parent)
        parent_result = leaf_pilot.solve_leaf(
            repo, conn, "", {"function": parent},
            model=str(config["model"]), timeout=900,
            think=str(config["think"]), num_thread=8,
            num_predict=int(config["parent_num_predict"]),
            temperature=float(config["temperature"]),
            seed=int(config["seed_base"]) + len(
                baseline["lane_a_leaf_results"]),
            cache_dir=args.cache_dir, run_id=run_id,
            repair_candidates=40, layout_candidates=12, cache_only=True,
            cache_namespace="two-lane-parent-baseline-v1",
            strategy_prefix="project-header-parent-replay")
        parent_result["compiled"] = any(
            bool(attempt.get("compiled"))
            for attempt in parent_result.get("attempts", []))
        receipt["parent_result"] = parent_result
        print(f"{parent}: " + (
            "EXACT" if parent_result.get("exact") else
            f"{float(parent_result.get('best_score') or 0):.3f}%"
            if parent_result.get("compiled") else "did not compile"),
            flush=True)

        if parent_result.get("compiled"):
            heldout = leaf_pilot.heldout_names(
                [Path(path) for path in config.get("heldout_sets", [])])
            child = str(config["transfer_child"])
            child_ws = workspace.bootstrap(repo, child)
            child_asm = workspace.target_asm(child_ws, child)
            receipt["inferred_transfer"] = \
                leaf_pilot.measure_inferred_contract_transfer(
                    repo, conn, [{
                        "function": child,
                        "signals": leaf_pilot.abi_signals(child_asm),
                    }], heldout, run_id=run_id, max_parents_per_leaf=1)
    exact = [row for row in receipt["results"] if row.get("exact")]
    receipt["assessment"] = {
        "replayed": len(failed),
        "new_exact": len(exact),
        "new_exact_functions": [row["function"] for row in exact],
        "model_calls": sum(int(row.get("model_calls") or 0)
                           for row in receipt["results"]),
        "charged_generation_tokens": sum(
            int(row.get("charged_generation_tokens") or 0)
            for row in receipt["results"]),
        "original_exact": sum(
            bool(row.get("exact"))
            for row in baseline["lane_a_leaf_results"]),
        "combined_exact": sum(
            bool(row.get("exact"))
            for row in baseline["lane_a_leaf_results"]) + len(exact),
        "parent_baseline_compiled": bool(
            receipt["parent_result"].get("compiled")),
        "parent_baseline_score": float(
            receipt["parent_result"].get("best_score") or 0),
        "inferred_variants_compiled": sum(
            bool(variant.get("compiled"))
            for transfer in receipt["inferred_transfer"]
            for caller in transfer.get("callers", [])
            for variant in caller.get("variants", [])),
        "inferred_assembly_deltas": sum(
            bool(variant.get("assembly_changed"))
            for transfer in receipt["inferred_transfer"]
            for caller in transfer.get("callers", [])
            for variant in caller.get("variants", [])),
    }
    receipt["completed_at"] = int(time.time())
    args.out.write_text(json.dumps(receipt, indent=2) + "\n",
                        encoding="utf-8")
    conn.close()
    print(json.dumps(receipt["assessment"], indent=2))
    print(f"receipt: {args.out}")


if __name__ == "__main__":
    main()
