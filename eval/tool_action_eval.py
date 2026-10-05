"""Three arms, one interface, two environments: does the policy ACT ON THE OBSERVATION?

ARMS
  A  `scripted`  the fixed declared order -- the null hypothesis, no model.
  B  `base`      the installed base model with the same JSON prefill and the same renderer.
  C  `adapter`   the same policy code with the trained LoRA action adapter loaded.
Every arm faces the same renderer, the same action space, the same runtime guards and the same
budget; the only difference is who chooses.

ENVIRONMENTS
  `procedural`  held-out exercises, graded MECHANICALLY against the state rules (`label_for`). No
                compiler is involved, so this measures protocol and procedure on its own -- which is
                what makes it possible to report learning without waiting for exact game solves.
  `panel`       real game functions with the real oracle, graded by the same mechanical rules at
                every decision, and additionally by the CERTIFICATE (certified matches). Procedure
                and outcome are reported separately because they are different claims: an episode can
                be procedurally clean and still not solve the function, and a lucky solve does not
                prove every preceding action was right.

WHAT IS NOT DONE HERE. No adapter is promoted, no ratchet is touched, and no number from this file is
a capability claim about decompilation. `certified_matches` on a handful of functions is a pilot.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

JSON_SAFE = (str, int, float, bool, type(None))


def context_keys(context) -> dict:
    keys = {k: v for k, v in context.as_dict().items() if isinstance(v, JSON_SAFE)}
    keys["compile_fn"] = bool(context.compile_fn)
    return keys


class GradingPolicy:
    """Wraps any policy and grades every decision it makes with the SAME mechanical rules.

    The wrapper is the fairness mechanism: the baseline and the adapter are graded by one function,
    from the state each was actually handed, so a difference in the score cannot come from a
    difference in how the two arms were judged.
    """

    def __init__(self, inner, *, function: str, dataset: str):
        self.inner, self.function, self.dataset = inner, function, str(dataset)
        self.name = f"graded({getattr(inner, 'name', type(inner).__name__)})"
        self.decisions: list[dict] = []

    def choose(self, context, history):
        from eval.tool_action_dataset import label_for, prerequisites_met
        from eval.tool_registry import ACTIONS

        keys = context_keys(context)
        label = label_for(context.candidate, history,
                          context.budget_remaining if context.budget_remaining is not None else 0,
                          keys)
        action, params = self.inner.choose(context, history)
        acceptable = [item["action"] for item in label["acceptable"]]
        # MISSING-PREREQUISITE CALLS ARE COUNTED SEPARATELY (spec §6) because they are a different
        # mistake from a bad choice: the action is legal and would have been reasonable if the context
        # had carried what it needs. Reporting them inside the acceptable rate hides the distinction,
        # and this is the metric the correction round is aimed at -- the first adapter proposed
        # `diffrepair` in states with no diff at all.
        needs_missing = bool(action in ACTIONS and not prerequisites_met(keys, action))
        self.decisions.append({
            "function": self.function, "dataset": self.dataset,
            "step": getattr(context, "budget_remaining", None),
            "action": action, "params": params,
            "acceptable": acceptable, "forbidden": label["forbidden"], "rule": label["rule"],
            "is_acceptable": action in acceptable,
            "needs_missing": needs_missing,
            "redundant_compile": bool(action == "compile" and "compile" not in acceptable
                                      and label["rule"].startswith("verified")),
            "repeat_after_no_effect": action in label["forbidden"],
            "premature_stop": bool(action == "stop" and acceptable != ["stop"]),
            "honest_stop": bool(action == "stop" and acceptable == ["stop"]),
        })
        return action, params


def summarize_decisions(decisions: list[dict]) -> dict:
    total = len(decisions)
    stops = [d for d in decisions if d["action"] == "stop"]
    return {
        "decisions": total,
        "acceptable": sum(1 for d in decisions if d["is_acceptable"]),
        "acceptable_rate": round(sum(1 for d in decisions if d["is_acceptable"]) / max(1, total), 4),
        "redundant_compiles": sum(1 for d in decisions if d["redundant_compile"]),
        "missing_prerequisite_calls": sum(1 for d in decisions if d.get("needs_missing")),
        "repeats_after_no_effect": sum(1 for d in decisions if d["repeat_after_no_effect"]),
        "premature_stops": sum(1 for d in decisions if d["premature_stop"]),
        "honest_stops": sum(1 for d in decisions if d["honest_stop"]),
        "stop_rate": round(len(stops) / max(1, total), 4),
        "action_counts": {name: sum(1 for d in decisions if d["action"] == name)
                          for name in sorted({d["action"] for d in decisions})},
    }


def load_policy(arm: str, args):
    """Build one arm. The model arms share ALL of their code; only the adapter path differs."""
    from eval.tool_agent import ScriptedPolicy

    if arm == "scripted":
        return ScriptedPolicy()
    from eval.tool_agent_compare import ModelPolicy
    from eval.tool_agent_probe import PREFILL, SYSTEM
    from eval.tool_registry import action_space

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tokenizer = AutoTokenizer.from_pretrained(str(args.base))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        str(args.base), dtype=torch.bfloat16, device_map="cuda:0",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16))
    if arm == "adapter":
        if not args.adapter:
            raise SystemExit("--adapter is required for arm C")
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(args.adapter))
    model.eval()
    system = SYSTEM.replace("{space}", json.dumps(action_space(), indent=2))
    return ModelPolicy(model, tokenizer, system, PREFILL, max_new_tokens=args.max_new_tokens,
                       show_tool_detail=not args.ablate_detail,
                       show_state_detail=not args.ablate_state, name=f"model-{arm}")


def load_adapter_policy(base, adapter, *, show_tool_detail: bool = True,
                        max_new_tokens: int = 96):
    """The trained arm as a plain policy, for the correction round's collection run.

    Deliberately routed through `load_policy` rather than re-loading a model here: the states the
    correction round trains on must be visited under the SAME interface the evaluation uses, or the
    corrections would be collected from a slightly different policy than the one being corrected.
    """
    class _Args:
        pass

    args = _Args()
    args.base, args.adapter = Path(base), Path(adapter)
    args.ablate_detail, args.ablate_state = not show_tool_detail, False
    args.max_new_tokens = max_new_tokens
    return load_policy("adapter", args)


def run_procedural(arm: str, args, policy) -> dict:
    """One decision per held-out exercise, graded against the state's mechanical label."""
    rows = [json.loads(line) for line in Path(args.dataset).read_text("utf-8").splitlines() if line]
    rows = [row for row in rows if row.get("split") == args.split]
    graded = GradingPolicy(policy, function="procedural", dataset=args.dataset)
    raw, started = [], time.time()
    for row in rows:
        from eval.tool_action_dataset import state_from_record
        context, steps = state_from_record(row)
        context.budget_remaining = row.get("budget_remaining")
        before = len(graded.decisions)
        action, params = graded.choose(context, steps)
        record = graded.decisions[-1]
        record["id"] = row["id"]
        record["label_confidence"] = row["label_confidence"]
        record["stored_acceptable"] = [item["action"] for item in row["acceptable"]]
        record["reproduced_label"] = record["acceptable"] == record["stored_acceptable"]
        raw.append(record)
        if len(graded.decisions) == before:
            raw[-1]["error"] = "no decision recorded"
    summary = summarize_decisions(graded.decisions)
    summary.update({"arm": arm, "mode": "procedural", "split": args.split,
                    "exercises": len(rows), "ablate_detail": bool(args.ablate_detail),
                    "ablate_state": bool(args.ablate_state),
                    "seconds": round(time.time() - started, 1),
                    "label_reproduced": sum(1 for r in raw if r.get("reproduced_label")),
                    "invalid_proposals": getattr(policy, "invalid", 0),
                    "generations": getattr(policy, "generations", 0),
                    "generated_tokens": getattr(policy, "new_tokens", 0)})
    return {"summary": summary, "rows": raw}


