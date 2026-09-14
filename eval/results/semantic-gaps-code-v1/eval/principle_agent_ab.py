"""Multi-function open-book/curiosity/principle GPT-OSS experiment.

Comparison 1 changes only the stopping policy: free open-book versus curiosity.
Comparison 2 changes only retrieved guidance: curiosity versus principled,
and runs only where at least one principle mechanically activates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair
from solver import llm, modelrepair, principles, refine, residual, toolagent, workspace


def _manifest_digest(value: dict) -> str:
    unsigned = dict(value)
    unsigned.pop("manifest_digest", None)
    return hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _attempt_summary(attempt: workspace.Attempt,
                     packet: residual.ResidualPacket) -> dict:
    return {
        "attempt_id": attempt.receipt_id,
        "compiled": attempt.compiled,
        "exact": attempt.exact,
        "weighted_progress_score": attempt.score,
        "residual": packet.to_dict(),
    }


def _arm_summary(result: toolagent.Result, target_asm: str,
                 ws: Path) -> dict:
    packet = residual.build(
        result.best.attempt, target_asm=target_asm,
        target_object=ws / "target.o",
        candidate_object=result.best.object_path)
    return {
        "best": _attempt_summary(result.best.attempt, packet),
        "calls_attempted": result.calls_attempted,
        "generations": result.generations,
        "compiles": result.compiles,
        "tool_actions": result.tool_actions,
        "invalid_actions": result.invalid_actions,
        "recorded_tokens": result.tokens,
        "charged_tokens": result.charged_tokens,
        "events": result.events,
    }


def _aggregate(rows: list[dict]) -> dict:
    arms = ("free_open_book", "curiosity", "principled")
    totals: dict[str, dict] = {}
    for arm in arms:
        applicable = [row["arms"][arm] for row in rows
                      if row["arms"].get(arm) is not None]
        totals[arm] = {
            "applicable_functions": len(applicable),
            "exact": sum(bool(item["best"]["exact"]) for item in applicable),
            "improved_byte_distance": sum(
                item["best"]["residual"]["positional_byte_distance"]
                < row["root"]["residual"]["positional_byte_distance"]
                for row in rows for item in [row["arms"].get(arm)]
                if item is not None
                and item["best"]["residual"]["positional_byte_distance"]
                is not None
                and row["root"]["residual"]["positional_byte_distance"]
                is not None),
            "calls": sum(item["calls_attempted"] for item in applicable),
            "compiles": sum(item["compiles"] for item in applicable),
            "tool_actions": sum(item["tool_actions"] for item in applicable),
            "charged_tokens": sum(item["charged_tokens"] for item in applicable),
        }
    paired_curiosity = [row for row in rows
                        if row["arms"].get("free_open_book") is not None
                        and row["arms"].get("curiosity") is not None]
    paired_principles = [row for row in rows
                         if row["arms"].get("principled") is not None]
    return {
        "arms": totals,
        "comparisons": {
            "curiosity_minus_free_exact": sum(
                int(row["arms"]["curiosity"]["best"]["exact"])
                - int(row["arms"]["free_open_book"]["best"]["exact"])
                for row in paired_curiosity),
            "curiosity_vs_free_n": len(paired_curiosity),
            "principled_minus_curiosity_exact": sum(
                int(row["arms"]["principled"]["best"]["exact"])
                - int(row["arms"]["curiosity"]["best"]["exact"])
                for row in paired_principles),
            "principled_vs_curiosity_n": len(paired_principles),
        },
    }


def run(*, repo: Path, db: Path, sets: Path, manifest_path: Path,
        out: Path, model: str = "gpt-oss:20b", endpoint: str,
        provider: modelrepair.ProposalProvider | None = None,
        max_calls: int = 6, curiosity_min_calls: int = 4,
        curiosity_min_compiles: int = 2,
        curiosity_min_tools: int = 1,
        max_minutes: float = 40.0, timeout: int = 420,
        think: str = "low", num_thread: int = 12,
        temperature: float = 0.35, num_predict: int = 1200,
        seed: int = 20260903, cache_dir: Path | None = None,
        verbose: bool = False) -> dict:
    provider = provider or modelrepair.OllamaProvider()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        raise ValueError("panel manifest digest is invalid")
    panel = manifest.get("panel", [])
    if not panel:
        raise ValueError("panel is empty")
    for row in panel:
        agentrepair._refuse_frozen_heldout(sets, row["function"])

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    comparison_id = f"principle-agent-ab-{time.time_ns()}"
    config = {
        "schema_version": 1,
        "kind": "free-vs-curiosity-vs-principled-open-book",
        "manifest": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "model": model, "provider": provider.provider_id,
        "max_calls_per_arm": max_calls,
        "max_compiles_per_arm": max_calls,
        "curiosity_min_calls": curiosity_min_calls,
        "curiosity_min_compiles": curiosity_min_compiles,
        "curiosity_min_tool_actions": curiosity_min_tools,
        "max_minutes": max_minutes,
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "temperature": temperature, "num_predict": num_predict,
        "base_seed": seed,
        "terminal_success": "verifier exact=true only",
        "comparison_1_changed_variable": "minimum stopping policy",
        "comparison_2_changed_variable": "retrieved compiler principles",
    }
    receipt = {
        "schema_version": 1, "kind": config["kind"],
        "comparison_id": comparison_id, "created_at": int(time.time()),
        "config": config, "functions": [], "stopped_for_time": False,
    }
    started = time.monotonic()

    for index, panel_row in enumerate(panel):
        if index and time.monotonic() - started >= max_minutes * 60:
            receipt["stopped_for_time"] = True
            break
        name = panel_row["function"]
        attempt_id = int(panel_row["attempt_id"])
        source = agentrepair._source_for_attempt(conn, attempt_id, name)
        if hashlib.sha256(source.encode()).hexdigest() != \
                panel_row["source_sha256"]:
            raise ValueError(f"frozen source hash changed for {name}")
        ws = workspace.bootstrap(repo, name)
        target_asm = workspace.target_asm(ws, name)
        root_tag = f"{name}_principle_ab_root_{time.time_ns()}"
        root = workspace.score(
            ws, repo, root_tag, source, conn=conn, func=name, iteration=0,
            strategy="principle-ab-root-reverify", model=model,
            run_id=f"{comparison_id}-{name}-root",
            parent_attempt_id=attempt_id, relation="principle-ab-root",
            action="fresh shared root verification", run_kind=config["kind"],
            run_config=config)
        root_object = ws / f"{root_tag}.o" if root.compiled else None
        root_packet = residual.build(
            root, target_asm=target_asm, target_object=ws / "target.o",
            candidate_object=root_object)
        diagnosis = (agentrepair._diagnosis(
            repo, ws, root_object, target_asm) if root_object else "")
        matches = principles.retrieve(
            target_asm, root, root_packet, include_hypotheses=True)
        rendered = principles.render(matches)
        schedule = tuple(
            seed + index * 100 + call for call in range(1, max_calls + 1))
        row = {
            "function": name, "source_attempt_id": attempt_id,
            "root": _attempt_summary(root, root_packet),
            "principles": [match.__dict__ for match in matches],
            "principle_activated": bool(matches), "arms": {},
        }
        if root.exact:
            row["excluded"] = "fresh root became exact"
            receipt["functions"].append(row)
            continue

        def execute(arm: str) -> None:
            use_curiosity = arm != "free_open_book"
            use_principles = arm == "principled"
            result = toolagent.search(
                repo, name, source, ws, model=model, endpoint=endpoint,
                conn=conn, base_attempt=root, base_object_path=root_object,
                parent_attempt_id=root.receipt_id, diagnosis=diagnosis,
                provider=provider, max_calls=max_calls,
                max_compiles=max_calls,
                require_inspection_before_patch=False,
                timeout=timeout, think=think, num_thread=num_thread,
                temperature=temperature, num_predict=num_predict,
                call_seeds=schedule,
                run_id=f"{comparison_id}-{name}-{arm}",
                cache_dir=cache_dir,
                cache_namespace=f"principle-agent-v1-{arm}",
                verbose=verbose, open_book=True,
                workbench_root=Path.cwd().resolve(),
                min_calls_before_finish=(curiosity_min_calls
                                         if use_curiosity else 0),
                min_compiles_before_finish=(curiosity_min_compiles
                                            if use_curiosity else 0),
                min_tool_actions_before_finish=(curiosity_min_tools
                                                if use_curiosity else 0),
                principles=(rendered if use_principles else ()))
            row["arms"][arm] = _arm_summary(result, target_asm, ws)

        arms = ["free_open_book", "curiosity"]
        if matches:
            arms.append("principled")
        rotated = arms[index % len(arms):] + arms[:index % len(arms)]
        for arm in rotated:
            execute(arm)
        if "principled" not in row["arms"]:
            row["arms"]["principled"] = None
        row["wall_seconds"] = time.monotonic() - started
        receipt["functions"].append(row)
        receipt["aggregate"] = _aggregate(receipt["functions"])
        agentrepair._atomic_json(out, receipt)

    receipt["elapsed_seconds"] = time.monotonic() - started
    receipt["aggregate"] = _aggregate(receipt["functions"])
    receipt["completed_functions"] = len(receipt["functions"])
    agentrepair._atomic_json(out, receipt)
    conn.close()
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--provider", default="solver.modelrepair:OllamaProvider")
    parser.add_argument("--endpoint")
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--curiosity-min-calls", type=int, default=4)
    parser.add_argument("--curiosity-min-compiles", type=int, default=2)
    parser.add_argument("--curiosity-min-tools", type=int, default=1)
    parser.add_argument("--max-minutes", type=float, default=40.0)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--num-predict", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    provider = agentrepair._load_provider(args.provider)
    receipt = run(
        repo=args.repo.expanduser().resolve(),
        db=args.db.expanduser().resolve(),
        sets=args.sets.expanduser().resolve(),
        manifest_path=args.manifest.expanduser().resolve(),
        out=args.out.expanduser().resolve(), model=args.model,
        endpoint=args.endpoint or llm.host(), provider=provider,
        max_calls=args.max_calls,
        curiosity_min_calls=args.curiosity_min_calls,
        curiosity_min_compiles=args.curiosity_min_compiles,
        curiosity_min_tools=args.curiosity_min_tools,
        max_minutes=args.max_minutes, timeout=args.timeout,
        think=args.think, num_thread=args.num_thread,
        temperature=args.temperature, num_predict=args.num_predict,
        seed=args.seed,
        cache_dir=(args.cache_dir.expanduser().resolve()
                   if args.cache_dir else None), verbose=args.verbose)
    print(json.dumps(receipt["aggregate"], indent=2))


if __name__ == "__main__":
    main()
