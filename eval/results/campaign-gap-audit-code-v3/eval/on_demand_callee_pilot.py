"""Test tool-like, on-demand inspection of completed direct callees.

The previous factorial appended every eligible callee packet to the parent
prompt.  This pilot tests a materially different interface:

1. show a planning pass a compact catalog and caller-local evidence;
2. let it request zero or more named callees;
3. reveal full exact packets only for valid requests; and
4. compare equal code-generation budgets for ``flywheel``, ``catalog``, and
   ``on_demand`` arms.

All exact bodies come from one frozen shaped-library digest.  A selector may
abstain, and an invalid response safely reveals nothing.  Only the compiler
and byte oracle can promote a generated parent.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import callee_context_pilot, wavefront_flywheel_pilot
from solver import callee_context, llm, pipeline, refine, shaped_flywheel, workspace


ARMS = ("flywheel", "catalog", "on_demand")


def _catalog(packets: list[dict]) -> str:
    """Render discoverable identities and hypotheses, never implementation C."""
    if not packets:
        return ""
    lines = [
        "\nCOMPLETED DIRECT-CALLEE CATALOG:",
        "These byte-exact functions can be inspected if their implementation",
        "would constrain the parent's value or control flow. Names and semantic",
        "aliases remain hypotheses; stable addresses and prototypes are stronger.",
    ]
    for packet in packets:
        line = (f"- {packet['address']} {packet['name']}; "
                f"parent call sites={packet['parent_call_sites']}; "
                f"prototype={packet['prototype'] or 'unknown'}")
        lines.append(line)
        annotation = packet.get("semantic_annotation")
        if annotation:
            confidence = annotation.get("confidence")
            suffix = (f" (confidence {confidence:.2f})"
                      if confidence is not None else "")
            lines.append(
                f"  semantic hypothesis{suffix}: {annotation['summary']}")
            if annotation.get("return_role"):
                lines.append(
                    f"  proposed return role: {annotation['return_role']}")
        lines.append("  exact implementation available through inspection")
    return "\n".join(lines) + "\n"


def _call_neighborhood(asm: str, callee: str, radius: int = 4) -> str:
    lines = (asm or "").splitlines()
    hits = [index for index, line in enumerate(lines)
            if re.search(rf"\b{re.escape(callee)}\b", line)]
    if not hits:
        return "no symbolic call-site line found"
    selected = set()
    for hit in hits[:3]:
        selected.update(range(max(0, hit - radius), min(len(lines), hit + radius + 1)))
    return "\n".join(lines[index] for index in sorted(selected))


def _inspection_prompt(parent: str, asm: str, draft: str,
                       packets: list[dict]) -> str:
    """Ask for a bounded tool request without revealing exact callee bodies."""
    evidence = []
    for packet in packets:
        evidence.extend([
            f"\n### {packet['name']} ({packet['address']})",
            f"prototype: {packet['prototype'] or 'unknown'}",
            "binary-observed effects: "
            + ("; ".join(packet["memory_effects"]) or "none recorded"),
            "callee call neighborhood in parent assembly:",
            "```asm",
            _call_neighborhood(asm, packet["name"]),
            "```",
        ])
        annotation = packet.get("semantic_annotation")
        if annotation:
            evidence.append(
                "fallible semantic hypothesis: " + annotation["summary"])

    names = ", ".join(packet["name"] for packet in packets)
    return f"""\
You are the planning step for a matching-decompilation coding agent.

Decide whether inspecting the exact implementation of a completed DIRECT
callee would reveal a caller-relevant constraint not already supplied by its
prototype and binary effects. Examples include returned-value construction,
callback dispatch, mutation ordering, or a non-obvious ABI-compatible type.
Do not request a body merely because it is available. The coding pass will
still receive the parent assembly and M2C draft.

Parent: {parent}
Available exact callees: {names}

Return JSON only:
{{
  "inspect": ["calleeName"],
  "reason": "one short caller-specific reason, or why none is needed",
  "expected_constraints": ["short constraint to look for"],
  "confidence": 0.0
}}

Only names in the available list are valid. Request at most one callee.

CALLER-LOCAL EVIDENCE:
{''.join(evidence)}

