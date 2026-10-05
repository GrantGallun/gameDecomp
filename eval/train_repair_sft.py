"""Source-repair SFT: train the model to turn compiler feedback into a better candidate.

WHAT THIS IS NOT
----------------
It is not `eval/train_policy.py`. That module trains a preference over JSON technique labels
such as `{"kind":"redirect","target":"layout"}` and evaluates preference likelihood; a gain
there would say nothing about producing C that compiles to the target's bytes. This module
trains the task the experiment actually needs: given the target assembly, a candidate C and
that candidate's real compiler outcome, emit C that the oracle scores higher.

THE FOUR THINGS THAT MAKE THE RUN TRUSTWORTHY
---------------------------------------------
1. COMPLETION-ONLY LOSS. The prompt is masked to -100 and only the target C contributes.
   Training on the prompt teaches the model to reproduce assembly.
2. A DETERMINISTIC, RECORDED INPUT. Every example goes through `eval.repair_prompts`, and
   the tokenized length of every example is stored. Truncation is handled explicitly: an
   example too long for the context is DROPPED AND COUNTED, never silently cut, because a
   cut completion trains the model to stop mid-function.
3. FUNCTION-LEVEL SPLITS ONLY. `TRAINING.md` is explicit that a function's attempts never
   straddle train and eval. The split comes from the dataset's own manifest, not from a
   fresh random draw here.
4. THE ADAPTER IS CHECKPOINTED AND RELOADED IN A FRESH PROCESS. An adapter that only exists
   in the training process is not a deliverable, and a silent failure to load it would make
   M1 identical to M0 and every comparison a null result by construction.

The known-degenerate smoke adapter at `models/adapters/smoke` is NOT used here and must not
be, for any purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TrainConfig:
    base: Path
    out: Path
    dataset: Path
    repo: Path | None = None          # sbk1 checkout; required for --include-headers
    dev_dataset: Path | None = None
    include_headers: bool = False
    max_seq_len: int = 4096
    epochs: float = 1.0
    lr: float = 1e-4
    batch_size: int = 1
    grad_accum: int = 8
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    seed: int = 20260920
    limit: int = 0
    max_steps: int = -1
    save_steps: int = 0
    logging_steps: int = 5
    require_provenance: tuple[str, ...] = ()
    max_score_delta: float = 0.0
    split: str | None = "train"
    load_in_4bit: bool = False
    device: str = "cuda:0"
    keep_longest: int = 0
    cap_keep_fraction: float = 1.0
    dry_run: bool = False


def load_examples(cfg: TrainConfig) -> tuple[list, list[dict]]:
    """Read the dataset and build Examples, returning (examples, dropped-with-reasons)."""
    from eval.repair_prompts import example_from_record, load_records

    records = load_records(cfg.dataset)
    records = [r for r in records if (cfg.split is None or r.get("split") == cfg.split)]
    if cfg.limit:
        records = records[:cfg.limit]
    if cfg.include_headers and cfg.repo is None:
        raise SystemExit("--include-headers needs --repo (the sbk1 checkout)")
    examples, dropped = [], []
    for index, record in enumerate(records):
        if cfg.require_provenance and record.get("provenance") not in cfg.require_provenance:
            dropped.append({"index": index, "id": record.get("id"),
                            "reason": f"provenance {record.get('provenance')!r} not selected"})
            continue
        delta = (record.get("meta") or {}).get("score_delta")
        if cfg.max_score_delta and delta is not None and delta > cfg.max_score_delta:
            # A reconstructed pair whose "parent" is tens of points worse is not a refinement
            # step. Training on it teaches "throw the candidate away and write something
            # unrelated", which is the opposite of the repair behaviour being tested. The
            # dataset audit measured 21 of 200 such records.
            dropped.append({"index": index, "id": record.get("id"),
                            "reason": f"score_delta {delta} exceeds {cfg.max_score_delta}"})
            continue
        example = example_from_record(record, repo=cfg.repo,
                                      include_headers=cfg.include_headers)
        if example is None:
            missing = []
            if not ((record.get("input") or {}).get("target_asm")):
                missing.append("target_asm")
            if not ((record.get("target") or {}).get("source_c")):
                missing.append("target.source_c")
            dropped.append({"index": index, "id": record.get("id"),
                            "reason": "missing " + ",".join(missing or ["unknown"])})
            continue
        examples.append(example)
    return examples, dropped


def _ids_of(encoded) -> list[int]:
    """Token ids from whatever `apply_chat_template` / the tokenizer returned.

    transformers 5 returns a `BatchEncoding` for `apply_chat_template(..., tokenize=True)`,
    NOT a list. `len()` of it is the number of KEYS -- 2 -- so a trainer that assumed a list
    would have computed a 2-token prompt boundary for every example and trained on the whole
    sequence with the mask in the wrong place, or (with a truncation check) dropped nothing
    and learned nothing. Caught by a length assertion below rather than by a loss curve.
    """
    if isinstance(encoded, dict):
        return list(encoded["input_ids"])
    if hasattr(encoded, "input_ids"):
        ids = encoded.input_ids
        return list(ids[0] if ids and isinstance(ids[0], (list, tuple)) else ids)
    return list(encoded)


def assert_tokenizer_usable(tokenizer, probe: str = "x") -> None:
    """Fail loudly if the tokenizer cannot express a chat turn we can mask.

    A tokenizer that returns fewer ids than characters cannot be rendering the template, and
    every downstream number would be silently wrong.
    """
    ids = _ids_of(tokenizer.apply_chat_template(
        [{"role": "user", "content": probe}], tokenize=True, add_generation_prompt=True))
    if len(ids) < 2:
        raise SystemExit(
            f"tokenizer produced {len(ids)} tokens for a chat turn; the chat template is not "
            f"being applied. tokenizer={type(tokenizer).__name__}")
    if getattr(tokenizer, "pad_token_id", None) is None and \
            getattr(tokenizer, "eos_token_id", None) is None:
        raise SystemExit("tokenizer has neither a pad nor an eos token; padding is undefined")


def collate(tokenizer, max_seq_len: int):
    """Completion-only collator: prompt tokens are masked, completion tokens are not.

    Built by hand rather than by a library flag because the masking rule IS the experiment.
    `labels[i] = -100` for every position at or before the prompt boundary, and for every
    pad position.
    """
    import torch

    def collate_fn(batch):
        input_ids, labels, attention = [], [], []
        for ids, boundary in batch:
            lab = list(ids)
            for position in range(min(boundary, len(lab))):
                lab[position] = -100
            input_ids.append(ids)
            labels.append(lab)
            attention.append([1] * len(ids))
        width = max(len(row) for row in input_ids)
        pad = tokenizer.pad_token_id
        if pad is None:
            pad = tokenizer.eos_token_id
        for row in input_ids:
            row.extend([pad] * (width - len(row)))
        for row in labels:
            row.extend([-100] * (width - len(row)))
        for row in attention:
            row.extend([0] * (width - len(row)))
        return {"input_ids": torch.tensor(input_ids, dtype=torch.long),
                "labels": torch.tensor(labels, dtype=torch.long),
                "attention_mask": torch.tensor(attention, dtype=torch.long)}
    return collate_fn


def tokenize_examples(tokenizer, examples, max_seq_len: int, *, report_every: int = 50):
    """Tokenize with the chat template and record every length.

    Over-long examples are DROPPED, not truncated, and the count is returned so the report
    can state it. Silently truncating a completion is how a model learns to stop in the
    middle of a function.
    """
    from eval.repair_prompts import ASSISTANT_PREFILL

    kept, dropped, lengths = [], [], []
    for index, example in enumerate(examples):
        ids, boundary = _render(tokenizer, example)
        lengths.append({"id": example.record_id, "tokens": len(ids), "prompt_tokens": boundary,
                        "kind": example.kind, "provenance": example.provenance,
                        "split": example.split, "function": example.function})
        if len(ids) > max_seq_len:
            dropped.append({"id": example.record_id, "tokens": len(ids),
                            "reason": f"exceeds max_seq_len {max_seq_len}"})
            continue
        kept.append((ids, boundary))
        if report_every and index and index % report_every == 0:
            print(f"  tokenized {index}/{len(examples)}", flush=True)
    return kept, dropped, lengths


def _render(tokenizer, example):
    from eval.repair_prompts import ASSISTANT_PREFILL

    messages = [{"role": "user", "content": example.prompt}]
    prefix_ids = _ids_of(tokenizer.apply_chat_template(messages, tokenize=True,
                                                       add_generation_prompt=True))
    completion_ids = _ids_of(tokenizer(ASSISTANT_PREFILL + example.completion,
                                       add_special_tokens=False))
    eos = tokenizer.eos_token_id
    if eos is not None:
        completion_ids = completion_ids + [eos]
    return list(prefix_ids) + list(completion_ids), len(prefix_ids)


def train(cfg: TrainConfig) -> dict:
    # Resource caps FIRST, before any CUDA allocation: on a shared box an uncapped run takes
    # the whole card and every core, and the machine stops being usable for anything else.
    # `eval.resource_limits` holds the knobs; the defaults leave half the card and half the
    # cores for whoever else is using the computer.
    from eval import resource_limits
    applied_limits = resource_limits.apply()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.time()
    cfg.out.mkdir(parents=True, exist_ok=True)
    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device visible; refusing to start a training run")
    torch.manual_seed(cfg.seed)
    torch.cuda.manual_seed_all(cfg.seed)
    print(f"resource limits: {json.dumps(applied_limits.as_dict())}", flush=True)

    examples, dropped_records = load_examples(cfg)
    if not examples:
        raise SystemExit(f"no usable examples in {cfg.dataset}: {json.dumps(dropped_records[:5])}")
    print(f"examples: {len(examples)} (dropped at load: {len(dropped_records)})", flush=True)

    tokenizer = AutoTokenizer.from_pretrained(str(cfg.base), trust_remote_code=False)
    assert_tokenizer_usable(tokenizer)
    kept, dropped_long, lengths = tokenize_examples(tokenizer, examples, cfg.max_seq_len)
    if cfg.keep_longest:
        before = len(kept)
        survivors = [(ids, boundary) for ids, boundary in kept if len(ids) <= cfg.keep_longest]
        for ids, _boundary in kept:
            if len(ids) > cfg.keep_longest:
                dropped_long.append({"id": None, "tokens": len(ids),
                                     "reason": f"exceeds keep-longest {cfg.keep_longest}"})
        kept = survivors
        print(f"keep-longest {cfg.keep_longest}: {before} -> {len(kept)}", flush=True)
    if cfg.cap_keep_fraction < 1.0 and kept:
        # Shortest-first. A memory cap is a property of the machine, not of the task, so the
        # cheapest way to respect it is to train on the examples that fit rather than to cut
        # completions in half. The dropped count is reported.
        kept.sort(key=lambda pair: len(pair[0]))
        want = max(1, int(len(kept) * cfg.cap_keep_fraction))
        for ids, _boundary in kept[want:]:
            dropped_long.append({"id": None, "tokens": len(ids),
                                 "reason": f"over cap-keep-fraction {cfg.cap_keep_fraction}"})
        kept = kept[:want]
    print(f"kept {len(kept)}, dropped for length {len(dropped_long)}", flush=True)
    if not kept:
        raise SystemExit("every example exceeded max_seq_len; nothing to train on")

    token_report = {
        "count": len(lengths),
        "max": max(row["tokens"] for row in lengths),
        "mean": round(sum(row["tokens"] for row in lengths) / len(lengths), 1),
        "prompt_tokens_mean": round(sum(row["prompt_tokens"] for row in lengths) / len(lengths), 1),
        "dropped_for_length": dropped_long,
        "dropped_at_load": dropped_records,
        "per_example": lengths,
    }
    (cfg.out / "token_report.json").write_text(json.dumps(token_report, indent=2) + "\n",
                                               encoding="utf-8")

    quantisation = None
    if cfg.load_in_4bit:
        # QLoRA. A 7B checkpoint in bf16 is ~15 GB, which does not fit beside anything else on
        # a 16 GB card -- and the failure is a bare CUDA OOM during backward, not a clear
        # "this machine cannot do that". 4-bit weights make training possible while another
        # job holds memory, at the cost of quantised weights. That cost is reported, never
        # hidden: it is written into the train report so the adapter's provenance says what
        # precision it was trained at.
        from transformers import BitsAndBytesConfig
        quantisation = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        str(cfg.base), dtype=torch.bfloat16,
        quantization_config=quantisation, device_map=(cfg.device if quantisation else None),
        trust_remote_code=False)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    lora = LoraConfig(r=cfg.lora_r, lora_alpha=cfg.lora_alpha,
                      lora_dropout=cfg.lora_dropout, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    # `get_peft_model` rebuilds module wrappers, so the device placement has to be
    # re-asserted AFTER wrapping. Without this the embedding table stays on CPU while the
    # batch is moved to CUDA, and the failure surfaces as an index_select device error deep
    # inside the model rather than as a placement mistake.
    device = torch.device(cfg.device)
    # A quantised model is already placed by `device_map`; moving it again is a no-op at best.
    if not cfg.load_in_4bit:
        model.to(device)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"trainable {trainable:,} / {total:,} ({100*trainable/total:.3f}%)", flush=True)
    model.print_trainable_parameters()

    if cfg.dry_run:
        report = {"dry_run": True, "examples": len(kept), "trainable": trainable,
                  "total": total, "tokens": token_report,
                  "seconds": round(time.time() - started, 1)}
        (cfg.out / "dry_run.json").write_text(json.dumps(report, indent=2) + "\n",
                                              encoding="utf-8")
        return report

    from torch.utils.data import DataLoader
    from transformers import get_cosine_schedule_with_warmup

    loader = DataLoader(kept, batch_size=cfg.batch_size, shuffle=True,
                        collate_fn=collate(tokenizer, cfg.max_seq_len),
                        generator=torch.Generator().manual_seed(cfg.seed))
    optimiser = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=cfg.lr, weight_decay=0.0)
    steps_per_epoch = max(1, len(loader) // cfg.grad_accum)
    total_steps = (cfg.max_steps if cfg.max_steps > 0
                   else max(1, int(steps_per_epoch * cfg.epochs)))
    scheduler = get_cosine_schedule_with_warmup(
        optimiser, num_warmup_steps=max(1, total_steps // 20),
        num_training_steps=total_steps)

    model.train()
    assert next(model.parameters()).is_cuda, (
        "model parameters are not on CUDA after wrapping; every step would fail on a device "
        "mismatch rather than training")
    losses: list[dict] = []
    step = 0
    running = 0.0
    micro = 0
    stop = False
    paused = False
    for epoch in range(max(1, int(cfg.epochs) + (1 if cfg.epochs % 1 else 0))):
        if stop or paused:
            break
        for batch in loader:
            if resource_limits.paused():
                # Stop between steps, not mid-step. The adapter is saved below either way, so
                # an interrupted run still produces a usable artifact and says it was cut short.
                paused = True
                print(json.dumps({"paused": resource_limits.wait_note()}), flush=True)
                break
            batch = {k: v.cuda() for k, v in batch.items()}
            out = model(**batch)
            loss = out.loss / cfg.grad_accum
            loss.backward()
            running += float(out.loss.detach())
            micro += 1
            if micro % cfg.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(
                    [p for p in model.parameters() if p.requires_grad], 1.0)
                optimiser.step()
                scheduler.step()
                optimiser.zero_grad(set_to_none=True)
                step += 1
                if step % cfg.logging_steps == 0 or step == 1:
                    row = {"step": step, "loss": round(running / cfg.grad_accum, 5),
                           "lr": scheduler.get_last_lr()[0], "epoch": epoch,
                           "elapsed_s": round(time.time() - started, 1)}
                    losses.append(row)
                    print(json.dumps(row), flush=True)
                    running = 0.0
                if cfg.save_steps and step % cfg.save_steps == 0:
                    model.save_pretrained(str(cfg.out / f"checkpoint-{step}"))
                if 0 < cfg.max_steps <= step:
                    stop = True
                    break
    if micro % cfg.grad_accum:
        optimiser.step()
        scheduler.step()

    model.save_pretrained(str(cfg.out))
    tokenizer.save_pretrained(str(cfg.out))
    report = {
        "base": str(cfg.base), "out": str(cfg.out), "dataset": str(cfg.dataset),
        "include_headers": cfg.include_headers, "load_in_4bit": cfg.load_in_4bit,
        "device": cfg.device,
        "examples_used": len(kept), "examples_dropped_for_length": len(dropped_long),
        "examples_dropped_at_load": len(dropped_records),
        "trainable_parameters": trainable, "total_parameters": total,
        "lora": {"r": cfg.lora_r, "alpha": cfg.lora_alpha, "dropout": cfg.lora_dropout},
        "hyperparameters": {"lr": cfg.lr, "epochs": cfg.epochs, "batch_size": cfg.batch_size,
                            "grad_accum": cfg.grad_accum, "max_seq_len": cfg.max_seq_len,
                            "seed": cfg.seed, "steps": step},
        "losses": losses,
        "first_loss": losses[0]["loss"] if losses else None,
        "last_loss": losses[-1]["loss"] if losses else None,
        "gpu_seconds": round(time.time() - started, 1),
        "resource_limits": applied_limits.as_dict(),
        "paused_early": paused,
        "torch": torch.__version__,
    }
    (cfg.out / "train_report.json").write_text(json.dumps(report, indent=2) + "\n",
                                               encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--include-headers", action="store_true")
    ap.add_argument("--max-seq-len", type=int, default=6144,
                    help="examples longer than this are DROPPED and counted, never truncated")
    ap.add_argument("--keep-longest", type=int, default=0,
                    help="drop examples longer than this many tokens (0 = only max-seq-len)")
    ap.add_argument("--cap-keep-fraction", type=float, default=1.0,
                    help="keep only this fraction of the shortest examples; 1.0 keeps all. "
                         "Used to fit a memory cap on a shared card.")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--save-steps", type=int, default=0)
    ap.add_argument("--split", default="train",
                    help="dataset split to train on; 'any' trains on every split "
                         "(only for a plumbing canary, never for the experiment)")
    ap.add_argument("--max-score-delta", type=float, default=0.0,
                    help="drop records whose improvement exceeds this; 0 disables")
    ap.add_argument("--logging-steps", type=int, default=5)
    ap.add_argument("--load-in-4bit", action="store_true",
                    help="QLoRA: 4-bit base weights, needed when the card is shared")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = TrainConfig(base=args.base, out=args.out, dataset=args.dataset, repo=args.repo,
                      include_headers=args.include_headers, max_seq_len=args.max_seq_len,
                      epochs=args.epochs, lr=args.lr, batch_size=args.batch_size,
                      grad_accum=args.grad_accum, lora_r=args.lora_r,
                      lora_alpha=args.lora_alpha, seed=args.seed, limit=args.limit,
                      max_steps=args.max_steps, save_steps=args.save_steps,
                      max_score_delta=args.max_score_delta,
                      logging_steps=args.logging_steps,
                      load_in_4bit=args.load_in_4bit, device=args.device,
                      keep_longest=args.keep_longest,
                      cap_keep_fraction=args.cap_keep_fraction,
                      split=(None if args.split == "any" else args.split),
                      dry_run=args.dry_run)
    report = train(cfg)
    print(json.dumps({k: v for k, v in report.items() if k != "losses"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
