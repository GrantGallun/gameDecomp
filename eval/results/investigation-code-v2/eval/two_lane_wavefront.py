"""Matchability-first exact harvest plus one transfer-surface pilot.

Lane A freezes never-attempted tiny DEV leaves using target assembly only:
at most 20 instructions, no calls, and no control-flow instruction except the
return.  One seeded cached draw plus bounded deterministic repair is allowed
per leaf.

Lane B creates a compiling baseline for one parent only when none exists, then
shadow-tests binary-compatible narrow-return hypotheses outside the verified
prototype store.  Exact child signatures from Lane A are propagated normally.

No target source is read.  Every frozen held-out name is subtracted before a
workspace is bootstrapped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import abi_leaf_pilot as leaf_pilot
from solver import llm, refine, workspace


CONTROL_FLOW = {
    "b", "bal", "beq", "beql", "bne", "bnel", "beqz", "bnez",
    "bgez", "bgezal", "bgtz", "blez", "bltz", "bltzal",
    "bc1f", "bc1fl", "bc1t", "bc1tl", "j", "jal", "jalr",
}
CALLS = {"bal", "jal", "jalr", "bgezal", "bltzal"}
MEMORY = {
    "lb", "lbu", "lh", "lhu", "lw", "lwl", "lwr", "ld",
    "sb", "sh", "sw", "swl", "swr", "sd", "lwc1", "ldc1",
    "swc1", "sdc1",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def matchability_signals(asm: str) -> dict[str, object]:
    """Cheap target-only features for a straight-line exact-harvest prior."""
    opcodes = []
    for raw in asm.splitlines():
        line = re.sub(r"^\s*/\*.*?\*/\s*", "", raw).strip()
        if not line or line.endswith(":") or line.startswith(
                ("glabel ", "endlabel ", ".")):
            continue
        match = re.match(r"([A-Za-z][A-Za-z0-9.]*)\b", line)
        if match:
            opcodes.append(match.group(1).lower())
    branches = [opcode for opcode in opcodes if opcode in CONTROL_FLOW]
    calls = [opcode for opcode in opcodes if opcode in CALLS]
    returns = sum(opcode == "jr" for opcode in opcodes)
    return {
        "decoded_instructions": len(opcodes),
        "control_flow_instructions": branches,
        "calls": calls,
        "return_count": returns,
        "straight_line": not branches and not calls and returns == 1,
        "stack_activity": bool(re.search(r"\(\$?sp\)|\bsp\s*,", asm, re.I)),
        "memory_instructions": sum(opcode in MEMORY for opcode in opcodes),
        "floating_instructions": sum("." in opcode or "c1" in opcode
                                     for opcode in opcodes),
        "relocations": len(re.findall(r"%(?:hi|lo)\(", asm)),
    }


def select_cohort(repo: Path, conn: sqlite3.Connection, leaf_set: dict,
                  heldout: set[str], *, max_leaves: int,
                  max_insns: int) -> tuple[list[dict], dict]:
    attempted = {str(name) for (name,) in conn.execute(
        "select distinct f.name from attempts a join functions f "
        "on f.addr=a.func_addr")}
    selected_pool = []
    rejected = {
        "heldout": [], "already_attempted": [], "too_large": [],
        "not_straight_line": [], "bootstrap_errors": [],
    }
    for item in leaf_set.get("dev", []):
        function = str(item["function"])
        if function in heldout:
            rejected["heldout"].append(function)
            continue
        if function in attempted:
            rejected["already_attempted"].append(function)
            continue
        insns = int(item.get("insns") or 0)
        if not insns or insns > max_insns:
            rejected["too_large"].append(function)
            continue
        try:
            ws = workspace.bootstrap(repo, function)
            asm = workspace.target_asm(ws, function)
        except Exception as exc:
            rejected["bootstrap_errors"].append({
                "function": function,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            })
            continue
        signals = matchability_signals(asm)
        if not signals["straight_line"]:
            rejected["not_straight_line"].append(function)
            continue
        selected_pool.append({
            "function": function,
            "tier": item.get("tier"),
            "insns": insns,
            "callers": int(item.get("callers") or 0),
            "matchability_signals": signals,
            "abi_signals": leaf_pilot.abi_signals(asm),
            "target_asm_sha256": _sha(asm),
        })
    selected_pool.sort(key=lambda row: (
        int(row["insns"]),
        bool(row["matchability_signals"]["stack_activity"]),
        int(row["matchability_signals"]["memory_instructions"]),
        int(row["matchability_signals"]["floating_instructions"]),
        str(row["function"]),
    ))
    selected = [{**row, "cohort_index": index}
                for index, row in enumerate(selected_pool[:max_leaves])]
    audit = {
        "input_dev_rows": len(leaf_set.get("dev", [])),
        "eligible_before_cap": len(selected_pool),
        "selected": len(selected),
        "rejected_counts": {key: len(value) for key, value in rejected.items()},
        "rejected": rejected,
        "full_ranking": selected_pool,
    }
    return selected, audit


def assessment(selected: list[dict], leaf_results: list[dict],
               exact_transfer: list[dict], parent_baseline: dict,
               inferred_transfer: list[dict]) -> dict[str, object]:
    exact = [row for row in leaf_results if row.get("exact")]
    tokens = sum(int(row.get("charged_generation_tokens") or 0)
                 for row in leaf_results)
    calls = sum(int(row.get("model_calls") or 0) for row in leaf_results)
    compiled = sum(any(bool(attempt.get("compiled"))
                       for attempt in row.get("attempts", []))
                   for row in leaf_results)
    exact_callers = [caller for row in exact_transfer
                     for caller in row.get("callers", [])]
    exact_variants = [variant for caller in exact_callers
                      for variant in caller.get("variants", [])]
    inferred_callers = [caller for row in inferred_transfer
                        for caller in row.get("callers", [])]
    inferred_variants = [variant for caller in inferred_callers
                         for variant in caller.get("variants", [])]
    parent_exact = bool(parent_baseline.get("exact"))
    total_exact = len(exact) + int(parent_exact)
    return {
        "status": ("exact_harvest_target_met" if len(exact) >= 3
                   else "exact_harvest_target_missed"),
        "lane_a": {
            "selected": len(selected),
            "compiled_leaves": compiled,
            "exact_leaves": len(exact),
            "exact_functions": [row["function"] for row in exact],
            "model_calls": calls,
            "charged_generation_tokens": tokens,
            "exact_per_1000_tokens": round(
                1000.0 * len(exact) / tokens, 6) if tokens else 0.0,
            "leaf_wall_seconds": round(sum(
                float(row.get("wall_seconds") or 0) for row in leaf_results), 3),
            "stored_callers_examined": len(exact_callers),
            "prototype_variants_compiled": sum(
                bool(row.get("compiled")) for row in exact_variants),
            "parent_assembly_deltas": sum(
                bool(row.get("assembly_changed")) for row in exact_variants),
            "exact_parent_promotions": sum(
                bool(row.get("exact")) for row in exact_variants),
        },
        "lane_b": {
            "parent": parent_baseline.get("function"),
            "baseline_source": parent_baseline.get("baseline_source"),
            "baseline_compiled": bool(parent_baseline.get("compiled")),
            "baseline_exact": parent_exact,
            "baseline_score": float(parent_baseline.get("best_score") or 0),
            "model_calls": int(parent_baseline.get("model_calls") or 0),
            "charged_generation_tokens": int(
                parent_baseline.get("charged_generation_tokens") or 0),
            "inferred_callers_examined": len(inferred_callers),
            "inferred_variants_compiled": sum(
                bool(row.get("compiled")) for row in inferred_variants),
            "inferred_assembly_deltas": sum(
                bool(row.get("assembly_changed")) for row in inferred_variants),
            "inferred_score_improvements": sum(
                float(row.get("score_delta") or 0) > 0
                for row in inferred_variants),
            "inferred_exact_promotions": sum(
                bool(row.get("exact")) for row in inferred_variants),
        },
        "new_exact_functions_total": total_exact,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--leaf-set", required=True, type=Path)
    parser.add_argument("--heldout-set", action="append", type=Path, default=[])
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument(
        "--resume", action="store_true",
        help="resume the frozen cohort and partial results from --out")
    parser.add_argument("--max-leaves", type=int, default=16)
    parser.add_argument("--max-insns", type=int, default=20)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--think", default="low")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--leaf-num-predict", type=int, default=1200)
    parser.add_argument("--parent-num-predict", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=202609011600)
    parser.add_argument("--repair-candidates", type=int, default=40)
    parser.add_argument("--layout-candidates", type=int, default=12)
    parser.add_argument("--transfer-child", default="randomNextObject")
    parser.add_argument("--transfer-parent", default="updateRacePickupIdle")
    args = parser.parse_args()
    if args.resume and args.scan_only:
        parser.error("--resume and --scan-only are mutually exclusive")
    if args.leaf_num_predict > 1200 or args.parent_num_predict > 1600:
        parser.error("generation caps exceed the preregistered budget")

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    out = args.out.expanduser().resolve()
    leaf_set = json.loads(args.leaf_set.read_text(encoding="utf-8"))
    heldout = leaf_pilot.heldout_names(args.heldout_set)
    if args.transfer_child in heldout or args.transfer_parent in heldout:
        parser.error("transfer edge intersects a frozen held-out set")
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    if args.resume:
        if not out.exists():
            parser.error(f"resume receipt does not exist: {out}")
        receipt = json.loads(out.read_text(encoding="utf-8"))
        if receipt.get("kind") != "two_lane_wavefront_pilot":
            parser.error(f"not a two-lane receipt: {out}")
        selected = receipt["selected"]
        scan = receipt["scan"]
        run_id = receipt["run_id"]
        frozen = receipt.get("configuration", {})
        expected = {
            "repo": str(repo), "db": str(db),
            "transfer_child": args.transfer_child,
            "transfer_parent": args.transfer_parent,
            "seed_base": args.seed_base,
        }
        mismatches = {
            key: (frozen.get(key), value)
            for key, value in expected.items()
            if frozen.get(key) != value
        }
        if mismatches:
            parser.error(f"resume configuration mismatch: {mismatches}")
    else:
        selected, scan = select_cohort(
            repo, conn, leaf_set, heldout, max_leaves=args.max_leaves,
            max_insns=args.max_insns)
        run_id = f"two-lane-wavefront-{int(time.time())}"
        receipt = {
        "schema_version": 1,
        "kind": "two_lane_wavefront_pilot",
        "run_id": run_id,
        "pre_registration": {
            "lane_a_prediction": "at least 3 of 16 selected leaves become oracle-exact",
            "lane_b_prediction": "updateRacePickupIdle obtains a compiling baseline; an inferred u8 or u32 return hypothesis changes its assembly",
            "leaf_generation_budget": args.max_leaves * args.leaf_num_predict,
            "parent_generation_budget": args.parent_num_predict,
            "total_generation_budget": (
                args.max_leaves * args.leaf_num_predict
                + args.parent_num_predict),
            "one_seeded_draw_per_selected_leaf": True,
            "parent_draw_only_if_no_compiling_baseline": True,
            "heldout_excluded_before_bootstrap": True,
            "no_target_source": True,
        },
        "configuration": {
            "repo": str(repo), "db": str(db),
            "leaf_set": str(args.leaf_set),
            "heldout_sets": [str(path) for path in args.heldout_set],
            "model": args.model, "think": args.think,
            "temperature": args.temperature,
            "leaf_num_predict": args.leaf_num_predict,
            "parent_num_predict": args.parent_num_predict,
            "seed_base": args.seed_base,
            "max_insns": args.max_insns,
            "transfer_child": args.transfer_child,
            "transfer_parent": args.transfer_parent,
        },
        "scan": scan,
        "selected": selected,
        "lane_a_leaf_results": [],
        "lane_a_exact_transfer": [],
        "lane_b_parent_baseline": {},
        "lane_b_inferred_transfer": [],
        "started_at": int(time.time()),
        }
        _write(out, receipt)
    print(f"eligible straight-line leaves: {scan['eligible_before_cap']}")
    for index, row in enumerate(selected, 1):
        signals = row["matchability_signals"]
        print(f" {index:2}. {row['function']}  insns={row['insns']} "
              f"memory={signals['memory_instructions']} "
              f"stack={signals['stack_activity']}", flush=True)
    if args.scan_only:
        receipt["assessment"] = {"status": "scan_only",
                                 "selected": len(selected)}
        receipt["completed_at"] = int(time.time())
        _write(out, receipt)
        conn.close()
        print(f"receipt: {out}")
        return
    if len(selected) < args.max_leaves:
        raise SystemExit(
            f"only {len(selected)} eligible leaves; frozen cohort requires "
            f"{args.max_leaves}")

    finished_leaves = {
        str(row.get("function"))
        for row in receipt["lane_a_leaf_results"]
    }
    needs_parent_draw = (
        not receipt.get("lane_b_parent_baseline")
        and leaf_pilot._best_parent_candidate(conn, args.transfer_parent) is None
    )
    endpoint = llm.host() if (
        len(finished_leaves) < len(selected) or needs_parent_draw) else ""
    for index, row in enumerate(selected):
        function = str(row["function"])
        if function in finished_leaves:
            print(f"\n=== lane A {index + 1}/{len(selected)}: {function} "
                  "(receipt replay) ===", flush=True)
            continue
        print(f"\n=== lane A {index + 1}/{len(selected)}: {function} ===",
              flush=True)
        try:
            result = leaf_pilot.solve_leaf(
                repo, conn, endpoint, row, model=args.model,
                timeout=args.timeout, think=args.think,
                num_thread=args.num_thread,
                num_predict=args.leaf_num_predict,
                temperature=args.temperature,
                seed=args.seed_base + index,
                cache_dir=args.cache_dir, run_id=run_id,
                repair_candidates=args.repair_candidates,
                layout_candidates=args.layout_candidates,
                cache_only=False,
                cache_namespace="two-lane-leaf-harvest-v1",
                strategy_prefix="two-lane-leaf")
        except Exception as exc:
            result = {
                "function": function, "exact": False, "best_score": 0.0,
                "model_calls": 0, "charged_generation_tokens": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:600]}",
            }
        receipt["lane_a_leaf_results"].append(result)
        _write(out, receipt)
        verdict = "EXACT" if result.get("exact") else \
            f"{float(result.get('best_score') or 0):.3f}%"
        print(f"  -> {verdict}; tokens="
              f"{result.get('charged_generation_tokens', 0)}; "
              f"wall={result.get('wall_seconds', 0)}s", flush=True)

    receipt["lane_a_exact_transfer"] = leaf_pilot.measure_parent_transfer(
        repo, conn, receipt["lane_a_leaf_results"], heldout,
        run_id=run_id, max_parents_per_leaf=12)
    _write(out, receipt)

    print(f"\n=== lane B parent baseline: {args.transfer_parent} ===", flush=True)
    if receipt.get("lane_b_parent_baseline"):
        parent_result = receipt["lane_b_parent_baseline"]
    else:
        existing = leaf_pilot._best_parent_candidate(conn, args.transfer_parent)
        if existing is not None:
            parent_result = {
                "function": args.transfer_parent,
                "baseline_source": "stored",
                "compiled": True, "exact": False,
                "best_score": existing["score"],
                "origin_attempt_id": existing["attempt_id"],
                "model_calls": 0, "charged_generation_tokens": 0,
            }
        else:
            try:
                parent_result = leaf_pilot.solve_leaf(
                    repo, conn, endpoint, {"function": args.transfer_parent},
                    model=args.model, timeout=args.timeout, think=args.think,
                    num_thread=args.num_thread,
                    num_predict=args.parent_num_predict,
                    temperature=args.temperature,
                    seed=args.seed_base + args.max_leaves,
                    cache_dir=args.cache_dir, run_id=run_id,
                    repair_candidates=args.repair_candidates,
                    layout_candidates=args.layout_candidates,
                    cache_only=False,
                    cache_namespace="two-lane-parent-baseline-v1",
                    strategy_prefix="two-lane-parent")
                parent_result["baseline_source"] = "new_generation"
                parent_result["compiled"] = any(
                    bool(attempt.get("compiled"))
                    for attempt in parent_result.get("attempts", []))
            except Exception as exc:
                parent_result = {
                    "function": args.transfer_parent,
                    "baseline_source": "new_generation",
                    "compiled": False, "exact": False, "best_score": 0.0,
                    "model_calls": 0, "charged_generation_tokens": 0,
                    "error": f"{type(exc).__name__}: {str(exc)[:600]}",
                }
    receipt["lane_b_parent_baseline"] = parent_result
    _write(out, receipt)
    parent_verdict = "EXACT" if parent_result.get("exact") else \
        f"{float(parent_result.get('best_score') or 0):.3f}%" \
        if parent_result.get("compiled") else "did not compile"
    print(f"  -> {parent_verdict}; tokens="
          f"{parent_result.get('charged_generation_tokens', 0)}", flush=True)

    if receipt.get("lane_b_inferred_transfer"):
        inferred_transfer = receipt["lane_b_inferred_transfer"]
    else:
        child_ws = workspace.bootstrap(repo, args.transfer_child)
        child_asm = workspace.target_asm(child_ws, args.transfer_child)
        transfer_selected = [{
            "function": args.transfer_child,
            "signals": leaf_pilot.abi_signals(child_asm),
        }]
        inferred_transfer = leaf_pilot.measure_inferred_contract_transfer(
            repo, conn, transfer_selected, heldout, run_id=run_id,
            max_parents_per_leaf=1)
    receipt["lane_b_inferred_transfer"] = inferred_transfer
    receipt["assessment"] = assessment(
        selected, receipt["lane_a_leaf_results"],
        receipt["lane_a_exact_transfer"], parent_result,
        receipt["lane_b_inferred_transfer"])
    receipt["completed_at"] = int(time.time())
    _write(out, receipt)
    conn.close()
    print("\n" + json.dumps(receipt["assessment"], indent=2), flush=True)
    print(f"receipt: {out}")


if __name__ == "__main__":
    main()