M2C DRAFT EXCERPT (implementation may be wrong):
```c
{draft[:5000]}
```
"""


def _parse_selection(text: str, available: set[str], *,
                     max_requests: int = 1) -> dict[str, object]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    if start < 0:
        raise ValueError("selector response contains no JSON object")
    try:
        value, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid selector JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("selector response must be an object")
    requested = value.get("inspect", [])
    if not isinstance(requested, list) or not all(
            isinstance(name, str) for name in requested):
        raise ValueError("inspect must be an array of names")
    requested = list(dict.fromkeys(requested))
    unknown = sorted(set(requested) - available)
    if unknown:
        raise ValueError("selector requested unavailable callee(s): "
                         + ", ".join(unknown))
    if len(requested) > max_requests:
        raise ValueError(f"selector requested more than {max_requests} callee")
    confidence = value.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("selector confidence must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("selector confidence must be between 0 and 1")
    constraints = value.get("expected_constraints", [])
    if not isinstance(constraints, list):
        raise ValueError("expected_constraints must be an array")
    return {
        "inspect": requested,
        "reason": " ".join(str(value.get("reason") or "").split())[:400],
        "expected_constraints": [
            " ".join(str(item).split())[:180]
            for item in constraints[:5] if str(item).strip()
        ],
        "confidence": confidence,
    }


def _select(endpoint: str, model: str, prompt: str, packets: list[dict], *,
            timeout: int, think: str, num_thread: int) -> dict[str, object]:
    """Run the selector once; malformed output fails closed to no inspection."""
    started = time.perf_counter()
    text, meta = llm.generate(
        endpoint, model, prompt, timeout=timeout, think=think,
        num_thread=num_thread, num_predict=800, temperature=0.0)
    receipt: dict[str, object] = {
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response": text,
        "generation_seconds": round(time.perf_counter() - started, 3),
        "generation_tokens": meta.get("eval_count", 0),
        "done_reason": meta.get("done_reason", ""),
    }
    try:
        selection = _parse_selection(
            text, {packet["name"] for packet in packets})
    except ValueError as exc:
        receipt["valid"] = False
        receipt["error"] = str(exc)
        receipt["selection"] = {
            "inspect": [],
            "reason": "invalid selector output; failed closed",
            "expected_constraints": [],
            "confidence": 0.0,
        }
    else:
        receipt["valid"] = True
        receipt["selection"] = selection
    return receipt


def _arm_order(function_index: int, draw: int) -> tuple[str, ...]:
    """Latin rotation: over three draws every arm occupies every position."""
    shift = (function_index + draw - 1) % len(ARMS)
    return ARMS[shift:] + ARMS[:shift]


def _stats(rows: list[dict]) -> dict[str, object]:
    by_function: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_function[str(row["function"])].append(row)
    best = {name: max(float(row["score"]) for row in function_rows)
            for name, function_rows in by_function.items()}
    exact_functions = sorted(
        name for name, function_rows in by_function.items()
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


def _assessment(results: list[dict], inspected: bool) -> dict[str, object]:
    stats = {arm: _stats([row for row in results if row["arm"] == arm])
             for arm in ARMS}
    flywheel_exact = set(stats["flywheel"]["exact_functions"])
    on_demand_exact = set(stats["on_demand"]["exact_functions"])
    if on_demand_exact - flywheel_exact:
        status = "on_demand_exact_gain_observed_needs_replication"
    elif flywheel_exact - on_demand_exact:
        status = "on_demand_exact_regression_observed_needs_replication"
    elif not inspected:
        status = "selector_abstained_full_body_not_tested"
    else:
        status = "inconclusive_no_exact_difference"
    return {
        "status": status,
        "scope": "bounded DEV pilot; score-only changes are not causal proof",
        "primary_outcome": "oracle-exact functions",
        "selector_revealed_full_body": inspected,
        "arms": stats,
        "catalog_vs_flywheel_mean_best_delta": round(
            float(stats["catalog"]["mean_best_score"])
            - float(stats["flywheel"]["mean_best_score"]), 6),
        "on_demand_vs_flywheel_mean_best_delta": round(
            float(stats["on_demand"]["mean_best_score"])
            - float(stats["flywheel"]["mean_best_score"]), 6),
        "on_demand_vs_catalog_mean_best_delta": round(
            float(stats["on_demand"]["mean_best_score"])
            - float(stats["catalog"]["mean_best_score"]), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--shaped-library", required=True, type=Path)
    parser.add_argument("--function", required=True)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-callees", type=int, default=2)
    parser.add_argument("--max-source-chars", type=int, default=2400)
    parser.add_argument("--skip-semantics", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    library_path = args.shaped_library.expanduser().resolve()
    library = shaped_flywheel.load_library(library_path)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    packets = wavefront_flywheel_pilot._freeze_packets(
        conn, args.function, library, max_callees=args.max_callees,
        max_source_chars=args.max_source_chars)
    if not packets:
        raise SystemExit("no non-recovered frozen exact direct callees")

    endpoint = llm.host()
    llm.generate(endpoint, args.model, "Return the JSON object {}.",
                 timeout=args.timeout, think="false",
                 num_thread=args.num_thread, num_predict=32,
                 temperature=0.0)

    annotations = []
    if not args.skip_semantics:
        annotations = callee_context_pilot._infer_annotations(
            packets, endpoint, args.model, args.timeout, args.think,
            args.num_thread)

    ws = workspace.bootstrap(repo, args.function)
    asm = workspace.target_asm(ws, args.function)
    draft = workspace.m2c_draft(ws)
    base = pipeline.build_prompt(
        repo, conn, args.function, asm, draft, "reshape", False)
    matches = shaped_flywheel.rank(repo, conn, args.function, library, top=2)
    if not matches:
        raise SystemExit("no shaped-flywheel candidate at the production threshold")
    flywheel_block = shaped_flywheel.render_context(matches)
    flywheel_prompt = wavefront_flywheel_pilot._inject(base, flywheel_block)
    catalog_block = _catalog(packets)
    catalog_prompt = wavefront_flywheel_pilot._inject(
        flywheel_prompt, catalog_block)

    selector_prompt = _inspection_prompt(args.function, asm, draft, packets)
    workspace.assert_uncontaminated(selector_prompt, repo, args.function)
    selection_receipt = _select(
        endpoint, args.model, selector_prompt, packets, timeout=args.timeout,
        think=args.think, num_thread=args.num_thread)
    selected_names = set(selection_receipt["selection"]["inspect"])
    selected = [packet for packet in packets if packet["name"] in selected_names]
    inspected_block = callee_context.render_packets(
        selected, include_source=True, include_semantics=False)
    on_demand_prompt = wavefront_flywheel_pilot._inject(
        catalog_prompt, inspected_block)
    prompts = {
        "flywheel": flywheel_prompt,
        "catalog": catalog_prompt,
        "on_demand": on_demand_prompt,
    }
    for prompt in prompts.values():
        workspace.assert_uncontaminated(prompt, repo, args.function)

    prior = conn.execute(
        "select max(a.score) from attempts a join functions f "
        "on f.addr=a.func_addr where f.name=? and a.compiled=1 "
        "and a.strategy not like 'callee-context-pilot-%' "
        "and a.strategy not like 'wavefront-flywheel-pilot-%' "
        "and a.strategy not like 'on-demand-callee-pilot-%'",
        (args.function,)).fetchone()
    run_id = f"on-demand-callee-pilot-{int(time.time())}-{args.function}"
    results = []
    for draw in range(1, args.samples + 1):
        order = _arm_order(0, draw)
        print(f"draw {draw} order: {', '.join(order)}", flush=True)
        for arm in order:
            result = callee_context_pilot._score_arm(
                repo, conn, ws, args.function, endpoint, args.model,
                prompts[arm], arm, args.timeout, args.think, args.num_thread,
                run_id, draw, args.temperature,
                strategy_prefix="on-demand-callee-pilot")
            result["function"] = args.function
            result["generation_position"] = order.index(arm) + 1
            results.append(result)
            verdict = ("EXACT" if result["exact"]
                       else f"{result['score']:.3f}%" if result["compiled"]
                       else "did not compile")
            print(f"  {arm}: {verdict}", flush=True)

    receipt = {
        "schema_version": 1,
        "kind": "on_demand_direct_callee_pilot",
        "function": args.function,
        "model": args.model,
        "temperature": args.temperature,
        "samples_per_arm": args.samples,
        "prior_best_score": float((prior or [0.0])[0] or 0.0),
        "shaped_library": {
            "path": str(library_path),
            "digest": library["digest"],
        },
        "callees": [callee_context_pilot._packet_receipt(packet)
                    for packet in packets],
        "annotation_generations": annotations,
        "flywheel_matches": [
            wavefront_flywheel_pilot._match_receipt(match)
            for match in matches],
        "selector": selection_receipt,
        "selected_callees": sorted(selected_names),
        "prompt_chars": {arm: len(prompt) for arm, prompt in prompts.items()},
        "prompt_sha256": {
            arm: hashlib.sha256(prompt.encode()).hexdigest()
            for arm, prompt in prompts.items()
        },
        "results": results,
        "assessment": _assessment(results, bool(selected)),
        "created_at": int(time.time()),
    }
    out = args.out or (Path(__file__).parent / "results" /
                       f"on_demand_callee_{args.function}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    conn.close()
    print(f"selector requested: {', '.join(selected_names) or 'nothing'}")
    print(f"receipt: {out}")
    print(f"status: {receipt['assessment']['status']}")


if __name__ == "__main__":
    main()
