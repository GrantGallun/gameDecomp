"""Build a token-budgeted wavefront plan from frozen experiment receipts.

This module never calls a model.  It first audits what prior pilots spent, then
selects only parents where exact-leaf contracts are aligned, fully bound, and
capable of constraining caller code.  The resulting manifest reserves one
bounded repair draw per selected parent; deterministic replay and callsite
validation happen before that reservation is spent.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import time


SCHEMA_VERSION = 1


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def audit_generation_receipts(paths: list[Path]) -> dict[str, object]:
    """Account for measured model work without inventing missing telemetry."""
    rows: list[dict] = []
    receipts = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        current = payload.get("results")
        if not isinstance(current, list):
            raise ValueError(f"{path} has no results list")
        rows.extend(row for row in current if isinstance(row, dict))
        receipts.append({
            "path": str(path),
            "digest": _digest(path),
            "kind": payload.get("kind"),
            "model": payload.get("model"),
            "draws": len(current),
        })

    generated = [int(row.get("generation_tokens") or 0) for row in rows]
    prompt_estimates = [math.ceil(int(row.get("prompt_chars") or 0) / 4)
                        for row in rows]
    by_arm: dict[str, dict[str, int]] = defaultdict(
        lambda: {"draws": 0, "generated_tokens": 0,
                 "compiled": 0, "exact": 0})
    for row, tokens in zip(rows, generated):
        arm = str(row.get("arm") or "unknown")
        by_arm[arm]["draws"] += 1
        by_arm[arm]["generated_tokens"] += tokens
        by_arm[arm]["compiled"] += int(bool(row.get("compiled")))
        by_arm[arm]["exact"] += int(bool(row.get("exact")))

    return {
        "receipts": receipts,
        "recorded_model_draws": len(rows),
        # Each current pilot performs one tiny server warm-up call which its
        # result list does not include. Keep that debt visible.
        "unrecorded_warmup_calls": len(paths),
        "generated_tokens": sum(generated),
        "estimated_prompt_tokens": sum(prompt_estimates),
        "estimated_total_tokens": sum(generated) + sum(prompt_estimates),
        "generation_seconds": round(sum(
            float(row.get("generation_seconds") or 0.0) for row in rows), 3),
        "compiled_draws": sum(bool(row.get("compiled")) for row in rows),
        "exact_draws": sum(bool(row.get("exact")) for row in rows),
        "median_generated_tokens_per_draw": (
            statistics.median(generated) if generated else None),
        "max_generated_tokens_per_draw": max(generated, default=None),
        "by_arm": dict(sorted(by_arm.items())),
        "telemetry_limits": [
            "prompt tokens are estimated from characters because old pilot receipts did not persist prompt_eval_count",
            "warm-up calls are counted but their tokens were not persisted",
        ],
    }


def _eligibility(row: dict) -> str | None:
    if row.get("error"):
        return "coverage_error"
    contracts = row.get("contracts")
    if not isinstance(contracts, dict):
        return "missing_contract_bundle"
    coverage = contracts.get("coverage")
    if not isinstance(coverage, dict):
        return "missing_coverage"
    if int(coverage.get("binary_calls") or 0) != \
            int(coverage.get("evidence_calls") or 0):
        return "call_count_mismatch"
    if int(coverage.get("exact_callee_contracts") or 0) == 0:
        return "no_exact_leaf_contract"
    if int(coverage.get("resolved_exact_arguments") or 0) != \
            int(coverage.get("total_exact_arguments") or 0):
        return "unresolved_exact_leaf_arguments"
    if int(row.get("information_score") or 0) <= 0:
        return "no_caller_relevant_information"
    return None


def build_plan(coverage_receipt: dict, *, generation_token_budget: int,
               repair_token_cap: int, max_targets: int) -> dict[str, object]:
    """Select a small ready frontier by information, closeness, then size."""
    if generation_token_budget < 0 or repair_token_cap <= 0 or max_targets < 0:
        raise ValueError("budgets must be non-negative and repair cap positive")
    rows = coverage_receipt.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage receipt has no rows list")

    eligible, excluded = [], []
    reasons = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        reason = _eligibility(row)
        if reason:
            reasons[reason] += 1
            excluded.append({"function": row.get("function"), "reason": reason})
        else:
            eligible.append(row)
    eligible.sort(key=lambda row: (
        -int(row["contracts"]["coverage"].get(
            "exact_calls_with_consumed_return") or 0),
        -int(row.get("information_score") or 0),
        -float(row.get("metadata", {}).get("historical_best_score") or 0.0),
        int(row.get("metadata", {}).get("instruction_count") or 10**9),
        str(row.get("function")),
    ))

    selected, budget_deferred = [], []
    remaining = generation_token_budget
    for row in eligible:
        name = str(row["function"])
        if len(selected) >= max_targets or remaining < repair_token_cap:
            budget_deferred.append({
                "function": name,
                "reason": ("max_targets" if len(selected) >= max_targets
                           else "generation_token_budget"),
            })
            continue
        c = row["contracts"]["coverage"]
        selected.append({
            "function": name,
            "information_score": int(row.get("information_score") or 0),
            "instruction_count": row.get("metadata", {}).get(
                "instruction_count"),
            "historical_best_score": float(row.get("metadata", {}).get(
                "historical_best_score") or 0.0),
            "exact_leaf_calls": int(c.get("exact_callee_contracts") or 0),
            "resolved_exact_arguments": int(
                c.get("resolved_exact_arguments") or 0),
            "consumed_exact_returns": int(
                c.get("exact_calls_with_consumed_return") or 0),
            "reserved_generation_tokens": repair_token_cap,
            "execution": [
                {"stage": "validate_stored_candidate", "model_tokens": 0},
                {"stage": "repair_only_contract_mismatches",
                 "model_tokens": f"0..{repair_token_cap}",
                 "condition": "run only if deterministic validation fails"},
                {"stage": "compile_score_and_revalidate", "model_tokens": 0},
                {"stage": "promote_parent",
                 "condition": "byte oracle exact; otherwise retain receipt only",
                 "model_tokens": 0},
            ],
        })
        remaining -= repair_token_cap

    return {
        "policy": {
            "ordering": [
                "consumed exact-leaf returns descending",
                "caller information score descending",
                "historical best score descending",
                "instruction count ascending",
            ],
            "proposal": "one mismatch-only repair draw, never a fresh factorial",
            "promotion": "target compiler plus byte-exact oracle only",
            "semantic_names": "optional hypotheses; never override binary facts",
        },
        "budget": {
            "generation_tokens": generation_token_budget,
            "repair_token_cap_per_target": repair_token_cap,
            "reserved": sum(int(row["reserved_generation_tokens"])
                            for row in selected),
            "unreserved": remaining,
            "max_targets": max_targets,
        },
        "frontier": selected,
        "frontier_size": len(selected),
        "eligible_functions": len(eligible),
        "excluded_reason_counts": dict(sorted(reasons.items())),
        "excluded": excluded,
        "budget_deferred": budget_deferred,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--generation-receipt", action="append", type=Path,
                        default=[])
    parser.add_argument("--generation-token-budget", type=int, default=2400)
    parser.add_argument("--repair-token-cap", type=int, default=1200)
    parser.add_argument("--max-targets", type=int, default=2)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "token_budgeted_wavefront_plan",
        "inputs": {
            "coverage": {"path": str(args.coverage),
                         "digest": _digest(args.coverage)},
            "generation_receipts": [
                {"path": str(path), "digest": _digest(path)}
                for path in args.generation_receipt],
        },
        "prior_cost": audit_generation_receipts(args.generation_receipt),
        "plan": build_plan(
            coverage,
            generation_token_budget=args.generation_token_budget,
            repair_token_cap=args.repair_token_cap,
            max_targets=args.max_targets),
        "created_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"receipt: {args.out}")
    print(json.dumps({
        "prior_model_draws": receipt["prior_cost"]["recorded_model_draws"],
        "prior_generated_tokens": receipt["prior_cost"]["generated_tokens"],
        "prior_exact_draws": receipt["prior_cost"]["exact_draws"],
        "frontier": [row["function"] for row in receipt["plan"]["frontier"]],
        "reserved_generation_tokens": receipt["plan"]["budget"]["reserved"],
    }, indent=2))


if __name__ == "__main__":
    main()
