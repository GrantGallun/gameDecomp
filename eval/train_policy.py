"""Close the loop: turn research-policy preferences into a weight update, then measure it.

The claim being tested is narrow and falsifiable, which is the point:

    After training on decisions from functions M0 has never seen, does M1 prefer the higher-value
    research technique on HELD-OUT functions more often than M0 did?

If it does not, the loop is not closed and no amount of generation compute will close it.

Three choices worth stating, because each is a place this could quietly become a lie:

1. HOLD OUT BY FUNCTION. Two states from one function share a search tree and often the same
   alternatives, so a row-wise split puts half a tree in training and measures recall of the other
   half. `split_by_function` is the same rule `eval/clean_set.py` applies between translation units.

2. THE REFERENCE IS THE FROZEN BASE. This is LoRA, so DPO's reference model is the very same weights
   with the adapter disabled -- no second 7B copy in VRAM, and no chance of the reference drifting.
   `model.disable_adapter()` makes that exact rather than approximate.

3. HAND-WRITTEN DPO. `trl` and `transformers` are on 1.x/5.x here and both renamed their entry
   points recently. The objective is fifteen lines; owning it is cheaper than tracking the churn, and
   it makes the beta, the masking and the normalisation visible instead of configurable.

Usage:
    python3 -m eval.train_policy --pairs <pairs.jsonl> --model <local dir> --out <adapter dir>
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from eval import distill_policy as dp

SYSTEM = ("You choose the next research action for a decompilation attempt. "
          "Reply with only a JSON object containing kind and target.")

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def completion_ids(tokenizer, prompt: str, completion: str) -> tuple[list[int], list[int]]:
    """(ids, completion mask) built by concatenation, never by re-tokenising the joined string.

    Tokenising `prefix + completion` and then trying to find where the prefix ended is a guess: BPE
    merges across the boundary, so the boundary can move. Concatenating the two encodings makes the
    mask exact.
    """
    prefix = tokenizer.apply_chat_template(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        tokenize=False, add_generation_prompt=True)
    prompt_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
    body = tokenizer(completion, add_special_tokens=False)["input_ids"]
    body = body + [tokenizer.eos_token_id]
    return prompt_ids + body, [0] * len(prompt_ids) + [1] * len(body)


def collate(tokenizer, rows: list[tuple[list[int], list[int]]], pad_id: int):
    import torch
    width = max(len(ids) for ids, _ in rows)
    ids = torch.full((len(rows), width), pad_id, dtype=torch.long)
    mask = torch.zeros((len(rows), width), dtype=torch.long)
    for index, (row_ids, row_mask) in enumerate(rows):
        ids[index, :len(row_ids)] = torch.tensor(row_ids)
        mask[index, :len(row_mask)] = torch.tensor(row_mask)
    attention = (ids != pad_id).long()
    attention[:, 0] = 1
    return ids, attention, mask


def sequence_logprob(model, ids, attention, mask):
    """Sum of the completion tokens' log-probabilities, per row."""
    import torch.nn.functional as F
    logits = model(input_ids=ids, attention_mask=attention).logits[:, :-1].float()
    targets = ids[:, 1:]
    picked = F.log_softmax(logits, dim=-1).gather(-1, targets.unsqueeze(-1)).squeeze(-1)
    weights = mask[:, 1:].float()
    return (picked * weights).sum(dim=-1)


def load_model(path: str, four_bit: bool = True):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    # `torch_dtype` was renamed to `dtype` in transformers 5; pass whichever this one accepts.
    import inspect
    signature = inspect.signature(AutoModelForCausalLM.from_pretrained)
    key = "dtype" if "dtype" in signature.parameters else "torch_dtype"
    kwargs = {key: torch.bfloat16, "device_map": {"": 0}, "trust_remote_code": True}
    if four_bit:
        from transformers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    return AutoModelForCausalLM.from_pretrained(path, **kwargs), tokenizer


def attach_lora(model, r: int = 16, alpha: int = 32, dropout: float = 0.05):
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    model = prepare_model_for_kbit_training(model)
    config = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, bias="none",
                        task_type="CAUSAL_LM", target_modules=TARGET_MODULES)
    return get_peft_model(model, config)


def preference_accuracy(model, tokenizer, examples: list[dict], batch: int = 4,
                        limit: int | None = None) -> dict:
    """How often the model prefers the higher-value technique. Chance is 0.5."""
    import torch
    rows = examples[:limit] if limit else examples
    wins, gaps = 0, []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(rows), batch):
            chunk = rows[start:start + batch]
            chosen = [completion_ids(tokenizer, e["prompt"], e["chosen"]) for e in chunk]
            rejected = [completion_ids(tokenizer, e["prompt"], e["rejected"]) for e in chunk]
            ids_c, att_c, msk_c = collate(tokenizer, chosen, tokenizer.pad_token_id)
            ids_r, att_r, msk_r = collate(tokenizer, rejected, tokenizer.pad_token_id)
            device = next(model.parameters()).device
            lp_c = sequence_logprob(model, ids_c.to(device), att_c.to(device), msk_c.to(device))
            lp_r = sequence_logprob(model, ids_r.to(device), att_r.to(device), msk_r.to(device))
            delta = (lp_c - lp_r).tolist()
            wins += sum(1 for d in delta if d > 0)
            gaps.extend(delta)
    model.train()
    return {"examples": len(rows), "accuracy": round(wins / max(1, len(rows)), 4),
            "mean_margin": round(sum(gaps) / max(1, len(gaps)), 4)}


