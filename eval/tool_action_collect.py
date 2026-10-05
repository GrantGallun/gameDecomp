"""Collect outcome-backed decision records from REAL episodes, with the real compiler (WSL).

WHY THIS IS A SEPARATE ENTRY POINT. `eval/tool_action_dataset.py` is pure Python and runs anywhere, so
its procedural exercises can be regenerated and re-graded on a laptop. This module needs m2c, the
workspace and the object oracle, so it runs under the WSL training venv against the sbk1 tree. Keeping
them apart is what lets the cheap half of the dataset be reproducible without a compiler.

WHAT IS ACTUALLY BEING COLLECTED. Not a demonstration of a successful script -- the script mostly
does not succeed, which is the point. Each episode contributes SNAPSHOTS (the state at a decision) and,
for a bounded number of them, BRANCHES: alternative applicable actions executed from independent
copies of that state under the same budget ceilings. What the alternatives did is evidence, and the
record's acceptable set comes from that evidence where it exists and from the mechanical rules of the
state where it does not. A branch that could not be executed is recorded as not executed, never
inferred from a later state (spec §4).

COST IS CAPPED BEFORE THE WORK, NOT AFTER. Compiles are the expensive unit here -- one regalloc search
can spend dozens -- so the caller passes a wall-clock deadline and per-episode ceilings, and the run
stops launching new work past the deadline and says so in the receipt.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _branch_candidates(snapshot: dict, *, limit: int, avoid: set[str]) -> list[str]:
    """The actions worth executing from one state, in the declared preference order.

    `avoid` holds the actions that already returned nothing against this exact source and the action
    the episode actually took next. Re-running a no-effect call would spend a compile to re-learn a
    known answer; running the action the episode already took would produce a record whose "evidence"
    is just the transcript it came from, which is precisely the non-counterfactual labelling the spec
    rejects.
    """
    from eval.tool_action_dataset import PREFERENCE_ORDER, prerequisites_met
    from eval.tool_registry import ACTIONS, TERMINAL

    chosen = []
    for name in PREFERENCE_ORDER:
        if len(chosen) >= limit:
            break
        if name in avoid or name not in ACTIONS or ACTIONS[name].kind == TERMINAL:
            continue
        if not prerequisites_met(snapshot["context_keys"], name):
            continue
        chosen.append(name)
    return chosen


def _inner_policy(args):
    """Who visits the states being collected.

    `scripted` builds the first dataset from a fixed order. `adapter` is the correction round: the
    LEARNED policy goes where it goes, and the states it gets wrong are the ones worth training on.
    The two produce the same record shape, so the correction states are graded and weighted exactly
    like every other record instead of being trusted because they came from the model.
    """
    from eval.tool_agent import ScriptedPolicy

    if getattr(args, "policy", "scripted") == "scripted":
        return ScriptedPolicy()
    if not getattr(args, "adapter", None):
        raise SystemExit("--policy adapter needs --adapter")
    from eval.tool_action_eval import load_adapter_policy
    return load_adapter_policy(args.base, args.adapter, show_tool_detail=True)


def collect(args) -> dict:
    from eval import resource_limits
    from eval.tool_agent import ScriptedPolicy, run_episode
    from eval.tool_agent_probe import tried_since_change
    from eval.tool_agent_run import build_context
    from eval.tool_action_dataset import (SnapshotPolicy, branch_outcomes, correction_records,
                                          label_for, outcome_records, write_jsonl)
    from eval.tool_registry import ACTIONS

    # LOAD FIRST, CAP AFTER, AND THE ORDER IS THE REASON THIS RUN WORKS. `resource_limits.apply()`
    # sets a per-process torch memory fraction, and the largest allocation this process ever makes is
    # the 4-bit checkpoint load plus its allocator warmup (~14.3 GiB peak). Applying the cap before it
    # made a load fail twice that the card could hold -- 130 MiB short at 0.75, then 2.66 GiB short at
    # 0.90 -- because the fraction is measured against the whole device while other jobs already hold
    # ~3 GiB of it. The caps govern every episode below and are recorded in the receipt; the model is
    # built once, here, rather than per function.
    inner = _inner_policy(args)
    limits = resource_limits.apply()
    splits = json.loads(Path(args.splits).read_text("utf-8")) if args.splits else {}
    names = list(splits.get(args.split) or [])
    if args.functions:
        names = names[:args.functions]
    if not names:
        raise SystemExit(f"no {args.split} functions in {args.splits}; run --mode splits first")

    runners = {action.runner: action.resolve() for action in ACTIONS.values() if action.runner}
    corrections: list[dict] = []

    conn = sqlite3.connect(str(args.kb)) if args.kb else None
    started, deadline = time.time(), time.time() + args.seconds
    specs, transcripts, skipped = [], [], []
    for name in names:
        if time.time() > deadline:
            skipped.append({"function": name, "reason": "wall-clock deadline reached before start"})
            continue
        try:
            context, why = build_context(args.repo, name, conn=conn)
        except Exception as exc:                                # noqa: BLE001
            skipped.append({"function": name, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if context is None:
            skipped.append({"function": name, "reason": why})
            continue
        policy = SnapshotPolicy(inner, args.budget, args.snapshots)
        episode_started = time.time()
        transcript = run_episode(context, policy, budget=args.budget, runners=runners)
        took = [step.action for step in transcript.steps]
        for index, snapshot in enumerate(policy.snapshots):
            snapshot = {**snapshot, "function": name,
                        "source_path": context.source_path, "target_asm_path": context.target_asm_path}
            if index < len(took) - 1:
                next_action = took[index]
            else:
                next_action = ""
            avoid = set(tried_since_change(snapshot["steps"]))
            if next_action:
                avoid.add(next_action)
            branch_names = _branch_candidates(snapshot, limit=args.branches, avoid=avoid)
            outcomes = []
            if branch_names and time.time() < deadline:
                outcomes = branch_outcomes(snapshot, branch_names, context.compile_fn, runners,
                                           max_seconds=args.branch_seconds)
            specs.append({"function": name, "snapshot": snapshot, "outcomes": outcomes,
                          "episode_action": next_action})
        # THE CORRECTION PASS: the same snapshots, but keeping only the states where the visiting
        # policy's own choice was NOT acceptable, with the mechanical corrective label. Verified the
        # same way as every other record -- a corrective action that was not executed is not recorded
        # as though it had been.
        for index, (snapshot, chosen) in enumerate(zip(policy.snapshots, policy.choices)):
            enriched = {**snapshot, "function": name,
                        "source_path": context.source_path,
                        "target_asm_path": context.target_asm_path}
            label = label_for(enriched["candidate"], enriched["steps"],
                              enriched["budget_remaining"], enriched["context_keys"])
            if chosen["action"] in [item["action"] for item in label["acceptable"]]:
                continue
            outcomes = []
            corrective = [item["action"] for item in label["acceptable"]]
            if corrective and corrective[0] != "stop" and time.time() < deadline:
                outcomes = branch_outcomes(enriched, corrective[:2], context.compile_fn, runners,
                                           max_seconds=args.branch_seconds)
            corrections.append({"function": name, "snapshot": enriched, "chosen": chosen,
                                "outcomes": outcomes})
        transcripts.append({
            "function": name, "exact": transcript.exact, "stop_reason": transcript.stop_reason,
            # The step -1 the controller records is state, not a decision, and the receipt must not
            # read as though the policy chose it. `decisions` is the action list that was actually
            # chosen, in order.
            "initial_state": [{"action": s.action, "status": s.status,
                               "certificate_status": s.detail.get("certificate_status"),
                               "diff_chars": len(s.detail.get("diff") or ""),
                               "stderr_chars": len(s.detail.get("stderr") or "")}
                              for s in transcript.steps if s.index < 0],
            "decisions": [{"action": s.action, "params": s.params, "status": s.status,
                           "changed": s.changed, "exact": s.exact}
                          for s in transcript.steps if s.index >= 0],
            "snapshots": len(policy.snapshots), "candidates_seen": transcript.candidates_seen,
            "seconds": round(time.time() - episode_started, 2)})
        print(json.dumps({**transcripts[-1], "decisions": [a["action"] for a in
                                                          transcripts[-1]["decisions"]]}), flush=True)

    rows = outcome_records(specs, split=args.split) if specs else []
    summary = write_jsonl(args.out, rows) if rows else {"path": str(args.out), "records": 0,
                                                        "counts": {}}
    correction_summary = {"path": str(args.corrections_out), "records": 0, "counts": {}}
    if corrections:
        correction_summary = write_jsonl(args.corrections_out,
                                         correction_records(corrections, split=args.split))
    receipt = {"limits": limits.as_dict(), "functions_attempted": names,
               "functions_collected": [t["function"] for t in transcripts],
               "skipped": skipped, "transcripts": transcripts, "dataset": summary,
               "corrections": correction_summary,
               "policy": getattr(args, "policy", "scripted"),
               "budget": args.budget, "snapshots_per_function": args.snapshots,
               "branches_per_snapshot": args.branches, "seconds": round(time.time() - started, 1),
               "deadline_seconds": args.seconds,
               "correction_states": len(corrections),
               "branch_outcomes": [{"function": s["function"], "outcomes": s["outcomes"],
                                    "episode_action": s["episode_action"]} for s in specs]}
    Path(args.receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset": summary, "receipt": str(args.receipt),
                      "collected": len(transcripts), "skipped": len(skipped)}, indent=2))
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--splits", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--split", default="train")
    ap.add_argument("--functions", type=int, default=6)
    ap.add_argument("--budget", type=int, default=4)
    ap.add_argument("--snapshots", type=int, default=2)
    ap.add_argument("--branches", type=int, default=2)
    ap.add_argument("--branch-seconds", type=float, default=90.0)
    ap.add_argument("--seconds", type=float, default=2400.0)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/outcome-train.jsonl")
    ap.add_argument("--receipt", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/collection-receipt.json")
    # THE CORRECTION ROUND (spec §5). `--policy adapter` collects the states the LEARNED policy
    # actually visits, on TRAIN-split functions only, and writes the ones it got wrong as correction
    # records. Held-out states are never collected here: `--split dev` would be a leak by construction.
    ap.add_argument("--policy", choices=("scripted", "adapter"), default="scripted")
    ap.add_argument("--adapter", type=Path, default=None)
    ap.add_argument("--base", type=Path, default=Path.home() / "decomp/models/qwen2.5-coder-7b")
    ap.add_argument("--corrections-out", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/corrections-train.jsonl")
    args = ap.parse_args(argv)
    collect(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
