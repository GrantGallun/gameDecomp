"""WF1: conditionally repair stored parents that violate exact-leaf contracts.

For each target in a frozen wavefront plan:

1. replay the best non-recovered stored parent candidate;
2. validate its compiled callsites against the frozen exact-leaf bundle;
3. spend one seeded, cached, 1,200-token draw only if validation fails; and
4. compile and revalidate the repaired candidate.

No fresh reconstruction arm exists here.  A contract-valid stored candidate
costs zero model tokens and leaves the reservation unspent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from solver import (c89, callsite_contracts, llm, pipeline, protostore,
                    refine, workspace)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def _bundle(receipt: dict, function: str) -> dict:
    row = next((row for row in receipt.get("rows", [])
                if row.get("function") == function), None)
    if not isinstance(row, dict) or not isinstance(row.get("contracts"), dict):
        raise ValueError(f"no frozen contract bundle for {function}")
    return row["contracts"]


def _stored_candidates(conn: sqlite3.Connection, function: str,
                       limit: int = 12) -> list[dict]:
    """Best generated candidates, excluding target-source recovery and WF1."""
    rows = conn.execute(
        "select a.id, a.source_code, a.score, a.strategy, a.model "
        "from attempts a join functions f on f.addr=a.func_addr "
        "where f.name=? and a.compiled=1 and a.exact=0 "
        "and length(trim(a.source_code)) > 0 "
        "order by a.score desc, a.id desc limit 100", (function,)).fetchall()
    out = []
    for attempt_id, source, score, strategy, model in rows:
        strategy = str(strategy or "")
        if strategy.startswith("wavefront-mismatch-"):
            continue
        if any(marker in strategy for marker in protostore.RECOVERY_STRATEGIES):
            continue
        out.append({
            "attempt_id": int(attempt_id),
            "source": str(source),
            "score": float(score or 0.0),
            "strategy": strategy,
            "model": str(model or ""),
        })
        if len(out) == limit:
            break
    return out


def _non_recovered_exact(conn: sqlite3.Connection, function: str) -> bool:
    rows = conn.execute(
        "select a.strategy from attempts a join functions f "
        "on f.addr=a.func_addr where f.name=? and a.exact=1", (function,))
    return any(not any(marker in str(strategy or "")
                       for marker in protostore.RECOVERY_STRATEGIES)
               for (strategy,) in rows)


def _call_neighborhoods(assembly: str, mismatches: list[dict],
                        radius: int = 4) -> str:
    lines = assembly.splitlines()
    callees = {str(row.get("callee") or "") for row in mismatches}
    selected = set()
    for index, line in enumerate(lines):
        if any(callee and re.search(rf"\b{re.escape(callee)}\b", line)
               for callee in callees):
            selected.update(range(max(0, index - radius),
                                  min(len(lines), index + radius + 1)))
    return "\n".join(lines[index] for index in sorted(selected))


def repair_prompt(function: str, source: str, score: float,
                  validation: dict, candidate_asm: str) -> str:
    """Bound the coding agent to observed callsite mismatches only."""
    mismatches = validation.get("mismatches", [])
    return f"""\
Repair one ALREADY-COMPILING matching-decompilation candidate for {function}.
Its current byte score is {score:.3f}%. Do not reconstruct it again.

The compiled candidate violates exact-leaf callsite facts. These facts compare
compiled assembly, so C variable names are irrelevant. Make only the smallest
C declaration or expression changes needed to make every OBSERVED value match
its EXPECTED value. Preserve unrelated control flow, statement ordering,
constants, declarations, and calls. If an expected argument is a signed
16-bit memory load but the observed argument is an address, pass the loaded
signed 16-bit element/value rather than the address.

MISMATCHES (binary facts, authoritative):
```json
{json.dumps(mismatches, indent=2)}
```

COMPILED CALL NEIGHBORHOODS (evidence only):
```asm
{_call_neighborhoods(candidate_asm, mismatches)}
```

CURRENT COMPILING C CANDIDATE:
```c
{source}
```

