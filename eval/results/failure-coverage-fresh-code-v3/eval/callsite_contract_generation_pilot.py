"""Paired generation test for deterministic interprocedural callsite contracts.

Unlike the earlier callee-body experiments, the treatment contains no completed
function implementation.  It adds only frozen ABI/effect contracts bound to
caller-local argument and return-use identities.  Baseline and treatment use
the same model, temperature, code-draw budget, byte oracle, and alternating
order.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import callee_context_pilot, wavefront_flywheel_pilot
from solver import callsite_contracts, llm, pipeline, refine, shaped_flywheel, workspace


ARMS = ("baseline", "contracts")


def _arm_order(function_index: int, draw: int) -> tuple[str, str]:
    return ARMS if (function_index + draw) % 2 else tuple(reversed(ARMS))


def _stats(rows: list[dict]) -> dict[str, object]:
    by_function: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_function[str(row["function"])].append(row)
    best = {name: max(float(row["score"]) for row in function_rows)
            for name, function_rows in by_function.items()}
    exact_functions = sorted(
        name for name, function_rows in by_function.items()
        if any(bool(row["exact"]) for row in function_rows))
    validations = [row.get("leaf_contract_validation") for row in rows
                   if isinstance(row.get("leaf_contract_validation"), dict)]
    valid_rows = [row for row in rows
                  if isinstance(row.get("leaf_contract_validation"), dict)
                  and row["leaf_contract_validation"].get("passed")]
    result = {
        "draws": len(rows),
        "compiled_draws": sum(bool(row["compiled"]) for row in rows),
        "exact_draws": sum(bool(row["exact"]) for row in rows),
        "exact_functions": exact_functions,
        "best_by_function": best,
        "mean_best_score": round(sum(best.values()) / len(best), 6)
        if best else 0.0,
        "mean_end_to_end_score": round(
            sum(float(row["score"]) for row in rows) / len(rows), 6)
        if rows else 0.0,
    }
    if validations:
        result.update({
            "leaf_contract_validated_draws": len(validations),
            "leaf_contract_valid_draws": sum(
                bool(row.get("passed")) for row in validations),
            "leaf_contract_violating_draws": sum(
                not bool(row.get("passed")) for row in validations),
            "best_leaf_contract_valid_score": max(
                (float(row["score"]) for row in valid_rows), default=0.0),
        })
    return result


def _candidate_artifact(ws: Path, result: dict, arm: str,
                        draw: int) -> Path | None:
    if not result.get("compiled"):
        return None
    stem = result.get("candidate_artifact_stem")
    if not isinstance(stem, str) or not stem:
        return None
    path = ws / f"{stem}_object_dump_normalized.s"
    return path if path.exists() else None


def _attach_leaf_validation(result: dict, ws: Path, bundle: dict,
                            arm: str, draw: int) -> None:
    path = _candidate_artifact(ws, result, arm, draw)
    if path is None:
        return
    result["candidate_asm_artifact"] = str(path)
    result["candidate_asm_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["leaf_contract_validation"] = \
        callsite_contracts.validate_candidate_asm(
            bundle, path.read_text(encoding="utf-8", errors="replace"))


def assessment(results: list[dict]) -> dict[str, object]:
    stats = {arm: _stats([row for row in results if row["arm"] == arm])
             for arm in ARMS}
    baseline_exact = set(stats["baseline"]["exact_functions"])
    contract_exact = set(stats["contracts"]["exact_functions"])
    if contract_exact - baseline_exact:
        status = "contract_exact_gain_observed_needs_replication"
    elif baseline_exact - contract_exact:
        status = "contract_exact_regression_observed_needs_replication"
    else:
        status = "inconclusive_no_exact_difference"
    per_function = {}
    functions = sorted({str(row["function"]) for row in results})
    for function in functions:
        base = _stats([row for row in results
                       if row["function"] == function and row["arm"] == "baseline"])
        treatment = _stats([row for row in results
                            if row["function"] == function and row["arm"] == "contracts"])
        per_function[function] = {
            "baseline": base,
            "contracts": treatment,
            "best_score_delta": round(
                float(treatment["mean_best_score"])
                - float(base["mean_best_score"]), 6),
        }
    return {
        "status": status,
        "scope": "bounded DEV pilot; exact is primary and score-only movement is secondary",
        "arms": stats,
        "per_function": per_function,
        "mean_best_score_delta": round(
            float(stats["contracts"]["mean_best_score"])
            - float(stats["baseline"]["mean_best_score"]), 6),
        "mean_end_to_end_score_delta": round(
            float(stats["contracts"]["mean_end_to_end_score"])
            - float(stats["baseline"]["mean_end_to_end_score"]), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--shaped-library", required=True, type=Path)
    parser.add_argument("--function", action="append", required=True)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    names = list(dict.fromkeys(args.function))

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    library_path = args.shaped_library.expanduser().resolve()
    library = shaped_flywheel.load_library(library_path)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    endpoint = llm.host()
    llm.generate(endpoint, args.model, "Return the JSON object {}.",
                 timeout=args.timeout, think="false",
                 num_thread=args.num_thread, num_predict=32,
                 temperature=0.0)

    function_receipts = []
    results = []
    run_id = f"callsite-contract-pilot-{int(time.time())}"
    for function_index, name in enumerate(names):
        ws = workspace.bootstrap(repo, name)
        asm = workspace.target_asm(ws, name)
        draft = workspace.m2c_draft(ws)
        bundle = callsite_contracts.build(conn, name, asm, library)
        block = callsite_contracts.render(bundle)
        if not block:
            raise SystemExit(f"{name} has no renderable exact callsite contract")
        baseline = pipeline.build_prompt(
            repo, conn, name, asm, draft, "reshape", False)
        treatment = wavefront_flywheel_pilot._inject(baseline, block)
        prompts = {"baseline": baseline, "contracts": treatment}
        for prompt in prompts.values():
            workspace.assert_uncontaminated(prompt, repo, name)
        if any(str(node.get("exact_source") or "") in treatment
               for node in library["nodes"].values()
               if isinstance(node, dict) and node.get("exact_source")):
            raise RuntimeError("completed callee body leaked into contract prompt")

        prior = conn.execute(
            "select max(a.score) from attempts a join functions f "
            "on f.addr=a.func_addr where f.name=? and a.compiled=1 "
            "and a.strategy not like 'callsite-contract-pilot-%'",
            (name,)).fetchone()
        function_receipts.append({
            "function": name,
            "prior_best_score": float((prior or [0.0])[0] or 0.0),
            "contracts": bundle,
            "contract_block": block,
            "prompt_chars": {arm: len(prompt) for arm, prompt in prompts.items()},
            "prompt_sha256": {
                arm: hashlib.sha256(prompt.encode()).hexdigest()
                for arm, prompt in prompts.items()
            },
        })

        for draw in range(1, args.samples + 1):
            order = _arm_order(function_index, draw)
            print(f"{name} draw {draw}: {', '.join(order)}", flush=True)
            for arm in order:
                result = callee_context_pilot._score_arm(
                    repo, conn, ws, name, endpoint, args.model, prompts[arm],
                    arm, args.timeout, args.think, args.num_thread, run_id,
                    draw, args.temperature,
                    strategy_prefix="callsite-contract-pilot")
                _attach_leaf_validation(result, ws, bundle, arm, draw)
                result["function"] = name
                result["generation_position"] = order.index(arm) + 1
                results.append(result)
                verdict = ("EXACT" if result["exact"]
                           else f"{result['score']:.3f}%" if result["compiled"]
                           else "did not compile")
                validation = result.get("leaf_contract_validation")
                if isinstance(validation, dict) and not validation.get("passed"):
                    verdict += " [LEAF-CONTRACT VIOLATION]"
                print(f"  {arm}: {verdict}", flush=True)

    receipt = {
        "schema_version": 1,
        "kind": "callsite_contract_generation_pilot",
        "functions": function_receipts,
        "model": args.model,
        "temperature": args.temperature,
        "samples_per_arm": args.samples,
        "arm_order_policy": "alternating and counterbalanced across functions",
        "shaped_library": {
            "path": str(library_path),
            "digest": library["digest"],
        },
        "results": results,
        "assessment": assessment(results),
        "created_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    conn.close()
    print(f"receipt: {args.out}")
    print(f"status: {receipt['assessment']['status']}")


if __name__ == "__main__":
    main()
