"""The source-repair LoRA trainer for compiler-verified post-training.

THE OBJECTIVE
-------------
    INPUT  = target assembly + parent candidate C + the compiler's own feedback
    OUTPUT = a verified better child C

This is NOT an action-selection or preference trainer. `eval/train_policy.py` learns a
preference over JSON technique labels and a gain there says nothing about producing C that
compiles to the target's bytes; the handoff says so explicitly and this module is the
alternative it asks for.

WHAT MAKES A RUN TRUSTWORTHY
----------------------------
1. COMPLETION-ONLY LOSS. Prompt tokens are masked to -100. Training on the prompt teaches the
   model to reproduce assembly.
2. THE LABEL IS COMPILER-VERIFIED. A task is used only when its builder compiled the child and
   matched its `.text` section against the target's (`child.exact`). An unverified label is a
   guess and is skipped, and the skip count is reported.
3. INTERRUPTION CANNOT PUBLISH AN ADAPTER. Weights are written to a staging directory and only
   promoted after the loop finishes cleanly AND the receipt is written. A killed process leaves
   staging behind and no published adapter, and the receipt records why.
4. BOUNDED IN BOTH DIMENSIONS. `--max-steps` and `--max-seconds` both bind, and whichever binds
   is named in the receipt. A deadline stop is a clean stop, not a failure.
5. THE RESOURCE CAP IS RECORDED. Peak GPU memory is logged so a later comparison cannot mistake
   a memory limit for a capability limit.

NO AUTOMATIC RETRIES, NO BUDGET RESETS. Re-running with the same `--out` refuses while a
published adapter exists; a staged-but-unpublished adapter is reported and left alone for a
human to accept or discard. That is what "an interrupted run cannot publish an adapter" means
operationally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUBLISHED_MARKER = "PUBLISHED.json"
STAGING_DIR = "staging"
RECEIPT_NAME = "training_receipt.json"


class Interrupted(RuntimeError):
    """Raised by the signal handler's flag so the loop can stop between steps."""


@dataclass
class TrainConfig:
    base: Path
    tasks: Path
    out: Path
    split: str = "train"
    max_seq_len: int = 3072
    max_steps: int = 40
    max_seconds: float = 900.0
    max_examples: int = 0
    learning_rate: float = 2e-4
    batch_size: int = 1
    grad_accum: int = 4
    block_size: int = 8
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    seed: int = 20260920
    load_in_4bit: bool = True
    logging_steps: int = 1
    completion_logits: bool = False
    # Continue training an existing LoRA (its r/alpha/targets) instead of starting a fresh one. None = fresh, as before.
    init_adapter: Path | None = None
    # Divides the per-example weight so the AVERAGE weight over the dataset is 1.0. Set to the number
    # of kept examples when balanced weights are in use; left at 1.0 for the single-child baseline,
    # which makes the weighted loss identical to the unweighted one and keeps the arms comparable.
    weight_scale: float = 1.0
    # Multiple verified children per repair state, and the balanced weighting over them. Both default
    # OFF, so the single-child configuration stays available as the baseline arm it is compared to.
    variants: Path | None = None
    balanced_weights: bool = False
    extra: dict = field(default_factory=dict)


def sha_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def dir_digest(path: Path, *, skip_over: int = 64 * 1024 * 1024) -> dict:
    """A digest over a checkpoint directory's small files, plus what was skipped.

    Large shards are hashed by name, size and a streamed digest rather than skipped silently, so
    two different checkpoints cannot collide by both having "one big file".
    """
    root = Path(path)
    if not root.exists():
        return {"path": str(root), "exists": False}
    digest = hashlib.sha256()
    files = 0
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        files += 1
        digest.update(item.name.encode("utf-8"))
        size = item.stat().st_size
        digest.update(str(size).encode())
        if size <= skip_over:
            digest.update(item.read_bytes())
        else:
            with item.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    digest.update(chunk)
    out = {"path": str(root), "exists": True, "files": files,
           "sha256": digest.hexdigest()}
    config = root / "config.json"
    if config.exists():
        try:
            payload = json.loads(config.read_text())
            out["architectures"] = payload.get("architectures")
            out["torch_dtype"] = payload.get("torch_dtype")
        except Exception:
            pass
    adapter = root / "adapter_config.json"
    if adapter.exists():
        try:
            payload = json.loads(adapter.read_text())
            out["peft"] = {"r": payload.get("r"), "lora_alpha": payload.get("lora_alpha"),
                           "target_modules": payload.get("target_modules")}
        except Exception:
            pass
    return out