def run_panel(arm: str, args, policy) -> dict:
    """Real functions: real m2c drafts, real runners, real oracle. Budgeted before it starts."""
    import sqlite3

    from eval import resource_limits
    from eval.tool_agent import run_episode
    from eval.tool_agent_run import build_context
    from eval.tool_registry import ACTIONS

    limits = resource_limits.apply()
    splits = json.loads(Path(args.splits).read_text("utf-8")) if args.splits else {}
    names = list(splits.get(args.split) or [])[:args.functions]
    if not names:
        raise SystemExit(f"no functions in {args.split!r} of {args.splits}")
    runners = {a.runner: a.resolve() for a in ACTIONS.values() if a.runner}
    conn = sqlite3.connect(str(args.kb)) if args.kb else None

    rows, decisions, started = [], [], time.time()
    for name in names:
        try:
            context, why = build_context(args.repo, name, conn=conn)
        except Exception as exc:                                # noqa: BLE001
            rows.append({"function": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if context is None:
            rows.append({"function": name, "error": why})
            continue
        graded = GradingPolicy(policy, function=name, dataset=f"panel:{args.split}")
        before = getattr(policy, "generations", 0)
        episode_started = time.time()
        transcript = run_episode(context, graded, budget=args.budget, runners=runners)
        decisions.extend(graded.decisions)
        setup_compiles = 1 if context.initial_verdict else 0
        internal = sum(int(s.detail.get("internal_compiles") or 0) for s in transcript.steps)
        rows.append({
            "function": name, "exact": transcript.exact, "stop_reason": transcript.stop_reason,
            "decisions": [{"action": s.action, "status": s.status, "changed": s.changed}
                          for s in transcript.steps if s.index >= 0],
            "setup_compiles": setup_compiles, "internal_compiles": internal,
            "candidates_seen": transcript.candidates_seen,
            "seconds": round(time.time() - episode_started, 2),
            "generations": getattr(policy, "generations", 0) - before,
        })
        print(json.dumps({**rows[-1], "decisions": [d["action"] for d in rows[-1]["decisions"]]}),
              flush=True)
    if conn is not None:
        conn.close()
    summary = summarize_decisions(decisions)
    summary.update({
        "arm": arm, "mode": "panel", "split": args.split, "functions": len(rows),
        "certified_matches": sum(1 for r in rows if r.get("exact")),
        "setup_compiles": sum(r.get("setup_compiles", 0) for r in rows),
        "internal_compiles": sum(r.get("internal_compiles", 0) for r in rows),
        "candidates_seen": sum(r.get("candidates_seen", 0) for r in rows),
        "generations": getattr(policy, "generations", 0),
        "generated_tokens": getattr(policy, "new_tokens", 0),
        "invalid_proposals": getattr(policy, "invalid", 0),
        "seconds": round(time.time() - started, 1),
        "limits": limits.as_dict(), "budget": args.budget,
    })
    return {"summary": summary, "rows": rows, "decisions": decisions}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=("scripted", "base", "adapter"), required=True)
    ap.add_argument("--mode", choices=("procedural", "panel"), required=True)
    ap.add_argument("--dataset", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/procedural-test.jsonl")
    ap.add_argument("--split", default="test")
    ap.add_argument("--base", type=Path, default=Path.home() / "decomp/models/qwen2.5-coder-7b")
    ap.add_argument("--adapter", type=Path, default=None)
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--splits", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--functions", type=int, default=4)
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    ap.add_argument("--ablate-detail", action="store_true",
                    help="DEV ablation: remove the informative tool-result fields and see whether "
                         "the demonstrated ability survives")
    ap.add_argument("--ablate-state", action="store_true",
                    help="stronger ablation: also remove the DERIVED state lines (verification, "
                         "staleness, no-effect set), leaving only action -> status history")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    policy = load_policy(args.arm, args)
    payload = (run_procedural(args.arm, args, policy) if args.mode == "procedural"
               else run_panel(args.arm, args, policy))
    payload["summary"].update({
        "base": str(args.base), "adapter": str(args.adapter) if args.adapter else None,
        "renderer_version": __import__("eval.tool_action_dataset",
                                       fromlist=["RENDERER_VERSION"]).RENDERER_VERSION,
        "tool_schema": "eval.tool_registry.action_space",
    })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
