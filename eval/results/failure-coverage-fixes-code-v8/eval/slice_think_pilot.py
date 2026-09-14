"""Paired S1 pilot: disable reasoning on sequential slice calls only.

This reuses the exact six-function cohort and sequential prompts from entry 2.
The final composer stays at ``think=low`` in both arms, so the sole treatment
is ``think=false`` versus ``think=low`` on the bounded slice calls.

A cheap capability gate runs first.  If Ollama returns the requested
``think=false`` call through its ``thinking`` field, the flag was not honoured
and the expensive comparison stops immediately.

Example (from the gameDecomp checkout under WSL)::

    python3 -m eval.slice_think_pilot \
      --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \
      --out eval/results/s1_slice_think_pilot.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import time

from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import llm, refine, shifts, workspace


COHORT = (
    "updateRaceSplitscreenSelectPlayerCountIcons",
    "isRacePlayerRespawnSurfaceValid",
    "checkMainMenuSecretCode",
    "getRacePlayerRankingProgress",
    "updateRacePlayerMode16AerialTrick",
    "drawPulsingAssetTableSprite",
)
ARMS = ("think_low", "think_false")
THINK_BY_ARM = {"think_low": "low", "think_false": "false"}
PROBE_PROMPT = """Reply with exactly these two fenced blocks and no prose:
```decls
```
```stmts
S1_OK;
```"""


def arm_order(function_index: int) -> tuple[str, str]:
    """Counterbalance whole-function arm order against server drift."""
    return ARMS if function_index % 2 == 0 else tuple(reversed(ARMS))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def _public(value):
    """Drop raw prompts/responses while retaining their measured metadata."""
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()
                if not key.startswith("_")}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def capability_gate(endpoint: str, model: str, *, timeout: int,
                    num_thread: int) -> dict:
    """Determine whether ``think=false`` yields an actual answer field."""
    # Load the model before timed probes. Keep this call visible in the receipt.
    started = time.perf_counter()
    warm_text, warm_meta = llm.generate(
        endpoint, model, "Reply with OK.", timeout=timeout,
        num_thread=num_thread, num_predict=64, think="low", temperature=0.0)
    warmup = {
        "generation_seconds": round(time.perf_counter() - started, 3),
        "generation_tokens": int(warm_meta.get("eval_count", 0) or 0),
        "done_reason": warm_meta.get("done_reason", ""),
        "fell_back_to_thinking": bool(
            warm_meta.get("_fell_back_to_thinking")),
        "response_chars": len(warm_text),
    }

    probes = []
    for arm in ARMS:
        started = time.perf_counter()
        text, meta = llm.generate(
            endpoint, model, PROBE_PROMPT, timeout=timeout,
            num_thread=num_thread, num_predict=512,
            think=THINK_BY_ARM[arm], temperature=0.0)
        elapsed = time.perf_counter() - started
        probes.append({
            "arm": arm,
            "requested_think": THINK_BY_ARM[arm],
            "generation_seconds": round(elapsed, 3),
            "generation_tokens": int(meta.get("eval_count", 0) or 0),
            "prompt_tokens": int(meta.get("prompt_eval_count", 0) or 0),
            "done_reason": meta.get("done_reason", ""),
            "fell_back_to_thinking": bool(
                meta.get("_fell_back_to_thinking")),
            "valid_fences": bool(
                shifts.DECLS_RE.search(text) and shifts.STMTS_RE.search(text)),
            "contains_marker": "S1_OK;" in text,
            "response_chars": len(text),
            "response_sha256": _digest(text),
        })

    false = next(row for row in probes if row["arm"] == "think_false")
    if false["fell_back_to_thinking"]:
        status = "lever_not_honoured_response_empty_thinking_returned"
        proceed = False
    elif false["done_reason"] == "length":
        status = "think_false_truncated_on_format_probe"
        proceed = False
    elif not false["valid_fences"] or not false["contains_marker"]:
        status = "think_false_failed_format_probe"
        proceed = False
    else:
        status = "think_false_returns_direct_fenced_answer"
        proceed = True
    return {"status": status, "proceed": proceed,
            "warmup": warmup, "probes": probes}


def _arm_stats(slice_calls: list[dict], results: list[dict], arm: str) -> dict:
    calls = [row for row in slice_calls if row["arm"] == arm]
    finals = [row for row in results if row["arm"] == arm]
    seconds = [float(row["generation_seconds"]) for row in calls]
    tokens = [int(row["generation_tokens"]) for row in calls]
    scores = [float(row["score"]) for row in finals]
    compiled_scores = [float(row["score"]) for row in finals
                       if row["compiled"]]
    return {
        "slice_calls": len(calls),
        "slice_generation_seconds": round(sum(seconds), 3),
        "mean_seconds_per_slice": round(statistics.mean(seconds), 3)
        if seconds else 0.0,
        "median_seconds_per_slice": round(statistics.median(seconds), 3)
        if seconds else 0.0,
        "slice_generation_tokens": sum(tokens),
        "mean_tokens_per_slice": round(statistics.mean(tokens), 3)
        if tokens else 0.0,
        "median_tokens_per_slice": round(statistics.median(tokens), 3)
        if tokens else 0.0,
        "refused_slices": sum(bool(row["refused"]) for row in calls),
        "empty_slices": sum(bool(row["empty"]) for row in calls),
        "truncated_slices": sum(bool(row["truncated"]) for row in calls),
        "empty_or_truncated_slices": sum(
            bool(row["empty"] or row["truncated"]) for row in calls),
        "invalid_fence_slices": sum(
            not bool(row["valid_fences"]) for row in calls),
        "fell_back_to_thinking": sum(
            bool(row["fell_back_to_thinking"]) for row in calls),
        "final_draws": len(finals),
        "compiled_finals": sum(bool(row["compiled"]) for row in finals),
        "exact_finals": sum(bool(row["exact"]) for row in finals),
        "mean_end_to_end_score": round(statistics.mean(scores), 6)
        if scores else 0.0,
        "mean_compiled_score": round(statistics.mean(compiled_scores), 6)
        if compiled_scores else 0.0,
    }


def assess(slice_calls: list[dict], results: list[dict]) -> dict:
    """Apply S1's pre-registered speed and quality gates."""
    arms = {arm: _arm_stats(slice_calls, results, arm) for arm in ARMS}
    low, false = arms["think_low"], arms["think_false"]

    def reduction(metric: str) -> float:
        base = float(low[metric])
        return round(100.0 * (base - float(false[metric])) / base, 3) \
            if base else 0.0

    wall_reduction = reduction("mean_seconds_per_slice")
    token_reduction = reduction("mean_tokens_per_slice")
    score_delta = round(
        float(false["mean_end_to_end_score"])
        - float(low["mean_end_to_end_score"]), 6)

    if false["empty_or_truncated_slices"] > \
            low["empty_or_truncated_slices"]:
        status = "quality_regression_empty_or_truncated_keep_thinking"
    elif score_delta < -10.0:
        status = "quality_regression_over_10_points_keep_thinking"
    elif false["invalid_fence_slices"] > low["invalid_fence_slices"]:
        status = "format_regression_keep_thinking"
    elif false["fell_back_to_thinking"]:
        status = "think_false_not_reliably_honoured"
    elif wall_reduction >= 40.0:
        status = "s1_supported_speed_gate_met_without_observed_regression"
    else:
        status = "s1_not_useful_speed_gate_missed"

    return {
        "status": status,
        "scope": "paired six-function DEV pilot; one final draw per arm/function",
        "pre_registered_thresholds": {
            "minimum_wall_clock_reduction_percent": 40.0,
            "maximum_mean_score_loss_points": 10.0,
            "empty_or_truncated_increase_allowed": 0,
            "invalid_fence_increase_allowed": 0,
        },
        "arms": arms,
        "wall_clock_reduction_percent": wall_reduction,
        "generated_token_reduction_percent": token_reduction,
        "mean_end_to_end_score_delta_false_minus_low": score_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--function", action="append",
                        help="override cohort; repeat for multiple functions")
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.5,
                        help="entry-2 used 0.5")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--force-full", action="store_true",
                        help="run full cohort even when capability gate fails")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    out = args.out.expanduser().resolve()
    functions = tuple(args.function or COHORT)
    run_id = f"s1-{int(time.time())}"
    receipt = {
        "schema_version": 1,
        "kind": "slice_think_paired_pilot",
        "run_id": run_id,
        "pre_registration": {
            "cohort": list(functions),
            "control": "think=low on slices",
            "treatment": "think=false on slices",
            "held_constant": [
                "entry-2 slice and final prompts",
                "loop-safe ~45-instruction regions",
                "gpt-oss:20b unless CLI overrides it",
                "temperature=0.5 unless CLI overrides it",
                "think=low on final composition in both arms",
                "one final draw per arm and function",
            ],
            "prediction": (
                "mean wall-clock per slice falls at least 40% with no "
                "increase in fence, empty, or truncation failures"),
            "kill_conditions": [
                "empty-or-truncated slice count rises at all",
                "mean end-to-end score falls by more than 10 points",
                "think=false is returned only through the thinking field",
            ],
            "historical_entry2": {
                "functions": 6, "draws": 12, "slices": 54,
                "slice_refusals": 0, "slice_truncated": 0,
                "slice_empty": 1, "mean_compiled_score": 21.0,
                "wall_seconds": 2374,
            },
        },
        "configuration": {
            "repo": str(repo), "db": str(db), "model": args.model,
            "temperature": args.temperature, "timeout": args.timeout,
            "num_thread": args.num_thread,
        },
        "capability_gate": None,
        "functions": [],
        "slice_calls": [],
        "results": [],
        "errors": [],
        "started_at": int(time.time()),
    }
    _write(out, receipt)

    endpoint = llm.host()
    gate = capability_gate(endpoint, args.model, timeout=args.timeout,
                           num_thread=args.num_thread)
    receipt["capability_gate"] = gate
    _write(out, receipt)
    print(f"capability gate: {gate['status']}", flush=True)
    if args.probe_only or (not gate["proceed"] and not args.force_full):
        receipt["assessment"] = {
            "status": "capability_gate_stopped_full_pilot",
            "reason": gate["status"],
        }
        receipt["completed_at"] = int(time.time())
        _write(out, receipt)
        print(f"receipt: {out}", flush=True)
        return

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)

    for function_index, func in enumerate(functions):
        try:
            ws = workspace.bootstrap(repo, func)
            asm = workspace.target_asm(ws, func)
            regions = shifts.split_regions(asm)
            kb = kb_context.for_function(conn, func)
            hints = hints_for_asm(asm)
        except Exception as exc:  # keep a partial receipt actionable
            receipt["errors"].append({
                "function": func,
                "stage": "bootstrap",
                "error": f"{type(exc).__name__}: {exc}",
            })
            _write(out, receipt)
            continue

        receipt["functions"].append({
            "function": func,
            "assembly_lines": len(asm.splitlines()),
            "regions": len(regions),
            "arm_order": list(arm_order(function_index)),
        })
        _write(out, receipt)
        print(f"\n=== {func}: {len(regions)} slices ===", flush=True)

        for arm in arm_order(function_index):
            started = time.perf_counter()
            try:
                code, stats = shifts.sequential_compose(
                    endpoint, args.model, asm, regions, kb=kb, hints=hints,
                    timeout=args.timeout, num_thread=args.num_thread,
                    temperature=args.temperature, verbose=True,
                    slice_think=THINK_BY_ARM[arm], final_think="low")
            except Exception as exc:
                receipt["errors"].append({
                    "function": func, "arm": arm, "stage": "generation",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                _write(out, receipt)
                continue
            total_elapsed = time.perf_counter() - started

            for call in stats["slice_calls"]:
                receipt["slice_calls"].append(
                    {"function": func, "arm": arm} | _public(call))

            final = stats["final_call"]
            raw_response = final["_response"]
            extraction = llm.classify_extraction(raw_response, code)
            total_tokens = (sum(
                int(call["generation_tokens"])
                for call in stats["slice_calls"])
                + int(final["generation_tokens"]))
            artifact = f"{run_id}_{function_index}_{arm}"
            att = workspace.score(
                ws, repo, artifact, code, conn=conn, func=func,
                strategy=f"s1-slice-think-{arm}", model=args.model,
                prompt=final["_prompt"], temperature=args.temperature,
                wall_ms=int(total_elapsed * 1000), token_cost=total_tokens,
                run_id=run_id,
                extra={
                    "arm": arm,
                    "slice_think": THINK_BY_ARM[arm],
                    "final_think": "low",
                    "slice_count": len(stats["slice_calls"]),
                },
                raw_response=raw_response, extract_status=extraction,
                done_reason=final["done_reason"])
            row = {
                "function": func,
                "arm": arm,
                "slice_think": THINK_BY_ARM[arm],
                "final_think": "low",
                "candidate_artifact_stem": artifact,
                "candidate_sha256": _digest(code),
                "candidate_chars": len(code),
                "extraction": extraction,
                "generation_seconds_total": round(total_elapsed, 3),
                "generation_tokens_total": total_tokens,
                "slice_stats": {
                    key: value for key, value in stats.items()
                    if key not in ("slice_calls", "final_call")
                },
                "final_call": _public(final),
                "compiled": att.compiled,
                "score": att.score,
                "exact": att.exact,
                "compiler_error": att.compiler_stderr[:500],
            }
            receipt["results"].append(row)
            _write(out, receipt)
            state = "EXACT" if att.exact else (
                f"{att.score:.3f}%" if att.compiled else "no compile")
            print(f"  {arm}: {state}; {total_tokens} generated tokens; "
                  f"{total_elapsed:.1f}s", flush=True)

    if all(any(row["arm"] == arm for row in receipt["results"])
           for arm in ARMS):
        receipt["assessment"] = assess(
            receipt["slice_calls"], receipt["results"])
    else:
        receipt["assessment"] = {
            "status": "incomplete_missing_arm_results",
            "result_count": len(receipt["results"]),
            "errors": len(receipt["errors"]),
        }
    receipt["completed_at"] = int(time.time())
    _write(out, receipt)
    print("\n" + json.dumps(receipt["assessment"], indent=2), flush=True)
    print(f"receipt: {out}", flush=True)


if __name__ == "__main__":
    main()