Output the complete corrected C file in one ```c block and no prose. Keep
`#include "common.h"` as the only include. Use C89 and never inline assembly.
"""


def _candidate_validation(ws: Path, artifact: str,
                          bundle: dict) -> tuple[dict | None, str]:
    path = ws / f"{artifact}_object_dump_normalized.s"
    if not path.exists():
        return None, ""
    assembly = path.read_text(encoding="utf-8", errors="replace")
    return callsite_contracts.validate_candidate_asm(bundle, assembly), assembly


def _compact_attempt(candidate: dict, att: workspace.Attempt,
                     validation: dict | None) -> dict:
    return {
        "variant": candidate["variant"],
        "artifact_stem": candidate["artifact"],
        "source_sha256": _sha(candidate["source"]),
        "source_chars": len(candidate["source"]),
        "compiled": att.compiled,
        "score": att.score,
        "exact": att.exact,
        "compiler_error": att.compiler_stderr[:500],
        "leaf_contract_validation": validation,
    }


def _best_variant(rows: list[tuple[dict, workspace.Attempt, dict | None]]) \
        -> tuple[dict, workspace.Attempt, dict | None]:
    return max(rows, key=lambda row: (
        bool(row[1].exact),
        bool(row[2] and row[2].get("passed")),
        bool(row[1].compiled),
        float(row[1].score),
    ))


def _targets(plan: dict, include_budget_deferred: bool) -> list[dict]:
    """Return the reserved frontier plus optional zero-token scan candidates."""
    rows = [dict(row) for row in plan["plan"]["frontier"]]
    if include_budget_deferred:
        seen = {str(row["function"]) for row in rows}
        for deferred in plan["plan"].get("budget_deferred", []):
            function = str(deferred["function"])
            if function in seen:
                continue
            rows.append({
                "function": function,
                "reserved_generation_tokens": 0,
                "scan_source": "eligible_budget_deferred",
                "deferred_reason": deferred.get("reason"),
            })
            seen.add(function)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--contracts", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--think", default="low")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=2026083100)
    parser.add_argument("--scan-only", action="store_true",
                        help="replay and validate stored candidates; never repair")
    parser.add_argument("--include-budget-deferred", action="store_true",
                        help="also scan eligible deferred targets at zero tokens")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    plan_path = args.plan.expanduser().resolve()
    contracts_path = args.contracts.expanduser().resolve()
    out = args.out.expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    if args.include_budget_deferred and not args.scan_only:
        raise ValueError("budget-deferred targets may only be added in scan-only mode")
    frontier = _targets(plan, args.include_budget_deferred)
    cap = int(plan["plan"]["budget"]["repair_token_cap_per_target"])
    if cap > 1200:
        raise ValueError("WF1 refuses a repair cap over 1,200 tokens")

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    endpoint = llm.host()
    run_id = f"wf1-{int(time.time())}"
    receipt = {
        "schema_version": 1,
        "kind": "wavefront_mismatch_only_repair",
        "run_id": run_id,
        "inputs": {
            "plan": {"path": str(plan_path),
                     "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()},
            "contracts": {"path": str(contracts_path),
                          "sha256": hashlib.sha256(
                              contracts_path.read_bytes()).hexdigest()},
        },
        "policy": {
            "repair_draws_per_activated_parent": 1,
            "generation_token_cap": cap,
            "seed_base": args.seed_base,
            "cache_dir": str(args.cache_dir),
            "promotion": "target compiler plus byte-exact oracle only",
            "scan_only": args.scan_only,
            "include_budget_deferred": args.include_budget_deferred,
        },
        "results": [],
        "started_at": int(time.time()),
    }
    _write(out, receipt)

    for index, frontier_row in enumerate(frontier):
        function = str(frontier_row["function"])
        row = {
            "function": function,
            "reserved_generation_tokens": int(
                frontier_row["reserved_generation_tokens"]),
            "charged_generation_tokens": 0,
        }
        receipt["results"].append(row)
        _write(out, receipt)
        print(f"\n=== {function} ===", flush=True)

        if _non_recovered_exact(conn, function):
            row["status"] = "already_exact_before_wf1_no_repair"
            _write(out, receipt)
            print("  already exact; reservation unspent", flush=True)
            continue

        bundle = _bundle(contracts, function)
        ws = workspace.bootstrap(repo, function)
        stored = None
        replay_att = None
        validation = None
        candidate_asm = ""
        replay_failures = []
        for candidate_index, candidate in enumerate(
                _stored_candidates(conn, function)):
            artifact = f"{run_id}_{index}_stored_{candidate_index}"
            att = workspace.score(
                ws, repo, artifact, candidate["source"], conn=conn,
                func=function, strategy="wavefront-mismatch-stored-replay",
                model=candidate["model"], prompt="", temperature=None,
                wall_ms=0, token_cost=0, run_id=run_id,
                extra={"origin_attempt_id": candidate["attempt_id"]})
            if not att.compiled:
                replay_failures.append({
                    "attempt_id": candidate["attempt_id"],
                    "historical_score": candidate["score"],
                    "compiler_error": att.compiler_stderr[:300],
                })
                continue
            validation, candidate_asm = _candidate_validation(
                ws, artifact, bundle)
            stored, replay_att = candidate, att
            row["stored_candidate"] = {
                "origin_attempt_id": candidate["attempt_id"],
                "origin_strategy": candidate["strategy"],
                "source_sha256": _sha(candidate["source"]),
                "historical_score": candidate["score"],
                "replay_artifact_stem": artifact,
                "replay_score": att.score,
                "replay_exact": att.exact,
                "leaf_contract_validation": validation,
                "higher_ranked_replay_failures": replay_failures,
            }
            break

        if stored is None or replay_att is None or validation is None:
            row["status"] = "no_replayable_stored_candidate"
            row["replay_failures"] = replay_failures
            _write(out, receipt)
            print("  no replayable stored candidate", flush=True)
            continue

        mismatch_count = len(validation.get("mismatches", []))
        print(f"  stored {replay_att.score:.3f}%; "
              f"contract mismatches={mismatch_count}", flush=True)
        if validation.get("passed"):
            row["status"] = "stored_candidate_contract_valid_no_repair_spent"
            _write(out, receipt)
            continue
        if args.scan_only:
            row["status"] = "stored_candidate_mismatch_scan_only"
            _write(out, receipt)
            continue

        prompt = repair_prompt(function, stored["source"], replay_att.score,
                               validation, candidate_asm)
        seed = args.seed_base + index
        started = time.perf_counter()
        text, meta = llm.generate(
            endpoint, args.model, prompt, timeout=args.timeout,
            num_thread=args.num_thread, num_predict=cap, think=args.think,
            temperature=args.temperature, prefill=pipeline.PREFILL,
            seed=seed, cache_dir=args.cache_dir,
            cache_namespace="wf1-mismatch-only-v1")
        elapsed = time.perf_counter() - started
        cache_hit = bool(meta.get("_cache_hit"))
        recorded_tokens = int(meta.get("eval_count", 0) or 0)
        charged_tokens = 0 if cache_hit else recorded_tokens
        row["charged_generation_tokens"] = charged_tokens
        row["repair_generation"] = {
            "seed": seed,
            "cache_hit": cache_hit,
            "cache_key": meta.get("_cache_key"),
            "prompt_chars": len(prompt),
            "prompt_sha256": _sha(prompt),
            "response_chars": len(text),
            "response_sha256": _sha(text),
            "generation_seconds": round(elapsed, 3),
            "recorded_generation_tokens": recorded_tokens,
            "charged_generation_tokens": charged_tokens,
            "done_reason": meta.get("done_reason", ""),
        }

        raw_code = llm.extract_c(text)
        variants = [{
            "variant": "raw", "source": raw_code,
            "artifact": f"{run_id}_{index}_repair_raw",
        }]
        repaired_c89 = c89.to_c89(raw_code)
        if repaired_c89 != raw_code:
            variants.append({
                "variant": "c89", "source": repaired_c89,
                "artifact": f"{run_id}_{index}_repair_c89",
            })

        scored = []
        compact = []
        extraction = llm.classify_extraction(text, raw_code)
        for variant_index, variant in enumerate(variants):
            att = workspace.score(
                ws, repo, variant["artifact"], variant["source"], conn=conn,
                func=function,
                strategy=f"wavefront-mismatch-repair-{variant['variant']}",
                model=args.model, prompt=prompt,
                temperature=args.temperature, wall_ms=int(elapsed * 1000),
                token_cost=charged_tokens if variant_index == 0 else 0,
                run_id=run_id,
                extra={
                    "seed": seed, "cache_hit": cache_hit,
                    "cache_key": meta.get("_cache_key"),
                    "origin_attempt_id": stored["attempt_id"],
                    "deterministic_variant": variant["variant"],
                },
                raw_response=text if variant_index == 0 else "",
                extract_status=extraction,
                done_reason=meta.get("done_reason", ""))
            final_validation, _asm = _candidate_validation(
                ws, variant["artifact"], bundle) if att.compiled else (None, "")
            scored.append((variant, att, final_validation))
            compact.append(_compact_attempt(variant, att, final_validation))
        row["repair_candidates"] = compact
        winner, winner_att, winner_validation = _best_variant(scored)
        row["selected_variant"] = winner["variant"]
        row["score_delta"] = round(
            winner_att.score - replay_att.score, 6)
        row["mismatches_before"] = mismatch_count
        row["mismatches_after"] = (len(winner_validation.get("mismatches", []))
                                    if winner_validation else None)

        if winner_att.exact:
            row["status"] = "exact_promoted_by_oracle"
        elif winner_validation and winner_validation.get("passed"):
            row["status"] = "contract_repaired_not_exact_park"
        elif winner_att.compiled:
            row["status"] = "repair_compiled_contract_still_violated_park"
        else:
            row["status"] = "repair_did_not_compile_park"
        _write(out, receipt)
        print(f"  {row['status']}; selected score={winner_att.score:.3f}%; "
              f"charged tokens={charged_tokens}", flush=True)

    receipt["summary"] = {
        "parents": len(receipt["results"]),
        "activated_repairs": sum(
            "repair_generation" in row for row in receipt["results"]),
        "exact_promotions": sum(
            row.get("status") == "exact_promoted_by_oracle"
            for row in receipt["results"]),
        "contract_repairs": sum(
            row.get("status") == "contract_repaired_not_exact_park"
            for row in receipt["results"]),
        "charged_generation_tokens": sum(
            int(row["charged_generation_tokens"])
            for row in receipt["results"]),
        "reserved_generation_tokens": sum(
            int(row["reserved_generation_tokens"])
            for row in receipt["results"]),
    }
    receipt["completed_at"] = int(time.time())
    _write(out, receipt)
    conn.close()
    print("\n" + json.dumps(receipt["summary"], indent=2), flush=True)
    print(f"receipt: {out}", flush=True)


if __name__ == "__main__":
    main()
