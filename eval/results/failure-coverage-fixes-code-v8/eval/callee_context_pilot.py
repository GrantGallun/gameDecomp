"""Three-arm pilot for knowledge flowing from exact callees to a parent.

Arms, the same bounded number of draws and sampling settings each:

``baseline``
    The current parent prompt.
``facts``
    Baseline plus exact direct-callee prototype and machine effects.  The full
    callee source is opt-in because the first pilot made a larger parent emit
    much longer, non-compiling reconstructions.
``semantics``
    Facts plus a separately generated, explicitly fallible annotation of each
    callee's purpose, parameter roles, and return role.

This is intentionally not wired into ``solver.pipeline``.  Its purpose is to
test the causal leg of leaf-first scheduling before richer context changes the
production solver.  Every code candidate goes through the ordinary byte-exact
oracle and is logged to the attempts ledger.

Example:

    python3 -m eval.callee_context_pilot \
      --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \
      --function renderRaceUiSingleTrailEffect
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from solver import (callee_context, c89, llm, pipeline, refine, workspace)


def _packet_receipt(packet: dict) -> dict:
    """Persist provenance and the annotation without duplicating source C."""
    return {
        key: value for key, value in packet.items()
        if key != "exact_source"
    } | {
        "exact_source_sha256": hashlib.sha256(
            packet["exact_source"].encode()).hexdigest(),
        "exact_source_chars": len(packet["exact_source"]),
    }


def _infer_annotations(packets: list[dict], endpoint: str, model: str,
                       timeout: int, think: str,
                       num_thread: int) -> list[dict]:
    receipts = []
    for packet in packets:
        prompt = callee_context.annotation_prompt(packet)
        started = time.perf_counter()
        text, meta = llm.generate(
            endpoint, model, prompt, timeout=timeout, think=think,
            num_thread=num_thread, num_predict=700, temperature=0.0)
        elapsed = time.perf_counter() - started
        row = {
            "callee": packet["name"],
            "prompt_chars": len(prompt),
            "response": text,
            "generation_seconds": round(elapsed, 3),
            "generation_tokens": meta.get("eval_count", 0),
            "done_reason": meta.get("done_reason", ""),
        }
        try:
            annotation = callee_context.parse_annotation(text)
        except ValueError as exc:
            row["error"] = str(exc)
        else:
            useful, reason = callee_context.annotation_is_useful(annotation)
            if useful:
                packet["semantic_annotation"] = annotation
                row["annotation"] = annotation
            else:
                row["rejected_annotation"] = annotation
                row["error"] = reason
        receipts.append(row)
    return receipts


def _score_arm(repo: Path, conn, ws: Path, func: str, endpoint: str,
               model: str, prompt: str, arm: str, timeout: int, think: str,
               num_thread: int, run_id: str, draw: int,
               temperature: float, *,
               strategy_prefix: str = "callee-context-pilot") -> dict:
    started = time.perf_counter()
    text, meta = llm.generate(
        endpoint, model, prompt, timeout=timeout, think=think,
        num_thread=num_thread, num_predict=6000, temperature=temperature,
        prefill=pipeline.PREFILL)
    elapsed = time.perf_counter() - started
    code = llm.extract_c(text)
    extraction = llm.classify_extraction(text, code)

    common_log = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "wall_ms": int(elapsed * 1000),
        "token_cost": int(meta.get("eval_count", 0) or 0),
        "run_id": run_id,
        "extra": {
            "arm": arm,
            "draw": draw,
            "eval_count": meta.get("eval_count", 0),
            "prompt_eval_count": meta.get("prompt_eval_count", 0),
        },
        "raw_response": text,
        "extract_status": extraction,
        "done_reason": meta.get("done_reason", ""),
    }
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-")[-72:]
    artifact_stem = f"{safe_run_id}_{arm}_{draw}"
    raw = workspace.score(
        ws, repo, artifact_stem, code, conn=conn, func=func,
        strategy=f"{strategy_prefix}-{arm}", **common_log)

    repaired_code = c89.to_c89(code)
    repaired = raw
    if repaired_code != code:
        repaired = workspace.score(
            ws, repo, f"{artifact_stem}_c89", repaired_code,
            conn=conn, func=func,
            strategy=f"{strategy_prefix}-{arm}-c89", **common_log)

    best = repaired if repaired.score > raw.score or repaired.exact else raw
    winning_stem = (f"{artifact_stem}_c89" if repaired is best and
                    repaired_code != code else artifact_stem)
    return {
        "arm": arm,
        "draw": draw,
        "prompt_chars": len(prompt),
        "response_chars": len(text),
        "extraction": extraction,
        "generation_seconds": round(elapsed, 3),
        "generation_tokens": meta.get("eval_count", 0),
        "tokens_per_second": round(llm.tokens_per_second(meta), 2),
        "done_reason": meta.get("done_reason", ""),
        "candidate_artifact_stem": winning_stem,
        "raw": {
            "compiled": raw.compiled,
            "score": raw.score,
            "exact": raw.exact,
            "compiler_error": raw.compiler_stderr[:500],
        },
        "c89_repair_changed_source": repaired_code != code,
        "compiled": best.compiled,
        "score": best.score,
        "exact": best.exact,
        "compiler_error": best.compiler_stderr[:500],
    }


def _assessment(results: list[dict]) -> dict:
    by_arm = {}
    for row in results:
        by_arm.setdefault(row["arm"], []).append(row)

    def stats(rows: list[dict]) -> dict:
        return {
            "draws": len(rows),
            "compiled": sum(bool(row["compiled"]) for row in rows),
            "exact": sum(bool(row["exact"]) for row in rows),
            "best_score": max((row["score"] for row in rows), default=0.0),
            "mean_end_to_end_score": round(
                sum(row["score"] for row in rows) / len(rows), 6)
                if rows else 0.0,
        }

    arm_stats = {arm: stats(rows) for arm, rows in by_arm.items()}
    baseline = arm_stats["baseline"]
    out = {
        "scope": "small paired pilot; not a population-level conclusion",
        "arms": arm_stats,
    }
    for arm in ("facts", "semantics"):
        row = arm_stats.get(arm)
        if row is None:
            continue
        out[f"{arm}_best_delta"] = round(
            row["best_score"] - baseline["best_score"], 6)
        out[f"{arm}_mean_delta"] = round(
            row["mean_end_to_end_score"]
            - baseline["mean_end_to_end_score"], 6)
        out[f"{arm}_exact_gain"] = bool(row["exact"] and not baseline["exact"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--function", required=True)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--samples", type=int, default=1,
                        help="paired code generations per arm")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-callees", type=int, default=3)
    parser.add_argument("--include-recovered", action="store_true",
                        help="ceiling-only: admit target-source-recovered callees")
    parser.add_argument("--include-callee-source", action="store_true",
                        help="also show exact callee C to the parent; compact "
                             "facts/annotations are the default")
    parser.add_argument("--skip-semantics", action="store_true",
                        help="run only baseline and deterministic callee facts")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)

    packets = callee_context.packets_for_parent(
        conn, args.function, include_recovered=args.include_recovered,
        max_callees=args.max_callees)
    if not packets:
        raise SystemExit(
            f"{args.function} has no eligible exact direct callees; "
            "nothing to test")

    recovered = [p["name"] for p in packets
                 if p["recovered_from_target_source"]]
    if recovered:
        print("*** CEILING CONTEXT: recovered target source admitted for "
              + ", ".join(recovered), flush=True)

    endpoint = llm.host()
    # Exclude model loading from the first measured arm.
    llm.generate(endpoint, args.model, "Return the JSON object {}.",
                 timeout=args.timeout, think="false",
                 num_thread=args.num_thread, num_predict=32,
                 temperature=0.0)

    annotation_receipts = []
    if not args.skip_semantics:
        print(f"annotating {len(packets)} exact direct callee(s)", flush=True)
        annotation_receipts = _infer_annotations(
            packets, endpoint, args.model, args.timeout, args.think,
            args.num_thread)

    ws = workspace.bootstrap(repo, args.function)
    asm = workspace.target_asm(ws, args.function)
    draft = workspace.m2c_draft(ws)
    baseline = pipeline.build_prompt(
        repo, conn, args.function, asm, draft, "reshape", False)
    facts = baseline + callee_context.render_packets(
        packets, include_source=args.include_callee_source,
        include_semantics=False)
    prompts = [("baseline", baseline), ("facts", facts)]
    if not args.skip_semantics and any(
            packet.get("semantic_annotation") for packet in packets):
        semantics = baseline + callee_context.render_packets(
            packets, include_source=args.include_callee_source,
            include_semantics=True)
        prompts.append(("semantics", semantics))

    for _arm, prompt in prompts:
        workspace.assert_uncontaminated(prompt, repo, args.function)

    run_id = f"callee-context-pilot-{int(time.time())}-{args.function}"
    results = []
    for draw in range(1, args.samples + 1):
        for arm, prompt in prompts:
            print(f"{arm} draw {draw}: generating from {len(prompt)} prompt chars",
                  flush=True)
            result = _score_arm(
                repo, conn, ws, args.function, endpoint, args.model, prompt, arm,
                args.timeout, args.think, args.num_thread, run_id, draw,
                args.temperature)
            results.append(result)
            verdict = ("EXACT" if result["exact"]
                       else f"{result['score']:.3f}%" if result["compiled"]
                       else "did not compile")
            print(f"{arm} draw {draw}: {verdict}", flush=True)

    prior = conn.execute(
        "select max(a.score) from attempts a join functions f "
        "on f.addr = a.func_addr where f.name = ? and a.compiled = 1 "
        "and a.strategy not like 'callee-context-pilot-%'",
        (args.function,)).fetchone()
    receipt = {
        "schema_version": 1,
        "kind": "callee_context_three_arm_pilot",
        "function": args.function,
        "model": args.model,
        "temperature": args.temperature,
        "samples_per_arm": args.samples,
        "include_recovered": args.include_recovered,
        "include_callee_source": args.include_callee_source,
        "prior_best_score": float((prior or [0.0])[0] or 0.0),
        "callees": [_packet_receipt(packet) for packet in packets],
        "annotation_generations": annotation_receipts,
        "results": results,
        "assessment": _assessment(results),
        "created_at": int(time.time()),
    }
    out = args.out or (
        Path(__file__).parent / "results" /
        f"callee_context_{args.function}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"receipt: {out}")


if __name__ == "__main__":
    main()
