"""GRPO on a LoRA adapter with the compiler as the reward: exploration turned into weight updates.

    python -m eval.train_grpo --base MODEL --init-adapter SFT_ADAPTER --tasks TASKS.jsonl --context CTX.jsonl
                              --out DIR [--steps 120] [--group 4] [--prompts 2] [--twin-share 0.5]

Per step: draw `--prompts` TRAIN tasks; sample `--group` answers each from the current policy (temperature
sampling = exploration); score every answer with the reward service (eval/results/edit-capability-20261002/
reward_server.py: a separate process running the trusted grader -- explain answers are compiled, predict/need
answers compared with compiler labels; it refuses non-train ids); advantage = (reward - group mean) / group std;
loss = -advantage x mean log-probability of the answer's tokens. One on-policy update per batch, so the importance
ratio is 1 and no clipping is needed. Groups whose answers all score the same carry no signal and are skipped
(counted). No KL term: the only reference available in one loaded model is the untrained base, which would pull the
policy back past its own SFT; drift is controlled by a small learning rate and checked by the held-out gate
(frozen exam, twins, losses vs the SFT adapter), never by training reward.

`--twin-share` draws that fraction of prompts from twin tasks (the same pair under a retyped declaration): the
logic pilot showed SFT never learned them, and a reward on twins is only earned by reading the declaration.
Publishes the adapter only on a clean stop (step budget or deadline), with a receipt of every step's rewards.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADER_DIR = ROOT / "eval/results/edit-capability-20261002"
GRADER_PY = Path.home() / "decomp/sbk1/.venv/bin/python"


class Rewards:
    """The reward service as a child process (the grader runs in the project's compiler environment)."""

    def __init__(self, tasks: Path, context: Path, v2: bool = False):
        self.proc = subprocess.Popen([str(GRADER_PY), "reward_server.py", str(tasks), "--context", str(context),
                                      *(["--feedback-v2"] if v2 else [])],
                                     cwd=GRADER_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        ready = json.loads(self.proc.stdout.readline())
        if not ready.get("ready"):
            raise RuntimeError(f"reward service did not start: {ready}")
        self.train_tasks = ready["train_tasks"]

    def score(self, task_id: str, answer: str, feedback: bool = False) -> dict:
        self.proc.stdin.write(json.dumps({"id": task_id, "answer": answer, "feedback": feedback}) + "\n")
        self.proc.stdin.flush()
        out = json.loads(self.proc.stdout.readline())
        if "error" in out:
            raise RuntimeError(f"reward service: {out['error']}")
        return out

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


from eval.repair_prompts import logic_retry_prompt     # the retry turn, shared with eval/logic_exam.py


def chat_ids(tokenizer, messages):
    from eval.train_source_repair import _ids_of
    return _ids_of(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True))


def pick_tasks(tasks, kinds, tokenizer, max_prompt_tokens):
    from eval.train_source_repair import _ids_of
    pool = []
    for t in tasks:
        if t["split"] != "train" or t["kind"] not in kinds:
            continue
        ids = _ids_of(tokenizer.apply_chat_template([{"role": "user", "content": t["prompt"]}], tokenize=True,
                                                    add_generation_prompt=True))
        if len(ids) <= max_prompt_tokens:
            pool.append((t, ids))
    return pool


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--init-adapter", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--context", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--kinds", default="logic-explain,logic-predict,logic-need")
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--group", type=int, default=4)
    ap.add_argument("--prompts", type=int, default=2)
    ap.add_argument("--twin-share", type=float, default=0.5)
    ap.add_argument("--turns", type=int, default=1, help="attempts per explain episode, with compiler feedback")
    ap.add_argument("--turn-cost", type=float, default=0.1, help="reward deducted per extra turn on a solve")
    ap.add_argument("--keep-best", action="store_true",
                    help="a retry that got further from the target is pointed back to the episode's best attempt")
    ap.add_argument("--feedback-v2", action="store_true", help="verbatim compiler errors in feedback")
    ap.add_argument("--no-batch-gen", dest="batch_gen", action="store_false",
                    help="one generate call per context (the fallback the batched path uses on any error)")
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--max-new-tokens", type=int, default=384)
    ap.add_argument("--max-prompt-tokens", type=int, default=2600)
    ap.add_argument("--max-seconds", type=float, default=7200)
    ap.add_argument("--seed", type=int, default=20261004)
    a = ap.parse_args(argv)
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out} is not empty: no automatic reruns")
    a.out.mkdir(parents=True, exist_ok=True)

    from eval import resource_limits
    limits = resource_limits.apply()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    random.seed(a.seed)
    torch.manual_seed(a.seed)
    tokenizer = AutoTokenizer.from_pretrained(str(a.base), local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tasks = [json.loads(line) for line in a.tasks.read_text().splitlines() if line.strip()]
    pool = pick_tasks(tasks, set(a.kinds.split(",")), tokenizer, a.max_prompt_tokens)
    twins = [p for p in pool if ":twin:" in p[0]["id"]]
    others = [p for p in pool if ":twin:" not in p[0]["id"]]
    if not pool:
        raise SystemExit("no train tasks within the prompt window")
    rewards = Rewards(a.tasks, a.context, v2=a.feedback_v2)

    model = AutoModelForCausalLM.from_pretrained(
        str(a.base), dtype=torch.bfloat16, device_map="cuda:0",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_use_double_quant=True,
                                               bnb_4bit_compute_dtype=torch.bfloat16))
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = PeftModel.from_pretrained(model, str(a.init_adapter), is_trainable=True)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)

    stop = {"flag": False}
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.update(flag=True))
    started, log = time.time(), []
    stop_reason = "steps"
    for step in range(a.steps):
        if stop["flag"]:
            stop_reason = "interrupted"
            break
        if time.time() - started > a.max_seconds:
            stop_reason = "deadline"
            break
        batch = [random.choice(twins if twins and random.random() < a.twin_share else others or twins)
                 for _ in range(a.prompts)]
        row = {"step": step, "groups": [], "skipped_flat": 0}
        optimizer.zero_grad(set_to_none=True)
        n_samples = 0
        model.eval()
        model.config.use_cache = True

        def trim(seq):
            if tokenizer.eos_token_id in seq:             # padding only ever follows EOS
                seq = seq[:seq.index(tokenizer.eos_token_id) + 1]
            return seq

        def sample_one(ids, n):
            inp = torch.tensor([ids], device="cuda:0")
            with torch.no_grad():
                gen = model.generate(input_ids=inp, attention_mask=torch.ones_like(inp), do_sample=True,
                                     temperature=a.temperature, top_p=0.95, top_k=0,
                                     max_new_tokens=a.max_new_tokens, num_return_sequences=n,
                                     pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
            return [trim(seq) for seq in gen[:, len(ids):].tolist()]

        def sample(contexts, n):
            """n samples for EACH context, in one left-padded generate call; on any failure (e.g. out of memory)
            the step falls back to one call per context and the fallback is counted in the step log."""
            if a.batch_gen and len(contexts) > 1:
                try:
                    width = max(len(ids) for ids in contexts)
                    inp = torch.tensor([[tokenizer.pad_token_id] * (width - len(ids)) + ids for ids in contexts],
                                       device="cuda:0")
                    mask = torch.tensor([[0] * (width - len(ids)) + [1] * len(ids) for ids in contexts],
                                        device="cuda:0")
                    with torch.no_grad():
                        gen = model.generate(input_ids=inp, attention_mask=mask, do_sample=True,
                                             temperature=a.temperature, top_p=0.95, top_k=0,
                                             max_new_tokens=a.max_new_tokens, num_return_sequences=n,
                                             pad_token_id=tokenizer.pad_token_id,
                                             eos_token_id=tokenizer.eos_token_id)
                    flat = [trim(seq) for seq in gen[:, width:].tolist()]
                    return [flat[i * n:(i + 1) * n] for i in range(len(contexts))]
                except Exception as exc:
                    row["gen_fallback"] = row.get("gen_fallback", 0) + 1
                    row["gen_fallback_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    torch.cuda.empty_cache()
            return [sample_one(ids, n) for ids in contexts]

        # An EPISODE is up to `turns` attempts; after each failed attempt the model sees the compiler's feedback on
        # it (explain tasks; other kinds are one-answer questions). Every episode's generated turns are kept as
        # (context ids, generated ids) so the update covers the turn that read the feedback, too. Generation is
        # batched across tasks and, per turn, across every episode that still needs a retry.
        per_task = []
        for (task, prompt_ids), seqs in zip(batch, sample([ids for _t, ids in batch], a.group)):
            per_task.append((task, [{"segments": [(prompt_ids, seq)], "answer": seq, "open": True,
                                     "turns": a.turns if task["kind"] == "logic-explain" else 1,
                                     "messages": [{"role": "user", "content": task["prompt"]}]} for seq in seqs]))
        for turn in range(a.turns):
            retry = []
            for task, episodes in per_task:
                for ep in episodes:
                    if not ep["open"]:
                        continue
                    text = tokenizer.decode(ep["answer"], skip_special_tokens=True)
                    s = rewards.score(task["id"], text, feedback=turn + 1 < ep["turns"] or a.keep_best)
                    ep["turns_used"], ep["solved"], ep["base_reward"] = turn + 1, s["full"], s["reward"]
                    if s.get("distance") is not None and (ep.get("best") is None or s["distance"] < ep["best"][0]):
                        ep["best"] = (s["distance"], turn + 1, text, s["feedback"])
                    if s["full"] or turn + 1 >= ep["turns"]:
                        ep["open"] = False
                        continue
                    best = ep.get("best")
                    worse = a.keep_best and best is not None and best[1] != turn + 1 and s["distance"] > best[0]
                    ep["messages"] += [{"role": "assistant", "content": text},
                                       {"role": "user", "content": logic_retry_prompt(
                                           s["feedback"], worse_than=best[1:] if worse else None)}]
                    ids = chat_ids(tokenizer, ep["messages"])
                    if len(ids) > a.max_prompt_tokens + a.turns * a.max_new_tokens:
                        ep["open"] = False
                        continue
                    retry.append((ep, ids))
            if not retry:
                break
            for (ep, ids), seqs in zip(retry, sample([ids for _ep, ids in retry], 1)):
                ep["answer"] = seqs[0]
                ep["segments"].append((ids, seqs[0]))

        for task, episodes in per_task:
            # Solving in fewer turns is worth more: coverage at a budget, inside training.
            r = torch.tensor([ep["base_reward"] - a.turn_cost * (ep["turns_used"] - 1) if ep["solved"]
                              else ep["base_reward"] * (ep["turns_used"] == 1) for ep in episodes])
            row["groups"].append({"id": task["id"], "rewards": r.tolist(),
                                  "turns": [ep["turns_used"] for ep in episodes],
                                  "solved": [bool(ep["solved"]) for ep in episodes]})
            if float(r.std()) < 1e-6:
                row["skipped_flat"] += 1
                continue
            adv = (r - r.mean()) / (r.std() + 1e-4)
            model.train()
            model.config.use_cache = False
            for ep, advantage in zip(episodes, adv.tolist()):
                n_tokens = sum(len(seq) for _ids, seq in ep["segments"])
                if not n_tokens:
                    continue
                for ids, seq in ep["segments"]:
                    if not seq:
                        continue
                    full = torch.tensor([ids + seq], device="cuda:0")
                    positions = torch.arange(len(ids) - 1, len(ids) + len(seq) - 1, device="cuda:0")
                    logits = model(input_ids=full, logits_to_keep=positions).logits[0].float()
                    logp = torch.log_softmax(logits, -1).gather(1, full[0, len(ids):, None]).squeeze(1)
                    # Token-mean over the whole episode, so a long episode does not outweigh a short one.
                    loss = -advantage * logp.sum() / n_tokens / (a.prompts * a.group)
                    loss.backward()
                n_samples += 1
        if n_samples:
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
        row.update(samples=n_samples, mean_reward=float(sum(sum(g["rewards"]) for g in row["groups"]) /
                                                        max(1, sum(len(g["rewards"]) for g in row["groups"]))),
                   elapsed_s=round(time.time() - started, 1),
                   peak_gpu_gb=round(torch.cuda.max_memory_allocated() / 2**30, 3))
        log.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "groups"}), flush=True)
        with (a.out / "steps.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
    rewards.close()

    receipt = {"stop_reason": stop_reason, "steps_run": len(log), "config": {k: str(v) for k, v in vars(a).items()},
               "pool": {"tasks": len(pool), "twins": len(twins)}, "reward_train_tasks": rewards.train_tasks,
               "limits": str(limits), "published": False,
               "mean_reward_first_10": sum(r["mean_reward"] for r in log[:10]) / max(1, len(log[:10])),
               "mean_reward_last_10": sum(r["mean_reward"] for r in log[-10:]) / max(1, len(log[-10:])),
               "flat_groups": sum(r["skipped_flat"] for r in log),
               "groups": sum(len(r["groups"]) for r in log)}
    if stop_reason in ("steps", "deadline") and log:
        staging = a.out / "staging"
        model.save_pretrained(str(staging))
        tokenizer.save_pretrained(str(staging))
        for item in staging.iterdir():
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, a.out / item.name)
        receipt["published"] = True
        (a.out / "PUBLISHED.json").write_text(json.dumps({"published_at": int(time.time()), "steps": len(log),
                                                          "init_adapter": str(a.init_adapter)}))
    (a.out / "grpo_receipt.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))
    return 0 if receipt["published"] else 1


if __name__ == "__main__":
    sys.exit(main())
