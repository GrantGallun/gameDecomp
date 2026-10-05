"""Can the base model ACT? Zero-shot action-format competence, measured before any training.

THE DISTINCTION THIS TESTS. Two different capabilities are being confused when we ask "can it use the
tools":

  knowing WHICH tool   needs decision signal, and the corpus supplies 133 usable preference pairs
  knowing HOW to act   emitting a legal action, reading a result, not looping -- a FORMAT and
                       PROCEDURE capability, learnable from a few demonstrations whatever the
                       decision signal turns out to be

The second is a prerequisite for measuring the first. A model that cannot emit a valid action cannot
demonstrate tool selection at all, and every selection experiment run against it would be measuring
the interface instead.

WHAT IS MEASURED, per function and per draw:
  parse     the output is JSON
  validate  it names a known action with legal parameters (`tool_registry.validate_json`)
  legal     `stop` only when the certificate has already passed
  repeat    it proposes an action that just returned no-change -- looping, not deciding

No training, no adapter: this is the BASE model's starting point, which is what decides whether
format training has a gap to close.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The assistant turn is primed with this, and the continuation is re-assembled with it. Measured:
# without a prefill the base model wraps every reply in a ```json fence and validates 0 of 12.
PREFILL = "{"

SYSTEM = """You choose the next action for a decompilation repair attempt.

Reply with ONE JSON object and nothing else:
  {"action": "<name>", "params": {<arguments>}}

Available actions:
{space}

Rules:
- The verdict is not yours to make. `compile` reports what the certificate said.
- THE FRAMEWORK COMPILES FOR YOU. It compiles the initial candidate and recompiles after every source
  change, and the observation states whether the current source carries a verdict. If it does,
  `compile` again returns the same answer and wastes the slot; if the observation says UNVERIFIED,
  `compile` is the action that produces a verdict.
