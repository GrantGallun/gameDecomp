"""Frozen-root GPT-OSS A/B: proposal-only versus bounded tool-using repair.

Both arms receive the same freshly verified DEV source, target/residual,
deterministic diagnosis, provider, numeric seed schedule, model-call cap,
compile cap, and output-token cap.  The changed variable is whether the model
may spend calls on allowlisted investigation tools before submitting patches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair
from solver import llm, modelrepair, refine, residual, toolagent, workspace


def _attempt_summary(candidate_attempt: workspace.Attempt,
                     packet: residual.ResidualPacket) -> dict:
    return {
        "attempt_id": candidate_attempt.receipt_id,
        "compiled": candidate_attempt.compiled,
        "exact": candidate_attempt.exact,
        "weighted_progress_score": candidate_attempt.score,
        "residual": packet.to_dict(),
    }


def run(*, repo: Path, db: Path, function: str, attempt_id: int,
        out: Path, model: str = "gpt-oss:20b", endpoint: str,
        provider: modelrepair.ProposalProvider | None = None,
        max_calls: int = 4, timeout: int = 420, think: str = "low",
        num_thread: int = 12, temperature: float = 0.35,
        num_predict: int = 1200, seed: int = 20260902,
        cache_dir: Path | None = None, tool_first: bool = False,
        verbose: bool = False, open_book: bool = False,
        workbench_root: Path | None = None) -> dict:
    if max_calls <= 0:
        raise ValueError("max_calls must be positive")
    provider = provider or modelrepair.OllamaProvider()
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    source = agentrepair._source_for_attempt(conn, attempt_id, function)
    ws = workspace.bootstrap(repo, function)
    target_asm = workspace.target_asm(ws, function)
    schedule = tuple(seed + index for index in range(1, max_calls + 1))
    comparison_id = f"toolagent-ab-{time.time_ns()}-{function}"
    config = {
        "schema_version": 1,
        "kind": ("proposal-only-vs-open-book-agent" if open_book
                 else "proposal-only-vs-tool-agent"),
        "function": function,
        "source_attempt_id": attempt_id,
        "model": model,
        "provider": provider.provider_id,
        "max_model_calls_per_arm": max_calls,
        "max_compiles_per_arm": max_calls,
        "call_seeds": list(schedule),
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "temperature": temperature, "num_predict": num_predict,
        "tool_first": tool_first,
        "open_book": open_book,
        "terminal_success": "verifier exact=true only",
    }
    config_digest = hashlib.sha256(json.dumps(
        config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    cache_namespace = f"toolagent-ab-v1-{config_digest}"

    root_tag = f"{function}_toolagent_ab_root_{time.time_ns()}"
    root = workspace.score(
        ws, repo, root_tag, source, conn=conn, func=function, iteration=0,
        strategy="toolagent-ab-root-reverify", model=model,
        run_id=f"{comparison_id}-root", parent_attempt_id=attempt_id,
        relation="toolagent-ab-root", action="fresh shared root verification",
        run_kind="toolagent-ab", run_config=config)
    root_object = ws / f"{root_tag}.o" if root.compiled else None
    root_packet = residual.build(
        root, target_asm=target_asm, target_object=ws / "target.o",
        candidate_object=root_object)
    diagnosis = (agentrepair._diagnosis(repo, ws, root_object, target_asm)
                 if root_object else "")

    arms: dict[str, dict] = {}

    def proposal_only() -> None:
        result = modelrepair.search(
            repo, function, source, ws, model=model, endpoint=endpoint,
            conn=conn, base_attempt=root, base_object_path=root_object,
            parent_attempt_id=root.receipt_id, diagnosis=diagnosis,
            draws=1, max_depth=max_calls, beam_width=1,
            max_calls=max_calls, exhaust_budget=True,
            timeout=timeout, think=think, num_thread=num_thread,
            temperature=temperature, num_predict=num_predict, seed=None,
            call_seeds=schedule, run_id=f"{comparison_id}-proposal",
            cache_dir=cache_dir,
            cache_namespace=f"{cache_namespace}-proposal",
            provider=provider, verbose=verbose)
        packet = residual.build(
            result.best_attempt, target_asm=target_asm,
            target_object=ws / "target.o",
            candidate_object=result.best_object_path)
        best_path = out.with_name(f"{out.stem}.proposal.best.c")
        best_path.parent.mkdir(parents=True, exist_ok=True)
        best_path.write_text(result.best_source, encoding="utf-8")
        arms["proposal_only"] = {
            "best": _attempt_summary(result.best_attempt, packet),
            "best_source_path": str(best_path),
            "calls_attempted": result.calls_attempted,
            "generations": result.generations,
            "compiles": result.compiling_children,
            "recorded_tokens": result.tokens,
            "charged_tokens": result.charged_tokens,
            "log": result.log,
        }

    def tool_using() -> None:
        result = toolagent.search(
            repo, function, source, ws, model=model, endpoint=endpoint,
            conn=conn, base_attempt=root, base_object_path=root_object,
            parent_attempt_id=root.receipt_id, diagnosis=diagnosis,
            provider=provider, max_calls=max_calls, max_compiles=max_calls,
            timeout=timeout, think=think, num_thread=num_thread,
            temperature=temperature, num_predict=num_predict, seed=None,
            call_seeds=schedule, run_id=f"{comparison_id}-tool",
            cache_dir=cache_dir, cache_namespace=f"{cache_namespace}-tool",
            verbose=verbose, open_book=open_book,
            require_inspection_before_patch=not open_book,
            workbench_root=workbench_root)
        packet = residual.build(
            result.best.attempt, target_asm=target_asm,
            target_object=ws / "target.o",
            candidate_object=result.best.object_path)
        best_path = out.with_name(f"{out.stem}.tool.best.c")
        best_path.parent.mkdir(parents=True, exist_ok=True)
        best_path.write_text(result.best.source, encoding="utf-8")
        arms["tool_using"] = {
            "best": _attempt_summary(result.best.attempt, packet),
            "best_source_path": str(best_path),
            "calls_attempted": result.calls_attempted,
            "generations": result.generations,
            "compiles": result.compiles,
            "tool_actions": result.tool_actions,
            "invalid_actions": result.invalid_actions,
            "recorded_tokens": result.tokens,
            "charged_tokens": result.charged_tokens,
            "events": result.events,
        }

    order = ((tool_using, proposal_only) if tool_first
             else (proposal_only, tool_using))
    for arm in order:
        arm()

    proposal = arms["proposal_only"]
    tool = arms["tool_using"]
    aggregate = {
        "proposal_exact": bool(proposal["best"]["exact"]),
        "tool_exact": bool(tool["best"]["exact"]),
        "exact_delta": (int(bool(tool["best"]["exact"]))
                        - int(bool(proposal["best"]["exact"]))),
        "proposal_score_delta": (
            proposal["best"]["weighted_progress_score"] - root.score),
        "tool_score_delta": (
            tool["best"]["weighted_progress_score"] - root.score),
        "proposal_charged_tokens": proposal["charged_tokens"],
        "tool_charged_tokens": tool["charged_tokens"],
        "interpretation": (
            "single-parent smoke validates mechanics and activation only; "
            "it cannot establish repair efficacy"),
    }
    receipt = {
        "schema_version": 1,
        "kind": config["kind"],
        "comparison_id": comparison_id,
        "created_at": int(time.time()),
        "config": config,
        "config_digest": config_digest,
        "root": _attempt_summary(root, root_packet),
        "arms": arms,
        "aggregate": aggregate,
    }
    agentrepair._atomic_json(out, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--attempt-id", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--provider",
                        default="solver.modelrepair:OllamaProvider")
    parser.add_argument("--endpoint")
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--num-predict", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--tool-first", action="store_true")
    parser.add_argument("--open-book", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    out = args.out.expanduser().resolve()
    agentrepair._refuse_frozen_heldout(
        args.sets.expanduser().resolve(), args.function)
    provider = agentrepair._load_provider(args.provider)
    receipt = run(
        repo=repo, db=db, function=args.function,
        attempt_id=args.attempt_id, out=out, model=args.model,
        endpoint=args.endpoint or llm.host(), provider=provider,
        max_calls=args.max_calls, timeout=args.timeout, think=args.think,
        num_thread=args.num_thread, temperature=args.temperature,
        num_predict=args.num_predict, seed=args.seed,
        cache_dir=(args.cache_dir.expanduser().resolve()
                   if args.cache_dir else None),
        tool_first=args.tool_first, verbose=args.verbose,
        open_book=args.open_book, workbench_root=Path.cwd().resolve())
    print(json.dumps(receipt["aggregate"], indent=2))


if __name__ == "__main__":
    main()
