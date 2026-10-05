"""The model's own zero-shot policy, through the loop, against the scripted order. Same budget.

WHY THIS IS THE DECISIVE EXPERIMENT RIGHT NOW. The base model, with no training, picks
`resolve-placeholders` first on 8 of 12 functions -- the one action the scripted run found actually
closes matches. If an untrained model beats a fixed order simply by choosing in a better order, then
the thing worth training is not tool syntax and not tool selection from scratch: it is the second and
third decision, which is a far narrower target.

BOTH ARMS RUN THE SAME ACTIONS THROUGH THE SAME DISPATCH. The model emits `{"action", "params"}`, the
registry validates and executes it, the real oracle compiles the result, and the outcome is fed back
into the history the next decision sees. So this is a closed loop, not a transcript replay.

EQUAL BUDGET means equal ACTION slots, and both arms are capped identically. It does not mean equal
wall clock: the scripted arm spends m2c subprocesses that the model arm does not, and that asymmetry
is reported rather than hidden.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModelPolicy:
    """The base model as a policy. Greedy, prefilled with `{`, validated by the registry."""

    name = "model-zero-shot"

    def __init__(self, model, tokenizer, system: str, prefill: str, max_new_tokens: int = 96,
                 *, show_tool_detail: bool = True, show_state_detail: bool = True,
                 notes: list | None = None, name: str | None = None):
        self.model, self.tokenizer = model, tokenizer
        self.system, self.prefill, self.max_new_tokens = system, prefill, max_new_tokens
        self.show_tool_detail = show_tool_detail
        self.show_state_detail = show_state_detail
        # The generation's verified-memory snapshot, as retrieved notes. Empty for S0.
        self.notes = list(notes or [])
        if name:
            self.name = name
        self.generations = 0
        self.new_tokens = 0
        self.invalid = 0
        self.raw: list[str] = []

    def _render(self, context, history) -> str:
        """The policy's view, built ONLY from the shared renderer.

        This method used to project history to `action/status/changed/exact` and hand that to
        `observation()`, which had just been taught to emit `detail`. The result: every renderer unit
        test passed while the live model still read `diffrepair -> no-change` with no reason, no diff
        and no arguments -- so the head-to-head's zero was measured against the old defect. The
        projection now lives in `step_view` and there is exactly one of it.
        """
        from eval.tool_agent_probe import observation, step_view, tried_since_change
        return observation(context.function, context.candidate,
                           [step_view(s) for s in history],
                           budget=getattr(context, "budget_remaining", None),
                           tried=tried_since_change(history),
                           tool_detail=self.show_tool_detail,
                           state_detail=self.show_state_detail, notes=self.notes)

    def choose(self, context, history) -> tuple[str, dict]:
        import torch
        from eval.tool_registry import validate_json
        prompt = self._render(context, history)
        ids = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": self.system}, {"role": "user", "content": prompt}],
            tokenize=True, add_generation_prompt=True)
        if hasattr(ids, "keys"):
            ids = ids["input_ids"]
        prefill = self.tokenizer(self.prefill, add_special_tokens=False)["input_ids"]
        ids = list(ids) + list(prefill)
        tensor = torch.tensor([ids], device="cuda:0")
        with torch.no_grad():
            out = self.model.generate(input_ids=tensor, attention_mask=torch.ones_like(tensor),
                                      max_new_tokens=self.max_new_tokens, do_sample=False,
                                      pad_token_id=self.tokenizer.pad_token_id)
        self.generations += 1
        self.new_tokens += int(out.shape[1] - len(ids))
        text = (self.prefill + self.tokenizer.decode(out[0][len(ids):],
                                                     skip_special_tokens=True)).strip()
        self.raw.append(text[:200])
        try:
            request = validate_json(text)
        except ValueError:
            # An invalid proposal is a real outcome, not a crash: the loop records it and stops.
            self.invalid += 1
            return "stop", {"reason": f"invalid proposal: {text[:80]!r}"}
        return request["action"], request["params"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, default=Path.home() / "decomp/models/qwen2.5-coder-7b")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--functions", type=int, default=12)
    ap.add_argument("--budget", type=int, default=7, help="action slots per arm")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/tool-agent-20260920/head-to-head.json")
    args = ap.parse_args(argv)

    from eval import resource_limits, tool_runners
    from eval.tool_agent import ScriptedPolicy, run_episode
    from eval.tool_agent_probe import PREFILL, SYSTEM
    from eval.tool_agent_run import build_context
    from eval.tool_registry import ACTIONS, action_space

    ro = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
    names = [r[0] for r in ro.execute(
        "select f.name from attempts a join functions f on f.addr = a.func_addr "
        "group by f.name having sum(coalesce(a.exact,0)) = 0 order by max(f.size) asc limit ?",
        (args.functions,)).fetchall()]
    ro.close()
    print(json.dumps({"functions": len(names), "budget": args.budget}), flush=True)

    limits = resource_limits.apply()
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
    model.eval()

    system = SYSTEM.replace("{space}", json.dumps(action_space(), indent=2))
    runners = {a.runner: a.resolve() for a in ACTIONS.values() if a.runner}
    conn = sqlite3.connect(str(args.kb))
    rows = []
    for name in names:
        try:
            context, why = build_context(args.repo, name, conn=conn)
        except Exception as exc:                                # noqa: BLE001
            rows.append({"function": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if context is None:
            rows.append({"function": name, "error": why})
            continue
        import copy
        entry = {"function": name}
        for label, policy in (("scripted", ScriptedPolicy()),
                              ("model", ModelPolicy(model, tokenizer, system, PREFILL))):
            ctx = copy.copy(context)
            started = time.time()
            transcript = run_episode(ctx, policy, budget=args.budget, runners=runners)
            entry[label] = {
                "exact": transcript.exact, "actions": [s.action for s in transcript.steps],
                "statuses": [s.status for s in transcript.steps],
                "candidates_seen": transcript.candidates_seen,
                "seconds": round(time.time() - started, 2),
                "generations": getattr(policy, "generations", 0),
                "invalid": getattr(policy, "invalid", 0),
            }
        rows.append(entry)
        print(json.dumps(entry), flush=True)
    conn.close()

    def total(arm, key):
        return sum(r[arm][key] for r in rows if arm in r)

    payload = {
        "functions": len(rows), "budget": args.budget,
        "scripted": {"exact": sum(1 for r in rows if r.get("scripted", {}).get("exact")),
                     "candidates_seen": total("scripted", "candidates_seen"),
                     "seconds": round(total("scripted", "seconds"), 1),
                     "generations": 0},
        "model": {"exact": sum(1 for r in rows if r.get("model", {}).get("exact")),
                  "candidates_seen": total("model", "candidates_seen"),
                  "seconds": round(total("model", "seconds"), 1),
                  "generations": total("model", "generations"),
                  "invalid_proposals": total("model", "invalid")},
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
