"""Factorial pilot for the shaped flywheel and bottom-up callee context.

The four arms use the same parent, model, sampling budget, frozen shaped
library, and byte-exact oracle:

``control``
    Current parent prompt only.
``flywheel``
    Current prompt plus the shaped assembly-similar-function context.
``wavefront``
    Current prompt plus exact direct-callee packets.
``combined``
    Both context sources.

This is intentionally separate from ``eval.run_set`` and ``solver.pipeline``.
It tests whether completed callees add value *given* the new flywheel without
silently changing the production scheduler.  The library digest, prompt
hashes, exact-callee hashes, annotations, generation order, and oracle results
are written to one receipt.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import callee_context_pilot
from solver import (callee_context, llm, pipeline, protostore, refine,
                    shaped_flywheel, workspace)


ARMS = ("control", "flywheel", "wavefront", "combined")
FINAL_INSTRUCTION = "\nProduce the corrected C file now.\n"


def _bounded(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, max(0, limit - 80))
    if cut < 0:
        cut = max(0, limit - 80)
    return text[:cut].rstrip() + "\n/* exact callee source truncated */"


def _freeze_packets(conn: sqlite3.Connection, parent: str,
                    library: dict[str, object], *, max_callees: int,
                    max_source_chars: int = 2400,
                    include_recovered: bool = False) -> list[dict]:
    """Bind direct-callee facts to exact source from one frozen library."""
    nodes = library.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("shaped library has no node map")

    # Ask for every plausible direct callee before applying frozen membership;
    # otherwise a live exact callee outside the snapshot can consume the limit.
    live = callee_context.packets_for_parent(
        conn, parent, include_recovered=include_recovered,
        max_callees=max(128, max_callees),
        max_source_chars=max_source_chars)
    frozen = []
    for packet in live:
        node = nodes.get(packet["name"])
        if not isinstance(node, dict):
            continue
        trust = node.get("trust")
        source = node.get("exact_source")
        if not isinstance(trust, dict) or trust.get("exact") is not True \
                or not isinstance(source, str):
            raise ValueError(
                f"invalid frozen exact node for {packet['name']}")

        packet = dict(packet)
        packet["exact_attempt_id"] = int(trust["attempt_id"])
        packet["exact_strategy"] = str(trust.get("strategy") or "unknown")
        packet["exact_source"] = _bounded(source, max_source_chars)
        signature = protostore.parse_definition(source, packet["name"])
        packet["prototype"] = signature["prototype"] if signature else ""
        packet["compatible_return_expressions"] = \
            callee_context._return_expressions(source)  # noqa: SLF001
        packet["semantic_annotation"] = None
        frozen.append(packet)
        if len(frozen) == max_callees:
            break
    return frozen


def _inject(prompt: str, block: str) -> str:
    """Insert experimental context beside other facts, before the final ask."""
    if not block:
        return prompt
    if FINAL_INSTRUCTION not in prompt:
        return prompt.rstrip() + "\n" + block + "\n"
    return prompt.replace(
        FINAL_INSTRUCTION, "\n" + block.strip() + FINAL_INSTRUCTION, 1)


def _arm_order(function_index: int, draw: int) -> tuple[str, ...]:
    """Counterbalance early/late model-server drift across two draws."""
    if (function_index + draw) % 2:
        return tuple(reversed(ARMS))
    return ARMS


def _match_receipt(match: dict[str, object]) -> dict[str, object]:
    """Record ranking evidence without duplicating a verified source body."""
    history = match.get("outcomes") or []
    return {
        "candidate": match["candidate"],
        "assembly_similarity": match["assembly_similarity"],
        "assembly_band": match["assembly_band"],
        "shaped_score": match["shaped_score"],
        "components": match["components"],
        "shared_calls": match["shared_calls"],
        "shared_memory_shapes": match["shared_memory_shapes"],
        "prior_outcomes": [{
            "arm_pair": item.get("arm_pair"),
            "score_delta": item.get("score_delta"),
            "exact_delta": item.get("exact_delta"),
        } for item in history],
    }


def _arm_stats(rows: list[dict]) -> dict[str, object]:
    by_function: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_function[str(row["function"])].append(row)
    best = {
        function: max(float(row["score"]) for row in function_rows)
        for function, function_rows in by_function.items()
    }
    exact_functions = sorted(
        function for function, function_rows in by_function.items()
        if any(bool(row["exact"]) for row in function_rows))
    return {
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


def _assessment(results: list[dict]) -> dict[str, object]:
    grouped = {
        arm: [row for row in results if row["arm"] == arm]
        for arm in ARMS
    }
    stats = {arm: _arm_stats(rows) for arm, rows in grouped.items()}

    def delta(left: str, right: str, metric: str) -> float:
        return round(float(stats[left][metric]) - float(stats[right][metric]), 6)

    flywheel_exact = set(stats["flywheel"]["exact_functions"])
    combined_exact = set(stats["combined"]["exact_functions"])
    if combined_exact - flywheel_exact:
        status = "combined_exact_gain_observed_needs_replication"
    elif flywheel_exact - combined_exact:
        status = "combined_exact_regression_observed_needs_replication"
    else:
        status = "inconclusive_no_exact_difference"

    interaction_best = round(
        float(stats["combined"]["mean_best_score"])
        - float(stats["flywheel"]["mean_best_score"])
        - float(stats["wavefront"]["mean_best_score"])
        + float(stats["control"]["mean_best_score"]), 6)
    return {
        "status": status,
        "scope": "bounded DEV factorial; score-only changes are not causal proof",
        "primary_outcome": "oracle-exact functions",
        "arms": stats,
        "flywheel_vs_control_mean_best_delta": delta(
            "flywheel", "control", "mean_best_score"),
        "wavefront_vs_control_mean_best_delta": delta(
            "wavefront", "control", "mean_best_score"),
        "combined_vs_flywheel_mean_best_delta": delta(
            "combined", "flywheel", "mean_best_score"),
        "combined_vs_best_single_context_delta": round(
            float(stats["combined"]["mean_best_score"])
            - max(float(stats["flywheel"]["mean_best_score"]),
                  float(stats["wavefront"]["mean_best_score"])), 6),
        "additive_interaction_mean_best": interaction_best,
        "interpretation": (
            "A positive interaction is only a secondary score signal. Promote "
            "the combined method only after a replicated oracle-exact gain."),
    }


def _functions(args: argparse.Namespace) -> list[str]:
    names = list(args.function or [])
    if args.set:
        payload = json.loads(args.set.read_text(encoding="utf-8"))
        names.extend(str(row["function"]) for row in payload[args.split])
    return list(dict.fromkeys(names))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--shaped-library", required=True, type=Path)
    parser.add_argument("--function", action="append",
                        help="target function; repeat for a small cohort")
    parser.add_argument("--set", type=Path,
                        help="scan or run every function in this frozen set")
    parser.add_argument("--split", default="dev", choices=("dev", "heldout"))
    parser.add_argument("--scan-only", action="store_true",
                        help="list frozen exact direct-callee coverage; no model calls")
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--samples", type=int, default=2,
                        help="code generations per arm and function")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-callees", type=int, default=2)
    parser.add_argument("--max-source-chars", type=int, default=2400)
    parser.add_argument("--include-recovered", action="store_true",
                        help="ceiling-only: admit target-source-recovered callees")
    parser.add_argument("--include-callee-source", action="store_true",
                        help="show the frozen exact direct-callee body")
    parser.add_argument("--skip-semantics", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    names = _functions(args)
    if not names:
        parser.error("provide at least one --function or --set")

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    library_path = args.shaped_library.expanduser().resolve()
    library = shaped_flywheel.load_library(library_path)
    if args.split == "heldout" and library.get("outcomes"):
        parser.error("heldout runs refuse shaped libraries carrying DEV outcomes")

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    packets_by_function = {
        name: _freeze_packets(
            conn, name, library, max_callees=args.max_callees,
            max_source_chars=args.max_source_chars,
            include_recovered=args.include_recovered)
        for name in names
    }

    if args.scan_only:
        print(f"shaped library {library['digest']}")
        for name in names:
            callees = packets_by_function[name]
            print(f"{name}: " + (", ".join(
                f"{packet['name']}@{packet['address']}" for packet in callees)
                if callees else "no eligible frozen exact direct callees"))
        conn.close()
        return

    missing = [name for name, packets in packets_by_function.items() if not packets]
    if missing:
        parser.error("no eligible frozen exact direct callees for: "
                     + ", ".join(missing))

    endpoint = llm.host()
    # Keep model load time out of the first arm.
    llm.generate(endpoint, args.model, "Return the JSON object {}.",
                 timeout=args.timeout, think="false",
                 num_thread=args.num_thread, num_predict=32,
                 temperature=0.0)

    annotation_receipts = []
    if not args.skip_semantics:
        for name in names:
            packets = packets_by_function[name]
            print(f"annotating {len(packets)} frozen direct callee(s) for {name}",
                  flush=True)
            rows = callee_context_pilot._infer_annotations(
                packets, endpoint, args.model, args.timeout, args.think,
                args.num_thread)
            annotation_receipts.extend(
                {"parent": name, **row} for row in rows)

    run_id = f"wavefront-flywheel-pilot-{int(time.time())}"
    function_receipts = []
    results = []
    for function_index, name in enumerate(names):
        packets = packets_by_function[name]
        ws = workspace.bootstrap(repo, name)
        asm = workspace.target_asm(ws, name)
        draft = workspace.m2c_draft(ws)
        base = pipeline.build_prompt(
            repo, conn, name, asm, draft, "reshape", False)
        matches = shaped_flywheel.rank(
            repo, conn, name, library, top=2)
        if not matches:
            raise SystemExit(
                f"{name} has direct-callee coverage but no shaped-flywheel "
                "candidate; it cannot identify the combined effect")
        flywheel_block = shaped_flywheel.render_context(matches)
        wavefront_block = callee_context.render_packets(
            packets, include_source=args.include_callee_source,
            include_semantics=not args.skip_semantics)
        prompts = {
            "control": base,
            "flywheel": _inject(base, flywheel_block),
            "wavefront": _inject(base, wavefront_block),
            "combined": _inject(base, flywheel_block + "\n" + wavefront_block),
        }
        for prompt in prompts.values():
            workspace.assert_uncontaminated(prompt, repo, name)

        prior = conn.execute(
            "select max(a.score) from attempts a join functions f "
            "on f.addr=a.func_addr where f.name=? and a.compiled=1 "
            "and a.strategy not like 'callee-context-pilot-%' "
            "and a.strategy not like 'wavefront-flywheel-pilot-%'",
            (name,)).fetchone()
        function_receipts.append({
            "function": name,
            "prior_best_score": float((prior or [0.0])[0] or 0.0),
            "callees": [callee_context_pilot._packet_receipt(packet)
                        for packet in packets],
            "flywheel_matches": [_match_receipt(match) for match in matches],
            "context_overlap": sorted(
                {packet["name"] for packet in packets}
                & {str(match["candidate"]) for match in matches}),
            "prompt_chars": {arm: len(prompt) for arm, prompt in prompts.items()},
            "prompt_sha256": {
                arm: hashlib.sha256(prompt.encode()).hexdigest()
                for arm, prompt in prompts.items()
            },
        })

        for draw in range(1, args.samples + 1):
            order = _arm_order(function_index, draw)
            print(f"{name} draw {draw} order: {', '.join(order)}", flush=True)
            for arm in order:
                result = callee_context_pilot._score_arm(
                    repo, conn, ws, name, endpoint, args.model, prompts[arm],
                    arm, args.timeout, args.think, args.num_thread, run_id,
                    draw, args.temperature,
                    strategy_prefix="wavefront-flywheel-pilot")
                result["function"] = name
                result["generation_position"] = order.index(arm) + 1
                results.append(result)
                verdict = ("EXACT" if result["exact"]
                           else f"{result['score']:.3f}%" if result["compiled"]
                           else "did not compile")
                print(f"  {arm}: {verdict}", flush=True)

    receipt = {
        "schema_version": 1,
        "kind": "wavefront_shaped_flywheel_factorial_pilot",
        "split": args.split,
        "functions": function_receipts,
        "model": args.model,
        "temperature": args.temperature,
        "samples_per_arm": args.samples,
        "arm_order_policy": "reverse-balanced by function index and draw",
        "shaped_library": {
            "path": str(library_path),
            "digest": library["digest"],
            "nodes": len(library["nodes"]),
            "outcomes": len(library.get("outcomes", [])),
        },
        "wavefront_policy": {
            "direct_calls_only": True,
            "frozen_library_members_only": True,
            "include_recovered": args.include_recovered,
            "include_callee_source": args.include_callee_source,
            "semantic_annotations": not args.skip_semantics,
            "max_callees": args.max_callees,
            "max_source_chars": args.max_source_chars,
        },
        "annotation_generations": annotation_receipts,
        "results": results,
        "assessment": _assessment(results),
        "created_at": int(time.time()),
    }
    out = args.out or (Path(__file__).parent / "results" /
                       f"wavefront_flywheel_{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    conn.close()
    print(f"receipt: {out}")
    print(f"status: {receipt['assessment']['status']}")


if __name__ == "__main__":
    main()