def published_adapter(out: Path) -> dict | None:
    marker = Path(out) / PUBLISHED_MARKER
    if not marker.exists():
        return None
    try:
        return json.loads(marker.read_text())
    except (OSError, ValueError):
        return {"path": str(marker), "unreadable": True}


# --- tokenisation -------------------------------------------------------------

def _ids_of(encoded) -> list[int]:
    """Token ids from whatever the tokenizer returned.

    transformers 5 returns a `BatchEncoding` for `apply_chat_template(..., tokenize=True)`, and
    `len()` of it is the number of KEYS. A trainer that assumed a list would compute a
    two-token prompt boundary for every example and mask the wrong span while the loss still
    fell. Caught by an assertion rather than by a training run.
    """
    if isinstance(encoded, dict):
        return list(encoded["input_ids"])
    if hasattr(encoded, "input_ids"):
        ids = encoded.input_ids
        return list(ids[0] if ids and isinstance(ids[0], (list, tuple)) else ids)
    return list(encoded)


def assert_tokenizer_usable(tokenizer) -> None:
    ids = _ids_of(tokenizer.apply_chat_template(
        [{"role": "user", "content": "x"}], tokenize=True, add_generation_prompt=True))
    if len(ids) < 2:
        raise SystemExit(
            f"tokenizer produced {len(ids)} tokens for a chat turn; the chat template is not "
            f"being applied, and a completion-only mask cannot be computed")
    if getattr(tokenizer, "pad_token_id", None) is None and \
            getattr(tokenizer, "eos_token_id", None) is None:
        raise SystemExit("tokenizer has neither pad nor eos; padding is undefined")


def render(tokenizer, example) -> tuple[list[int], int]:
    """(ids, prompt boundary). The boundary comes from the SAME call that produced the ids."""
    from eval.repair_prompts import ASSISTANT_PREFILL, LOGIC_KINDS

    prefix = _ids_of(tokenizer.apply_chat_template(
        [{"role": "user", "content": example.prompt}], tokenize=True,
        add_generation_prompt=True))
    prefill = "" if example.kind in LOGIC_KINDS else ASSISTANT_PREFILL
    completion = _ids_of(tokenizer(prefill + example.completion,
                                   add_special_tokens=False))
    eos = tokenizer.eos_token_id
    if eos is not None:
        completion = completion + [eos]
    full = list(prefix) + list(completion)
    if not 0 < len(prefix) < len(full):
        raise ValueError(f"degenerate rendering for {example.record_id!r}: "
                         f"{len(prefix)} prompt tokens of {len(full)}")
    return full, len(prefix)


def tokenize_examples(tokenizer, examples, max_seq_len: int):
    """Tokenize, and DROP over-long examples rather than truncating them.

    Truncating a completion teaches the model to stop mid-function, and the damage is invisible
    in the loss. The dropped ids and lengths are returned so the receipt can state them.

    Each kept row carries the example's training WEIGHT (default 1.0). Weights come from
    `eval.repair_states.balanced_weights`, which balances functions, then states, then novelty
    classes and splits one unit of weight across them. They are carried through tokenisation
    untouched: tokenisation must not silently reweight anything.
    """
    kept, dropped, lengths = [], [], []
    for example in examples:
        ids, boundary = render(tokenizer, example)
        row = {"id": example.record_id, "tokens": len(ids), "prompt_tokens": boundary,
               "completion_tokens": len(ids) - boundary, "function": example.function}
        lengths.append(row)
        if len(ids) > max_seq_len:
            dropped.append({**row, "reason": f"exceeds max_seq_len {max_seq_len}"})
            continue
        kept.append((ids, boundary, float(getattr(example, "weight", 1.0) or 1.0)))
    return kept, dropped, lengths


def completion_loss(model, batch):
    """Compute the usual shifted completion-token mean without allocating prompt logits.

    Opt-in for models supporting tensor `logits_to_keep` (the pilot's Qwen does).
    The first completion token is predicted by the LAST PROMPT position, and EOS
    remains supervised. Prompt hidden states still participate in attention/backprop.
    """
    import torch
    labels = batch["labels"]
    if labels.shape[0] != 1:
        raise ValueError("selected completion logits require batch_size == 1")
    positions = torch.where(labels[0, 1:] != -100)[0]
    if not positions.numel():
        raise ValueError("no completion labels")
    inputs = {k: v for k, v in batch.items() if k != "labels"}
    logits = model(**inputs, logits_to_keep=positions).logits[0]
    return torch.nn.functional.cross_entropy(logits.float(), labels[0, positions + 1])


