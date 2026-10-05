"""Does the intervention change anything on functions it was not derived from?

THE COMPARISON. Two arms, one panel. Both arms get the same functions, the same action budget, the same
deterministic policy and a FRESH COPY of the same starting context, so the only difference between them
is the intervention under test. A context is built once per function and shallow-copied per arm: the
loop mutates `context.diff` and `context.candidate` as it runs, so arms that shared one object would be
measuring the order they ran in.

PRIMARY AND SECONDARY ENDPOINTS ARE KEPT APART (spec §6). The primary benefit is additional CERTIFIED
matches. Everything else -- candidates that compile at all, procedural decisions graded by the same
mechanical rules as the action-policy work, action counts, compiles -- is secondary and is reported as a
procedure result, never as decompilation capability. A run that improves a secondary measure and adds no
match is reported as exactly that.

THE PANEL IS SEALED BY CONSTRUCTION, not by discipline: this module is given a list of function names and
refuses any that appears in the motivating set or in a held-out set it is told about. It never reads the
researcher's demand queue, so it cannot accidentally select the functions that motivated the finding.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1


@dataclass
class ArmResult:
    arm: str
    functions: list = field(default_factory=list)
    certified: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> dict:
        decisions = [d for row in self.rows for d in row.get("decisions", [])]
        total = len(decisions)
        return {
            "arm": self.arm, "functions": len(self.functions),
            "certified_matches": len(self.certified),
            "certified_functions": sorted(self.certified),
            "compile_successes": sum(1 for row in self.rows if row.get("compiled_any")),
            "actions": total,
            "acceptable_rate": round(sum(1 for d in decisions if d.get("is_acceptable")) / max(1, total), 4),
            "candidates_seen": sum(row.get("candidates_seen", 0) for row in self.rows),
            "errors": sum(1 for row in self.rows if row.get("error")),
            "seconds": round(self.seconds, 1),
        }


def eligible_panel(names: list[str], *, motivating: set[str], held_out: set[str],
                   purpose: str = "research") -> tuple[list, dict]:
    """Which of `names` this caller may use, and which exclusions were applied to get there.

    TWO EXCLUSIONS WITH TWO DIFFERENT OWNERS, and conflating them broke the evaluator.

    * The MOTIVATING exclusion protects the measurement: a function the intervention was derived from
      cannot be the function it is tested on, in either role.
    * The HELD-OUT exclusion protects the RESEARCH: held-out functions must not be shown to the
      researcher, because an intervention derived from them is contaminated.

    `purpose="research"` applies both. `purpose="evaluation"` applies the motivating exclusion ONLY:
    the isolated evaluator exists to execute the frozen split, and an evaluator that refuses to run
    the very tasks it was sealed to measure reports an empty result that looks like a strict policy.
    Measured: `eligible_panel(["t1","t2"], motivating=set(), held_out={"t1","t2"})` kept 0 of 2.
    """
    if purpose not in ("research", "evaluation"):
        raise ValueError(f"unknown purpose {purpose!r}; expected 'research' or 'evaluation'")
    excluded_held_out = set(names) & held_out if purpose == "research" else set()
    kept = [name for name in names
            if name not in motivating and name not in excluded_held_out]
    return kept, {"requested": len(names), "kept": len(kept), "purpose": purpose,
                  "excluded_motivating": sorted(set(names) & motivating),
                  "excluded_held_out": len(excluded_held_out),
                  "held_out_exclusion_applied": purpose == "research",
                  "note": ("the held-out exclusion keeps held-out functions away from RESEARCH; the "
                           "isolated evaluator is entitled to execute the frozen split, so it is "
                           "applied for `research` and not for `evaluation`")}


def run_arm(panel: list[str], *, arm: str, policy_factory, budget: int, repo: Path, kb: Path,
            contexts: dict, limits_seconds: float) -> ArmResult:
    """One arm over the panel, using contexts built before either arm ran.

    THE POLICY IS BUILT ONCE PER ARM, not once per function. The first version called the factory
    inside the loop, which for a model arm would have reloaded a 7B checkpoint for every function --
    minutes of GPU time per function and a silent difference between arms if the load failed for one of
    them. The grading wrapper is per function so its decisions stay attributable.
    """
    from eval.tool_agent import run_episode
    from eval.tool_action_eval import GradingPolicy
    from eval.tool_registry import ACTIONS

    runners = {action.runner: action.resolve() for action in ACTIONS.values() if action.runner}
    started = time.time()
    result = ArmResult(arm=arm)
    policy = policy_factory()
    conn = sqlite3.connect(str(kb)) if kb else None
    try:
        for name in panel:
            context = contexts.get(name)
            if context is None:
                result.rows.append({"function": name, "error": "no context"})
                continue
            if time.time() - started > limits_seconds:
                result.rows.append({"function": name, "error": "arm wall-clock cap reached"})
                continue
            grading = GradingPolicy(policy, function=name, dataset=f"transfer:{arm}")
            ctx = copy.copy(context)          # isolate: the loop mutates candidate and diff
            before = time.time()
            retrieval_before = len(getattr(policy, "history", []) or [])
            try:
                transcript = run_episode(ctx, grading, budget=budget, runners=runners)
                compiled_any = any((step.detail or {}).get("compiled") for step in transcript.steps)
                result.rows.append({
                    "function": name, "exact": transcript.exact,
                    "stop_reason": transcript.stop_reason, "compiled_any": compiled_any,
                    "candidates_seen": transcript.candidates_seen,
                    "decisions": [dict(d) for d in grading.decisions],
                    "actions": [step.action for step in transcript.steps if step.index >= 0],
                    # WHAT THE RETRIEVAL WRAPPER ACTUALLY RETURNED during this function. Without it a
                    # receipt cannot distinguish "the intervention was enabled and did nothing" from
                    # "the intervention was never active" -- the two arms then look identical and the
                    # null is reported as evidence.
                    "retrieval": list(getattr(policy, "history", []) or [])[retrieval_before:],
                    "seconds": round(time.time() - before, 2)})
                if transcript.exact:
                    result.certified.append(name)
            except Exception as exc:                            # noqa: BLE001
                result.rows.append({"function": name, "error": f"{type(exc).__name__}: {exc}"})
            result.functions.append(name)
    finally:
        if conn is not None:
            conn.close()
    result.seconds = time.time() - started
    return result


def paired_transfer(*, panel: list[str], budget: int, repo: Path, kb: Path,
                    baseline_factory, intervention_factory, limits_seconds: float = 900.0,
                    ledger=None, stage: str = "transfer") -> dict:
    """Build every context ONCE, then run both arms over the frozen copies."""
    from eval.tool_agent_run import build_context

    if ledger is not None:
        # One compile per panel function for setup, plus at most `budget` actions each; reserve before
        # the work so an over-budget panel is refused rather than half-run.
        ledger.reserve(f"{stage}-setup", compiles=len(panel))
        ledger.reserve(stage, compiles=2 * len(panel) * max(1, budget))

    contexts, skipped = {}, []
    conn = sqlite3.connect(str(kb)) if kb else None
    try:
        # THE CONNECTION MUST OUTLIVE BOTH ARMS. `build_context` closes over it inside `compile_fn`
        # (every score logs an attempt), so closing it here -- which the first version did, before the
        # arms ran -- turns every later compile into "Cannot operate on a closed database". Five of six
        # functions failed that way IN BOTH ARMS, which made the paired comparison look like a tie
        # between two arms that had each run one function. A silent harness failure that produces a
        # symmetric result is the most dangerous shape a bug can take here.
        for name in panel:
            try:
                context, why = build_context(repo, name, conn=conn)
            except Exception as exc:                            # noqa: BLE001
                skipped.append({"function": name, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            if context is None:
                skipped.append({"function": name, "reason": why})
                continue
            contexts[name] = context
        if ledger is not None:
            ledger.spend(f"{stage}-setup", compiles=len(contexts))

        usable = [name for name in panel if name in contexts]
        baseline = run_arm(usable, arm="baseline", policy_factory=baseline_factory, budget=budget,
                           repo=repo, kb=kb, contexts=contexts, limits_seconds=limits_seconds)
        intervention = run_arm(usable, arm="intervention", policy_factory=intervention_factory,
                               budget=budget, repo=repo, kb=kb, contexts=contexts,
                               limits_seconds=limits_seconds)
    finally:
        if conn is not None:
            conn.close()
    if ledger is not None:
        ledger.spend(stage, compiles=baseline.summary()["candidates_seen"]
                     + intervention.summary()["candidates_seen"])
    payload = {
        "schema_version": SCHEMA_VERSION, "budget_actions": budget, "panel": panel,
        "skipped": skipped, "usable": usable,
        "baseline": baseline.summary(), "intervention": intervention.summary(),
        "delta": {
            "certified_matches": intervention.summary()["certified_matches"]
                                 - baseline.summary()["certified_matches"],
            "compile_successes": intervention.summary()["compile_successes"]
                                 - baseline.summary()["compile_successes"],
            "acceptable_rate": round(intervention.summary()["acceptable_rate"]
                                     - baseline.summary()["acceptable_rate"], 4),
            "candidates_seen": intervention.summary()["candidates_seen"]
                               - baseline.summary()["candidates_seen"],
        },
        "rows": {"baseline": baseline.rows, "intervention": intervention.rows},
        "note": ("primary endpoint is certified_matches; every other number is a procedure measure and "
                 "is not a decompilation capability claim"),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--panel", type=Path, required=True, help="JSON list of function names")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    from eval.tool_agent import ScriptedPolicy
    panel = json.loads(Path(args.panel).read_text("utf-8"))
    payload = paired_transfer(panel=panel, budget=args.budget, repo=args.repo, kb=args.kb,
                              baseline_factory=ScriptedPolicy, intervention_factory=ScriptedPolicy)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("baseline", "intervention", "delta")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