- Never repair against a diagnostic that describes a different source than the current candidate.
- If an action returns no-change or not-applicable against an unchanged source, do not propose it again.
- Propose `stop` when the certificate has passed, when the budget is exhausted, or when nothing
  further you can choose would change the situation. Say which, in `reason`."""


# Statuses that mean "this action produced nothing". Retrying one of these without an intervening
# source change is the loop the system prompt already warns against, and naming the set explicitly is
# what lets the model SEE it rather than infer it from a status string it may not know.
NO_EFFECT_STATUSES = ("no-change", "not-applicable")


def step_view(step) -> dict:
    """One recorded step as the renderer must see it: the ARGUMENTS and the tool's ACTUAL RESULT.

    THE SAME BUG, TWICE. `observation()` was fixed to emit `detail` (compiler stderr, the diff, a
    missing prerequisite's reason), and the unit tests called it directly and passed -- while
    `ModelPolicy._render` one call earlier still projected history down to
    `action/status/changed/exact`. The live path therefore kept showing the model
    `diffrepair -> no-change` with no reason, no diff and no arguments, and a head-to-head run
    against that prompt is a measurement of the prompt, not of the policy.

    The lesson is not "fix the renderer twice", it is that a renderer test must go THROUGH the
    policy. `tests/test_tool_boundary.py::test_the_model_policy_prompt_carries_the_tool_result`
    does that, and this function exists so there is exactly one projection to test.
    """
    def field(name, default=None):
        if isinstance(step, dict):
            return step.get(name, default)
        return getattr(step, name, default)

    return {"index": field("index"), "kind": field("kind"),
            "action": field("action"), "params": field("params") or {},
            "status": field("status"), "changed": bool(field("changed", False)),
            "exact": bool(field("exact", False)), "detail": field("detail") or {},
            # The hash the diagnostic belongs to. Without it a renderer cannot tell a fresh verdict
            # from one about a candidate that no longer exists, which is exactly the stale-diff bug.
            "candidate_sha256": field("candidate_sha256") or "",
            "pre_action_sha256": field("pre_action_sha256") or "",
            "cancelled": bool(field("cancelled", False))}


def tried_since_change(history) -> set[str]:
    """Actions that already returned nothing SINCE THE LAST SOURCE CHANGE.

    Scoped that way deliberately: the same tool can be useful after another transformation moved the
    source (spec §2 forbids a blanket ban on calling a tool twice). What must not repeat is a
    no-effect call against an unchanged input, so only steps after the most recent `changed` step
    count.
    """
    last_change = max((i for i, s in enumerate(history)
                       if step_view(s)["changed"]), default=-1)
    return {step_view(s)["action"] for s in history[last_change + 1:]
            if step_view(s)["status"] in NO_EFFECT_STATUSES}


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def compile_state(candidate: str, history) -> dict:
    """Does the CURRENT source already carry a fresh verdict from the controller?

    THE COMPILATION CONTRACT, made visible. The framework compiles the initial candidate and every
    source a transform produced, so at most decisions the answer is already available and choosing
    `compile` again can only return the same verdict for the same source. The first version hid this:
    the prompt told the model to compile after a transform AND the loop already had, so the model was
    being trained into a redundant slot, and the two disagreed about who owns compilation.

    A verdict counts only when it describes THIS source (`candidate_sha256`), which is also how a
    stale diagnostic is detected: `stale` names the case where the last thing the compiler said was
    about a different candidate than the one in front of the policy.
    """
    sha = _sha256(candidate)
    verified, index, status, exact, stale = False, None, None, False, False
    for position, raw in enumerate(history):
        step = step_view(raw)
        detail = step["detail"]
        carries_verdict = ("certificate_status" in detail or "compiled" in detail
                           or (step["action"] == "compile" and step["status"] in ("ok", "failed")))
        if carries_verdict:
            if step.get("candidate_sha256") == sha:
                verified, index, status, exact = True, position, detail.get("certificate_status"), \
                    bool(step["exact"] or detail.get("exact"))
            else:
                stale = True
    return {"verified": verified, "step": index, "certificate_status": status, "exact": exact,
            "stale": bool(stale and not verified), "source_sha256": sha}


def observation(function: str, candidate: str, history: list[dict], *,
                detail_chars: int = 400, source_chars: int = 1200, budget: int | None = None,
                tried: set[str] | None = None, tool_detail: bool = True,
                state_detail: bool = True, notes: list | None = None) -> str:
    """The policy's view of one decision. ONE renderer, shared by training and inference.

    WHAT THE FIRST VERSION OMITTED, and why it mattered: it showed only `action -> status`, dropping
    the tool's actual result -- the compiler's error text, the instruction diff, a missing
    prerequisite's reason, the arguments used. A model cannot act on information it never receives,
    so a negative result measured against this prompt would have been a measurement of the prompt.

    TRUNCATION IS EXPLICIT, not silent: the limits are stated in the text so the model knows it is
    reading an excerpt rather than the whole artifact, and the full artifacts stay outside the prompt.

    TWO ABLATION LEVELS, because one of them cannot answer the question on its own:
      `tool_detail=False`  removes the per-step RESULT fields (stderr, diff, reason, score, counts).
      `state_detail=False` also removes the DERIVED state lines (verification, staleness, no-effect
                           set). A policy that survives the first ablation may simply be reading the
                           state line, which is itself derived from the result -- so the first level
                           measures "does it need the raw evidence", and the second measures "does it
                           read the observation at all rather than a fixed order".
    """
    lines = [f"function: {function}",
             f"remaining budget: {'unknown (the loop did not supply it)' if budget is None else budget}"
             f" action slot(s)", ""]
    state = compile_state(candidate, history)
    if not state_detail:
        lines += ["(the derived state lines are withheld for this run: read the steps below)", ""]
    elif state["verified"]:
        lines += [f"verification of the current source: ALREADY COMPILED at step {state['step']}"
                  f" (certificate_status={state['certificate_status']}, exact={state['exact']})."
                  f" The framework recompiles after every source change, so `compile` again on this"
                  f" unchanged source returns the same verdict and spends a slot.", ""]
    else:
        lines += ["verification of the current source: UNVERIFIED -- no compile result describes it."
                  " `compile` is the action that produces one.", ""]
    if state_detail and state["stale"]:
        lines += ["note: the last recorded diagnostic describes a DIFFERENT source than the current"
                  " candidate; do not repair against it.", ""]
    if state_detail and tried:
        lines += [f"actions that returned nothing against this unchanged source:"
                  f" {', '.join(sorted(tried))}", ""]
    # VERIFIED FINDINGS, when a generation carries them. This is the memory intervention: a confirmed
    # research finding is retrieved ONLY for states its applicability block matches, and it arrives as
    # text in the observation -- the same renderer and the same position for training and inference, so a
    # note cannot be a privileged channel that only the evaluated arm sees. The note states what was
    # measured and how strongly; it never claims the current function is solved.
    if notes:
        lines += ["verified findings from earlier experiments (measured, not assumed):"]
        for note in notes:
            text = note.get("text") if isinstance(note, dict) else str(note)
            evidence = (note or {}).get("provenance", {}).get("finding_id") if isinstance(note, dict) else ""
            lines.append(f"  - {text}" + (f"  [finding {evidence}]" if evidence else ""))
        lines.append("")
    lines += [f"current candidate C (first {source_chars} chars"
              + (", TRUNCATED" if len(candidate) > source_chars else "") + "):",
              "```c", candidate[:source_chars], "```", "", "actions already tried:"]
    if not history:
        lines.append("  (none)")
    for raw in history:
        step = step_view(raw)
        lines.append(f"  {step['action']} {json.dumps(step['params'])}"
                     f" -> {step['status']}"
                     + (" (changed the source)" if step["changed"] else "")
                     + (" EXACT" if step["exact"] else "")
                     + (" (CANCELLED)" if step.get("cancelled") else ""))
        # THE ACTUAL TOOL RESULT. This is the part the first version threw away.
        #
        # `tool_detail=False` is the ABLATION (spec §6), and it exists so the question "does the policy
        # act on the result or on the shape of the prompt?" has a measurement rather than an argument.
        # The step lines stay -- same actions, same statuses, same order -- and only the informative
        # fields go, so a policy that keeps scoring well is using the shape, and one that collapses was
        # reading the evidence.
        detail = step["detail"] if tool_detail else {}
        for key in ("reason", "error", "stderr", "diff", "best_label", "compiles",
                    "certificate_status", "score", "count", "trace_calls"):
            value = detail.get(key)
            if value in (None, "", [], {}):
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            if len(text) > detail_chars:
                text = text[:detail_chars] + f"... (TRUNCATED, {len(text)} chars total)"
            lines.append(f"      {key}: {text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path,
                    default=Path.home() / "decomp/models/qwen2.5-coder-7b")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--functions", type=int, default=12)
    ap.add_argument("--draws", type=int, default=1)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/tool-agent-20260920/action-probe.json")
    args = ap.parse_args(argv)

    from eval import resource_limits
    from eval.tool_registry import action_space, validate_json
    from eval.tool_agent_run import build_context

    conn_ro = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
    names = [r[0] for r in conn_ro.execute(
        "select f.name from attempts a join functions f on f.addr = a.func_addr "
        "group by f.name having sum(coalesce(a.exact,0)) = 0 order by max(f.size) asc limit ?",
        (args.functions,)).fetchall()]
    conn_ro.close()

    space = json.dumps(action_space(), indent=2)
    # `.replace`, not `.format`: the prompt contains literal JSON braces (`{"action": ...}`) and
    # `str.format` reads them as replacement fields, which failed with KeyError: '"action"'.
    system = SYSTEM.replace("{space}", space)

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

    conn = sqlite3.connect(str(args.kb))
    rows, calls = [], 0
    started = time.time()
    for name in names:
        try:
            context, why = build_context(Path.home() / "decomp/sbk1", name, conn=conn)
        except Exception as exc:                                # noqa: BLE001
            rows.append({"function": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if context is None:
            rows.append({"function": name, "error": why})
            continue
        history: list[dict] = []
        for draw in range(args.draws):
            prompt = observation(name, context.candidate, history)
            ids = tokenizer.apply_chat_template(
                [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                tokenize=True, add_generation_prompt=True)
            if hasattr(ids, "keys"):
                ids = ids["input_ids"]
            ids = list(ids)
            # PRIME THE ASSISTANT TURN WITH THE OPENING BRACE, exactly as `solver/llm.py` primes with
            # an opening code fence. Without a prefill the model opens its OWN fence, every reply
            # arrived as ```json ... ``` and json.loads rejected all 12 -- a 0.000 validate rate that
            # measured the prompt harness rather than the model. The continuation is re-assembled
            # with the brace it was handed, because the model continues the prefill rather than
            # repeating it.
            prefill = tokenizer(PREFILL, add_special_tokens=False)["input_ids"]
            ids = ids + list(prefill)
            tensor = torch.tensor([ids], device="cuda:0")
            with torch.no_grad():
                out = model.generate(input_ids=tensor, attention_mask=torch.ones_like(tensor),
                                     max_new_tokens=args.max_new_tokens, do_sample=False,
                                     pad_token_id=tokenizer.pad_token_id)
            calls += 1
            continuation = tokenizer.decode(out[0][len(ids):], skip_special_tokens=True)
            text = (PREFILL + continuation).strip()
            row = {"function": name, "draw": draw, "raw_head": text[:200]}
            try:
                request = validate_json(text)
                row["parse"], row["validate"] = True, True
                row["action"] = request["action"]
                row["params"] = request["params"]
            except ValueError as exc:
                row["parse"] = text.strip().startswith("{")
                row["validate"] = False
                row["error"] = str(exc)[:160]
            history.append({"action": row.get("action") or "<invalid>",
                            "params": row.get("params") or {},
                            "status": row.get("error") or "proposed",
                            "changed": False, "exact": False,
                            "detail": {"error": row["error"]} if row.get("error") else {}})
            rows.append(row)
            print(json.dumps(row), flush=True)
    conn.close()

    valid = [r for r in rows if r.get("validate")]
    payload = {
        "base": str(args.base), "functions": len(names), "calls": calls,
        "seconds": round(time.time() - started, 1),
        "parsed_json": sum(1 for r in rows if r.get("parse")),
        "validated": len(valid),
        "validate_rate": round(len(valid) / max(1, len(rows)), 4),
        "action_counts": {a: sum(1 for r in valid if r["action"] == a)
                          for a in sorted({r["action"] for r in valid})},
        "rows": rows,
        "note": ("zero-shot base model, no adapter and no training: this is the starting point that "
                 "decides whether action-format competence has a gap for training to close"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