def collate(tokenizer):
    """Completion-only collator: prompt and pad positions are -100, completion is not.

    Written by hand because the masking rule IS the objective. `labels[i] = -100` for every
    position at or before the boundary and for every pad position.
    """
    import torch

    def collate_fn(batch):
        input_ids, labels, attention, weights = [], [], [], []
        for ids, boundary, weight in batch:
            lab = list(ids)
            for position in range(min(boundary, len(lab))):
                lab[position] = -100
            input_ids.append(list(ids))
            labels.append(lab)
            attention.append([1] * len(ids))
            weights.append(weight)
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
                "attention_mask": torch.tensor(attention, dtype=torch.long),
                "weights": torch.tensor(weights, dtype=torch.float32)}
    return collate_fn


# --- the run ------------------------------------------------------------------

def train(cfg: TrainConfig) -> dict:
    from eval import resource_limits
    limits = resource_limits.apply()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.time()
    cfg.out = Path(cfg.out)
    cfg.out.mkdir(parents=True, exist_ok=True)
    receipt_path = cfg.out / RECEIPT_NAME
    staging = cfg.out / STAGING_DIR

    # --- publish safety, checked BEFORE any work ---------------------------------
    existing = published_adapter(cfg.out)
    if existing:
        raise SystemExit(
            f"an adapter is already published at {cfg.out} "
            f"({json.dumps(existing)[:200]}). Re-running would overwrite a published result; "
            f"there is no automatic retry and no budget reset. Move it aside deliberately.")
    staged_before = staging.exists()
    if staged_before and any(staging.iterdir()):
        # Left by an interrupted run. Reported, never silently adopted or deleted.
        print(json.dumps({"warning": "staged weights from a previous run are present and will "
                                     "NOT be published; they are not evidence",
                          "staging": str(staging)}), flush=True)

    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device visible; refusing to start a training run")
    torch.manual_seed(cfg.seed)
    torch.cuda.manual_seed_all(cfg.seed)

    interrupted = {"flag": False, "signal": ""}

    def on_signal(signum, _frame):
        interrupted["flag"] = True
        interrupted["signal"] = signal.Signals(signum).name
        print(json.dumps({"interrupted_by": interrupted["signal"],
                          "note": "stopping after the current step; nothing will be published"}),
              flush=True)
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)

    # --- data ---------------------------------------------------------------------
    from eval.repair_prompts import load_task_examples, load_variants
    tasks = [json.loads(line) for line in
             Path(cfg.tasks).read_text(encoding="utf-8").splitlines() if line.strip()]
    # MULTIPLE VERIFIED CHILDREN PER STATE. `load_task_examples` already accepts extra
    # certificate-verified spellings of the same answers; the balanced weighting turns a state with
    # several verified children into one unit of weight split across them, rather than into several
    # units that would outvote a state with one.
    variants = load_variants(cfg.variants) if getattr(cfg, "variants", None) else []
    examples = load_task_examples(tasks, split=cfg.split, variants=variants)
    if cfg.balanced_weights:
        from eval.repair_states import balanced_weights
        weight_map = balanced_weights([t for t in tasks if t.get("split") == cfg.split], variants)
        from eval.repair_archive import novelty_key
        for example in examples:
            classes = weight_map.get(example.record_id) or {}
            # An example's completion is either the recorded child or a verified variant, and the
            # weighting is expressed in novelty keys, so the match is on the source text itself.
            # A zero lookup means the weighting does not know this example; that is a wiring bug and
            # is reported rather than silently trained at full weight.
            example.weight = float(classes.get(novelty_key(example.completion), 0.0))
        unweighted = [e.record_id for e in examples if not e.weight]
        if unweighted:
            raise SystemExit(
                f"{len(unweighted)} example(s) got no weight from the balanced weighting "
                f"(e.g. {unweighted[:3]}). Training them at an implicit 1.0 would silently undo the "
                f"balance; the state/variant join is wrong.")
    from eval.repair_prompts import LOGIC_KINDS
    skipped_unverified = sum(1 for t in tasks
                             if (t.get("split") == cfg.split) and t.get("kind") not in LOGIC_KINDS
                             and not (t.get("child") or {}).get("exact"))
    if cfg.max_examples:
        examples = examples[:cfg.max_examples]
    if not examples:
        raise SystemExit(
            f"no verified {cfg.split} examples in {cfg.tasks}; a task without a compiler-"
            f"verified child cannot be trained on")

    tokenizer = AutoTokenizer.from_pretrained(str(cfg.base))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    assert_tokenizer_usable(tokenizer)
    kept, dropped, lengths = tokenize_examples(tokenizer, examples, cfg.max_seq_len)
    # Average weight 1.0, so the weighted run's gradient scale matches the unweighted baseline's.
    # Without this the weights (which sum to 1.0 across the dataset) would divide every gradient by
    # the example count and the run would look like a much smaller learning rate.
    if cfg.balanced_weights and kept:
        cfg.weight_scale = float(len(kept))
    if not kept:
        raise SystemExit(f"every example exceeded max_seq_len {cfg.max_seq_len}")

    # --- model --------------------------------------------------------------------
    quantisation = None
    if cfg.load_in_4bit:
        from transformers import BitsAndBytesConfig
        quantisation = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        str(cfg.base), dtype=torch.bfloat16, quantization_config=quantisation,
        device_map=("cuda:0" if quantisation else None))
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    base_adapter = cfg.base / "adapter_config.json"
    before_weights = {}
    if cfg.init_adapter is not None:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(cfg.init_adapter), is_trainable=True)
    else:
        model = get_peft_model(model, LoraConfig(
            r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout, bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj",
                            "down_proj"]))
    if not cfg.load_in_4bit:
        model.to("cuda:0")
    model.train()
    # Hash the initial LoRA weights so "the adapter changed" is a measurement, not a claim.
    for name, param in model.named_parameters():
        if param.requires_grad:
            before_weights[name] = hashlib.sha256(
                param.detach().float().cpu().numpy().tobytes()).hexdigest()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())

    from torch.utils.data import DataLoader
    from transformers import get_cosine_schedule_with_warmup

    # SAMPLES ARE SORTED BY LENGTH AND READ IN BLOCKS. A step's activation and logits cost scales
    # with the LONGEST sequence in the batch, so one long example drags every short one up with
    # it. Measured on this card, the memory curve is ~1.9 MB per token against a 5.24 GB resident
    # base, and the dataset spans 1311-2092 tokens: padding a 1311-token example up to 2092 costs
    # 1.5 GB for nothing. Sorting bounds the wasted padding inside a block, and shuffling within
    # the block keeps the order from being a curriculum by construction.
    ordered = sorted(kept, key=lambda pair: len(pair[0]))

    def blocks():
        import random as _random
        rng = _random.Random(cfg.seed)
        out = []
        for start in range(0, len(ordered), cfg.block_size):
            block = ordered[start:start + cfg.block_size]
            rng.shuffle(block)
            out.extend(block)
        return out
    loader = DataLoader(blocks(), batch_size=cfg.batch_size,
                        collate_fn=collate(tokenizer))

    optimiser = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                  lr=cfg.learning_rate, weight_decay=0.0)
    # `max(1, ...)` is load-bearing: with fewer examples than `grad_accum` the integer division
    # gave ZERO, `total_steps` became 1, the loop never reached a `micro % grad_accum == 0`
    # boundary, and the run finished with `steps_run: 0` and no weight touched -- while still
    # reporting a plausible stop reason. Found by reading the receipt, not by a test that had one.
    steps_per_epoch = max(1, len(loader) // cfg.grad_accum)
    total_steps = min(cfg.max_steps, steps_per_epoch)
    scheduler = get_cosine_schedule_with_warmup(
        optimiser, num_warmup_steps=max(1, total_steps // 10),
        num_training_steps=total_steps)

    torch.cuda.reset_peak_memory_stats()
    losses: list[dict] = []
    step = micro = 0
    running = 0.0
    # Tracked separately from `running` on purpose. `running` is the RAW completion-token mean CE,
    # which is the only loss comparable across arms with different weights; `running_weighted` is
    # what the optimiser actually saw. Reporting only the weighted number would make the arms look
    # different for a reason that has nothing to do with the model.
    running_weighted = 0.0
    stop_reason = ""
    deadline = started + cfg.max_seconds
    for batch in loader:
        if interrupted["flag"]:
            stop_reason = f"interrupted ({interrupted['signal']})"
            break
        if time.time() >= deadline:
            stop_reason = "wall-clock budget"
            break
        if step >= cfg.max_steps:
            stop_reason = "step budget"
            break
        batch = {k: v.cuda() for k, v in batch.items()}
        weight = batch.pop("weights")
        raw_loss = completion_loss(model, batch) if cfg.completion_logits else model(**batch).loss
        # THE OBJECTIVE: L(x) = -sum_c w_c * log p(c | x), over the certified children of a state.
        #
        # SEQUENCE/TOKEN NORMALISATION, stated exactly, because it is easy to get silently wrong.
        # HuggingFace's built-in loss with `labels` and -100 masking is a MEAN OVER COMPLETION TOKENS
        # across the whole batch. That has two consequences this code has to be explicit about:
        #
        #   * a LONGER completion contributes proportionally more gradient, so an unusually verbose
        #     spelling of the same repair already outweighed a terse one before any weighting. The
        #     weights cannot fix that, and they are not meant to; they balance children and states;
        #   * with `batch_size > 1` the batch loss is one pooled mean, so a per-example weight has no
        #     exact meaning. Weighting is therefore defined only at `batch_size == 1`, which is what
        #     this trainer uses, and it REFUSES rather than approximating silently.
        #
        # `scale` normalises so the average weight over the dataset is 1.0. Without it, weights that
        # sum to 1.0 across ~111 examples would shrink every gradient by ~1/111 and the run would
        # simply look like a much lower learning rate -- a silent failure that would be read as
        # "weighting did not help".
        if weight.numel() != 1:
            raise SystemExit(
                f"weighted training needs batch_size == 1, got a batch of {weight.numel()}; the "
                f"pooled batch loss cannot carry per-example weights")
        scale = float(weight.item()) * cfg.weight_scale
        loss = raw_loss * scale
        (loss / cfg.grad_accum).backward()
        running += float(raw_loss.detach())
        running_weighted += float(loss.detach())
        micro += 1
        if micro % cfg.grad_accum:
            continue
        torch.nn.utils.clip_grad_norm_(
            [p for p in model.parameters() if p.requires_grad], 1.0)
        optimiser.step()
        scheduler.step()
        optimiser.zero_grad(set_to_none=True)
        step += 1
        if step % cfg.logging_steps == 0 or step == 1:
            row = {"step": step, "loss": round(running / cfg.grad_accum, 5),
                   "weighted_loss": round(running_weighted / cfg.grad_accum, 5),
                   "lr": round(scheduler.get_last_lr()[0], 8),
                   "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3),
                   "elapsed_s": round(time.time() - started, 1)}
            losses.append(row)
            print(json.dumps(row), flush=True)
            running = 0.0
            running_weighted = 0.0
    if micro % cfg.grad_accum and micro:
        optimiser.step()
        scheduler.step()

    # Hash the final LoRA weights BEFORE the publish decision, not after: "did any weight move"
    # is an input to that decision, and the previous ordering read `changed` before computing it.
    after_weights, changed = {}, 0
    for name, param in model.named_parameters():
        if name in before_weights:
            digest = hashlib.sha256(
                param.detach().float().cpu().numpy().tobytes()).hexdigest()
            after_weights[name] = digest
            changed += digest != before_weights[name]

    # `stop_reason` is decided FIRST and the publish test reads it, because the previous version
    # tested for a set of reasons and only then assigned the fallback: a run that finished the
    # whole dataset cleanly left `stop_reason` empty at the test, failed the check, and published
    # nothing. A publish gate that rejects successful runs is as broken as one that accepts
    # failed ones, and harder to notice, because the receipt looked plausible.
    if not stop_reason:
        stop_reason = "training loop exhausted"
    clean_reasons = ("step budget", "wall-clock budget", "training loop exhausted")
    completed_cleanly = not interrupted["flag"] and stop_reason in clean_reasons

    # A CLEAN STOP IS NOT SUFFICIENT. The first version of this gate published an adapter from a
    # run that executed ZERO optimiser steps and changed NO weight, because "the loop ended
    # without an error" was mistaken for "training happened". An adapter that is the
    # initialisation is not a fine-tune, and publishing one would put a meaningless artifact in
    # the promotion path where a later comparison could mistake it for a treatment.
    trained_something = step > 0 and changed > 0
    if completed_cleanly and not trained_something:
        completed_cleanly = False
        stop_reason = (f"no weight changed ({step} step(s), {changed} tensor(s) modified); "
                       f"refusing to publish an adapter that is the initialisation")
    if not completed_cleanly:
        print(json.dumps({"not_published": stop_reason,
                          "note": "an interrupted, failed or no-op run publishes no adapter"}),
              flush=True)

    # --- publish, only on a clean stop --------------------------------------------
    published = False
    staged_paths = None
    if completed_cleanly:
        # Stage first, then promote INTO the published location. Staging is where an unfinishable
        # run leaves its partial weights; the published directory must be directly loadable, so
        # the files are copied to the top level rather than left a directory deeper. The first
        # version published a marker pointing at a directory whose `adapter_config.json` was in a
        # `staging/` subdirectory, so `PeftModel.from_pretrained(published_path)` failed -- a
        # published artifact nobody could load.
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(staging))
        tokenizer.save_pretrained(str(staging))
        staged_paths = sorted(p.name for p in staging.iterdir())
        for item in list(staging.iterdir()):
            target = cfg.out / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        marker = {
            "published_at": int(time.time()),
            "stage": "training",
            "split": cfg.split,
            "steps": step,
            "stop_reason": stop_reason,
            "adapter_sha256": dir_digest(staging)["sha256"],
            "base": str(cfg.base),
            "lora": {"r": cfg.lora_r, "alpha": cfg.lora_alpha},
            "loadable_from": str(cfg.out),
        }
        (cfg.out / PUBLISHED_MARKER).write_text(json.dumps(marker, indent=2) + "\n",
                                                encoding="utf-8")
        published = True

    receipt = {
        "schema_version": 1,
        "stage": "compiler-verified-post-training/smoke",
        "started_at": int(started),
        "seconds": round(time.time() - started, 1),
        "base": dir_digest(cfg.base),
        "base_has_adapter_config": base_adapter.exists(),
        "init_adapter": (dir_digest(cfg.init_adapter) if cfg.init_adapter is not None else None),
        "tasks_file": str(cfg.tasks),
        "tasks_sha256": sha_file(Path(cfg.tasks)),
        "split": cfg.split,
        "examples": {"available": len(examples), "kept": len(kept),
                     "dropped_for_length": len(dropped),
                     "skipped_unverified_labels": skipped_unverified,
                     "tokens_total": sum(row["tokens"] for row in lengths),
                     "prompt_tokens_total": sum(row["prompt_tokens"] for row in lengths),
                     "completion_tokens_total": sum(
                         row["completion_tokens"] for row in lengths),
                     "token_lengths": lengths},
        "hyperparameters": {"max_steps": cfg.max_steps, "max_seconds": cfg.max_seconds,
                            "max_seq_len": cfg.max_seq_len, "batch_size": cfg.batch_size,
                            "grad_accum": cfg.grad_accum, "learning_rate": cfg.learning_rate,
                            "lora_r": cfg.lora_r, "lora_alpha": cfg.lora_alpha,
                            "load_in_4bit": cfg.load_in_4bit, "seed": cfg.seed,
                            "completion_logits": cfg.completion_logits,
                            "balanced_weights": bool(cfg.balanced_weights),
                            "weight_scale": cfg.weight_scale,
                            "variants_file": str(cfg.variants) if cfg.variants else None},
        "steps_run": step,
        "stop_reason": stop_reason,
        "losses": losses,
        # `loss` is the raw completion-token mean CE and is the only figure comparable across arms;
        # `weighted_loss` is what the optimiser saw. Reporting only the latter would make a weighted
        # run look better or worse for a reason that has nothing to do with the model.
        "first_loss": losses[0]["loss"] if losses else None,
        "last_loss": losses[-1]["loss"] if losses else None,
        "first_weighted_loss": losses[0].get("weighted_loss") if losses else None,
        "last_weighted_loss": losses[-1].get("weighted_loss") if losses else None,
        "trainable_parameters": trainable,
        "total_parameters": total,
        "weight_change": {
            "tensors_compared": len(before_weights),
            "tensors_changed": changed,
            "any_change": changed > 0,
            "note": ("hashes of the LoRA parameters before and after; a run that changed no "
                     "weight is not a weight-learning run and must not be reported as one"),
        },
        "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3),
        "resource_limits": limits.as_dict(),
        "published": published,
        "published_marker": str(cfg.out / PUBLISHED_MARKER) if published else None,
        "staging": str(staging) if staged_paths else None,
        "adapter_sha256": dir_digest(staging)["sha256"] if published else None,
        "torch": torch.__version__,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--split", default="train")
    ap.add_argument("--max-seq-len", type=int, default=3072)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--max-seconds", type=float, default=900.0)
    ap.add_argument("--max-examples", type=int, default=0)
    ap.add_argument("--learning-rate", type=float, default=2e-4)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--block-size", type=int, default=8,
                    help="examples per length-sorted bucket; bounds padding inside a batch")
    ap.add_argument("--lora-r", type=int, default=8)
    ap.add_argument("--lora-alpha", type=int, default=16)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--logging-steps", type=int, default=1)
    ap.add_argument("--no-4bit", dest="load_in_4bit", action="store_false")
    ap.add_argument("--init-adapter", type=Path, default=None,
                    help="continue training this published LoRA instead of a fresh one")
    ap.add_argument("--completion-logits", action="store_true",
                    help="compute vocabulary logits only for supervised completion positions; requires model support")
    ap.add_argument("--variants", type=Path, default=None,
                    help="verified-target-variants.jsonl from eval.target_augment: additional "
                         "certificate-verified spellings of the same answers")
    ap.add_argument("--balanced-weights", action="store_true",
                    help="weight each verified child by eval.repair_states.balanced_weights "
                         "(functions balanced, then states, then novelty classes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="tokenize and report the plan; load no model, write no adapter")
    args = ap.parse_args(argv)

    if args.dry_run:
        return _dry_run(args)

    cfg = TrainConfig(base=args.base, tasks=args.tasks, out=args.out, split=args.split,
                      max_seq_len=args.max_seq_len, max_steps=args.max_steps,
                      max_seconds=args.max_seconds, max_examples=args.max_examples,
                      learning_rate=args.learning_rate, batch_size=args.batch_size,
                      grad_accum=args.grad_accum, block_size=args.block_size,
                      lora_r=args.lora_r,
                      lora_alpha=args.lora_alpha, seed=args.seed,
                      logging_steps=args.logging_steps, load_in_4bit=args.load_in_4bit,
                      completion_logits=args.completion_logits,
                      init_adapter=args.init_adapter,
                      variants=args.variants, balanced_weights=args.balanced_weights)
    receipt = train(cfg)
    print(json.dumps({k: v for k, v in receipt.items()
                      if k not in ("losses", "examples")}, indent=2))
    return 0 if receipt["published"] else 1


def _dry_run(args) -> int:
    """Report the plan without loading a model: what the run WOULD train on."""
    from eval.repair_prompts import load_task_examples, load_variants
    tasks = [json.loads(line) for line in
             Path(args.tasks).read_text(encoding="utf-8").splitlines() if line.strip()]
    variants = load_variants(args.variants) if args.variants else []
    examples = load_task_examples(tasks, split=args.split, variants=variants)
    weights = {}
    if args.balanced_weights:
        from eval.repair_states import balanced_weights
        weights = balanced_weights([t for t in tasks if t.get("split") == args.split], variants)
    out = {"split": args.split, "examples": len(examples),
           "max_examples": args.max_examples, "max_steps": args.max_steps,
           "max_seconds": args.max_seconds,
           "variants": str(args.variants) if args.variants else None,
           "balanced_weights": bool(args.balanced_weights),
           "published_already": bool(published_adapter(args.out))}
    if args.balanced_weights:
        from eval.repair_states import weight_summary
        out["weighting"] = weight_summary(
            [t for t in tasks if t.get("split") == args.split], variants)
    if examples:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(args.base))
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        kept, dropped, lengths = tokenize_examples(tokenizer, examples, args.max_seq_len)
        out["kept"] = len(kept)
        out["dropped_for_length"] = len(dropped)
        out["tokens_total"] = sum(row["tokens"] for row in lengths)
        out["tokens_max"] = max(row["tokens"] for row in lengths)
        out["prompt_tokens_max"] = max(row["prompt_tokens"] for row in lengths)
        out["completion_tokens_max"] = max(row["completion_tokens"] for row in lengths)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
