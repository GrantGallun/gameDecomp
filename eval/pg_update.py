"""One on-policy policy-gradient update of a LoRA adapter from samples scored elsewhere (sequential RL).

    python -m eval.pg_update --base MODEL --init-adapter ADAPTER --samples SAMPLES.jsonl --out NEW_ADAPTER
    python -m eval.pg_update --base MODEL --fresh --out ADAPTER          (a LoRA equal to the base: B starts at 0)
    SAMPLES.jsonl rows: {"group": id, "prompt": text, "completion": text, "reward": float, ["messages": [...]]}
    (`messages`, when present, is the conversation the completion followed, e.g. after web tool calls; only the final
    turn is trained, so tool-call turns earn no direct credit yet.)

Why separate: an examiner's reward needs the SOLVER served (vLLM) to attempt its tasks, and the 16 GB card cannot
hold a vLLM server and a training copy of the model at once. So RL runs SEQUENTIALLY: serve (examiner + solver
adapters) -> sample proposals from the CURRENT examiner and score them -> stop -> this update -> serve the new
examiner -> ... Every update uses samples drawn from the adapter it updates, so it is on-policy (importance ratio 1,
no clipping), the same objective as eval/train_grpo.py: advantage = (reward - group mean) / group std, loss = -adv x
token-mean log-probability of the completion. Groups with equal rewards carry no signal and are skipped (counted).

Completions are re-tokenized from text (the serving receipt does not carry token ids); a tokenization that differs
from the sampled one changes the log-probability of a few boundary tokens, recorded as a known approximation.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import time
from pathlib import Path

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def advantages(rows: list[dict]) -> tuple[list[tuple[dict, float]], int]:
    """(sample, advantage) for every sample in a group whose rewards differ; and the number of flat groups."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    out, flat = [], 0
    for members in groups.values():
        rewards = [m["reward"] for m in members]
        mean = sum(rewards) / len(rewards)
        std = (sum((x - mean) ** 2 for x in rewards) / len(rewards)) ** 0.5
        if std < 1e-6:
            flat += 1
            continue
        out += [(m, (m["reward"] - mean) / (std + 1e-4)) for m in members]
    return out, flat


def _load(base: Path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(str(base), local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(base), dtype=torch.bfloat16, device_map="cuda:0",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
    return tok, model


def _publish(model, tok, out: Path, receipt: dict) -> None:
    staging = out / "staging"
    model.save_pretrained(str(staging))
    tok.save_pretrained(str(staging))
    for item in staging.iterdir():
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, out / item.name)
    (out / "PUBLISHED.json").write_text(json.dumps({"published_at": int(time.time())}))
    (out / "pg_receipt.json").write_text(json.dumps(receipt, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--init-adapter", type=Path)
    ap.add_argument("--fresh", action="store_true", help="write a new zero-effect LoRA and stop")
    ap.add_argument("--samples", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--groups-per-step", type=int, default=8)
    ap.add_argument("--lora-r", type=int, default=8)
    ap.add_argument("--lora-alpha", type=int, default=16)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    a = ap.parse_args(argv)
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out} is not empty: adapters are never overwritten")
    a.out.mkdir(parents=True, exist_ok=True)
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    tok, model = _load(a.base)
    if a.fresh:
        model = get_peft_model(model, LoraConfig(r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=0.0, bias="none",
                                                 task_type="CAUSAL_LM", target_modules=TARGETS))
        _publish(model, tok, a.out, {"fresh": True, "r": a.lora_r, "alpha": a.lora_alpha})
        return 0
    if a.init_adapter is None or a.samples is None:
        raise SystemExit("--init-adapter and --samples are required for an update")
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = PeftModel.from_pretrained(model, str(a.init_adapter), is_trainable=True)
    model.train()
    model.config.use_cache = False
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
    rows = [json.loads(line) for line in a.samples.read_text().splitlines() if line.strip()]
    scored, flat = advantages(rows)
    by_group = collections.defaultdict(list)
    for sample, adv in scored:
        by_group[sample["group"]].append((sample, adv))
    groups = list(by_group.values())
    started, steps, skipped_long = time.time(), 0, 0
    for i in range(0, len(groups), a.groups_per_step):
        chunk = [x for g in groups[i:i + a.groups_per_step] for x in g]
        optimizer.zero_grad(set_to_none=True)
        n = 0
        for sample, adv in chunk:
            # A tool conversation trains its final turn on the conversation it was sampled after.
            prompt_ids = tok.apply_chat_template(sample.get("messages") or [{"role": "user", "content": sample["prompt"]}],
                                                 add_generation_prompt=True, tokenize=True)
            seq = tok(sample["completion"], add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
            if len(prompt_ids) + len(seq) > a.max_seq_len:
                skipped_long += 1
                continue
            full = torch.tensor([prompt_ids + seq], device="cuda:0")
            positions = torch.arange(len(prompt_ids) - 1, len(prompt_ids) + len(seq) - 1, device="cuda:0")
            logits = model(input_ids=full, logits_to_keep=positions).logits[0].float()
            logp = torch.log_softmax(logits, -1).gather(1, full[0, len(prompt_ids):, None]).squeeze(1)
            loss = -adv * logp.sum() / len(seq) / len(chunk)
            loss.backward()
            n += 1
        if n:
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            steps += 1
    rewards = [r["reward"] for r in rows]
    receipt = {"init_adapter": str(a.init_adapter), "samples": len(rows), "groups": len(by_group) + flat,
               "flat_groups": flat, "optimizer_steps": steps, "skipped_too_long": skipped_long,
               "mean_reward": round(sum(rewards) / max(1, len(rewards)), 4), "lr": a.lr,
               "seconds": round(time.time() - started, 1),
               "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3)}
    _publish(model, tok, a.out, receipt)
    print(json.dumps(receipt, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