def train(model, tokenizer, train_rows: list[dict], *, beta: float = 0.1, epochs: int = 3,
          batch: int = 2, lr: float = 5e-5, accumulate: int = 4, max_len: int = 640,
          log_every: int = 25, max_steps: int | None = None) -> dict:
    """DPO against the adapter-disabled base, which is exactly the frozen reference for LoRA."""
    import torch
    import torch.nn.functional as F

    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=lr, betas=(0.9, 0.999), weight_decay=0.0)
    device = next(model.parameters()).device
    pad = tokenizer.pad_token_id
    history, step, started = [], 0, time.time()
    model.train()
    for epoch in range(epochs):
        order = list(range(len(train_rows)))
        random.Random(1000 + epoch).shuffle(order)
        for start in range(0, len(order), batch * accumulate):
            group = [train_rows[i] for i in order[start:start + batch * accumulate]]
            if not group:
                continue
            total, pieces = 0.0, 0
            for chunk_start in range(0, len(group), batch):
                chunk = group[chunk_start:chunk_start + batch]
                chosen = [completion_ids(tokenizer, e["prompt"], e["chosen"]) for e in chunk]
                rejected = [completion_ids(tokenizer, e["prompt"], e["rejected"]) for e in chunk]
                if max(len(i) for i, _ in chosen + rejected) > max_len:
                    continue
                ids_c, att_c, msk_c = (t.to(device) for t in collate(tokenizer, chosen, pad))
                ids_r, att_r, msk_r = (t.to(device) for t in collate(tokenizer, rejected, pad))
                with torch.no_grad(), model.disable_adapter():
                    ref_c = sequence_logprob(model, ids_c, att_c, msk_c)
                    ref_r = sequence_logprob(model, ids_r, att_r, msk_r)
                pol_c = sequence_logprob(model, ids_c, att_c, msk_c)
                pol_r = sequence_logprob(model, ids_r, att_r, msk_r)
                margin = (pol_c - pol_r) - (ref_c - ref_r)
                loss = -F.logsigmoid(beta * margin).mean() / accumulate
                loss.backward()
                total += float(loss) * accumulate
                pieces += 1
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % log_every == 0:
                history.append({"step": step, "epoch": epoch, "loss": round(total / max(1, pieces), 4),
                                "seconds": round(time.time() - started, 1)})
                print(json.dumps(history[-1]), flush=True)
            if max_steps and step >= max_steps:
                return {"steps": step, "history": history, "seconds": round(time.time() - started, 1)}
    return {"steps": step, "history": history, "seconds": round(time.time() - started, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--model", required=True, help="local directory of the base checkpoint")
    ap.add_argument("--out", type=Path, required=True, help="where to write the LoRA adapter")
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=20260916)
    ap.add_argument("--min-gap", type=float, default=2.0)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--smoke", action="store_true", help="20 steps, tiny sample, for a wiring check")
    args = ap.parse_args(argv)

    raw = [json.loads(line) for line in args.pairs.read_text(encoding="utf-8").splitlines() if line]
    examples = dp.filter_examples(dp.build_examples(raw), args.min_gap)
    if not examples:
        raise SystemExit(f"no usable preference pairs from {len(raw)} raw pairs at gap {args.min_gap}")
    train_rows, test_rows = dp.split_by_function(examples, args.holdout, args.seed)
    print(json.dumps({"raw_pairs": len(raw), "technique_pairs": len(examples),
                      "train": len(train_rows), "test": len(test_rows),
                      "train_functions": len({e['func'] for e in train_rows}),
                      "test_functions": len({e['func'] for e in test_rows})}))

    model, tokenizer = load_model(args.model)
    model = attach_lora(model)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(json.dumps({"trainable_parameters": trainable}))

    before = preference_accuracy(model, tokenizer, test_rows)
    print(json.dumps({"M0_heldout": before}), flush=True)

    epochs, max_steps = (1, 20) if args.smoke else (args.epochs, args.max_steps)
    rows = train_rows[:64] if args.smoke else train_rows
    run = train(model, tokenizer, rows, epochs=epochs, max_steps=max_steps)
    after = preference_accuracy(model, tokenizer, test_rows)
    print(json.dumps({"M1_heldout": after}), flush=True)

    model.save_pretrained(str(args.out))
    tokenizer.save_pretrained(str(args.out))
    report = {"base_model": args.model, "adapter": str(args.out), "beta": 0.1,
              "min_gap": args.min_gap, "seed": args.seed,
              "train": len(train_rows), "test": len(test_rows),
              "test_functions": sorted({e["func"] for e in test_rows}),
              "M0": before, "M1": after, "training": run,
              "delta_accuracy": round(after["accuracy"] - before["accuracy"], 4)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"delta_accuracy": report["delta_accuracy"],
                      "M0": before["accuracy"], "M1": after["accuracy"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
