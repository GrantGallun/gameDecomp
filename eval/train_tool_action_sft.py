"""Tool-action SFT: train the model to choose the NEXT TOOL ACTION from an observation.

WHAT THIS IS NOT
----------------
It is not `eval/train_repair_sft.py`. That trainer teaches the model to emit better C given
compiler feedback, and its examples come from repair states. This one trains a different task
format, from a different dataset: given an observation of a repair attempt, emit the one JSON
action object the policy should take next (`{"action": ..., "params": {...}}`). The two trainers
share the completion-only masking contract and nothing else, and this module deliberately does not
touch the source-repair path.

THE THING THAT MAKES THIS RUN TRUSTWORTHY
-----------------------------------------
THE LABEL BOUNDARY MUST BE THE INFERENCE BOUNDARY.

Inference prefills the assistant turn with the single character `{` and lets the model continue
from there (`eval/tool_agent_probe.py`, `PREFILL`; `ModelPolicy.choose` in
`eval/tool_agent_compare.py` does the same thing through `tokenizer(PREFILL,
add_special_tokens=False)`). So the trained span has to be:

    ids_of("{")  ++  ids_of(completion[1:], add_special_tokens=False)  ++  EOS

and NOT `ids_of(completion)`. The reason is a BPE merge, not a technicality: a byte-level BPE
tokenizer can carry the `{` as an incomplete byte sequence and fuse it with whatever follows, so
`ids_of('{"action"...')` may start with one token `{"` where inference would have fed `{` and then
`"`. Tokenizing the whole completion therefore trains a different first label than the one the
model is asked to produce at inference, and the damage is invisible: the loss still falls. `render`
below builds the two pieces separately: the check it reports is that the resulting completion ids
start with the prefill ids, and the property that matters -- the first trained token being the token
inference prefills -- is what the two calls buy, demonstrated in
`tests/test_tool_action_sft.py::test_the_trained_span_is_the_prefill_plus_the_rest_plus_eos`.
WHAT ELSE THIS GETS RIGHT
-------------------------
1. COMPLETION-ONLY LOSS. Every prompt token is -100; only the prefilled `{`, the action JSON and
   the EOS carry loss. Tool results live in the prompt, so masking the prompt masks them too --
   `mask_tool_result_tokens`-style filtering is unnecessary here precisely because the policy is
   never trained to emit a tool result.
2. OVER-LONG EXAMPLES ARE DROPPED AND COUNTED. An action JSON that is cut in half trains the model
   to emit invalid JSON, which is the one failure the action format exists to prevent.
3. THE SPLIT COMES FROM THE DATASET. Records whose `split` is not the requested one are excluded,
   never re-drawn; `family` isolation is the dataset builder's job and is not second-guessed here.
4. THE RECEIPT IS WRITTEN LAST AND CARRIES EVERYTHING a later reader needs to say what this
   adapter was trained on: dataset path and sha256, base path, adapter path, hyperparameters, seed,
   token counts, kept/dropped counts, wall seconds and package versions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# The assistant turn is primed with this at inference. It is ALSO the first trained token, which is
# the whole point of the boundary machinery below.
PREFILL = "{"

RECEIPT_NAME = "training_receipt.json"
TOKEN_REPORT_NAME = "token_report.json"


@dataclass
class TrainConfig:
    dataset: Path
    base: Path
    out: Path
    epochs: float = 1.0
    lr: float = 1e-4
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    max_seq_len: int = 4096
    batch_size: int = 1
    grad_accum: int = 8
    seed: int = 20260920
    limit: int = 0
    max_steps: int = -1
    split: str = "train"
    adapter_name: str = "adapter"
    load_in_4bit: bool = True
    device: str = "cuda:0"
    logging_steps: int = 5
    dry_run: bool = False

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("dataset", "base", "out"):
            out[key] = str(out[key])
        return out


# --- dataset ------------------------------------------------------------------

REQUIRED_KEYS = ("messages", "completion")


def validate_record(record: object, *, index: int, source: "Path | str") -> None:
    """Reject a malformed record loudly. A silent empty completion is the failure to avoid.

    THE SPECIFIC DEFECT THIS PREVENTS: `record.get("completion", "")` produces an empty label
    span, and an example whose label span is only the prefilled `{` plus EOS trains the model to
    emit an opening brace and stop. It does not raise. It looks like a small loss. Every required
    field is therefore checked by name, and the error names the line so the dataset can be fixed
    rather than filtered.
    """
    where = f"{source} line {index + 1}"
    if not isinstance(record, dict):
        raise ValueError(f"{where}: record is {type(record).__name__}, expected a JSON object")
    missing = [key for key in REQUIRED_KEYS if not record.get(key)]
    if missing:
        raise ValueError(
            f"{where} (id={record.get('id')!r}): missing or empty {', '.join(missing)}; a record "
            f"without a prompt or without an action JSON cannot be trained on and is not silently "
            f"skipped here")
    messages = record["messages"]
    if not isinstance(messages, list) or not all(
            isinstance(m, dict) and m.get("role") and m.get("content") for m in messages):
        raise ValueError(f"{where} (id={record.get('id')!r}): messages must be a non-empty list of "
                         f"{{'role', 'content'}} objects")
    completion = record["completion"]
    if not isinstance(completion, str) or not completion.startswith(PREFILL):
        raise ValueError(
            f"{where} (id={record.get('id')!r}): completion must be a string starting with "
            f"{PREFILL!r} because inference prefills exactly that character; got "
            f"{str(completion)[:40]!r}")


def load_records(path: "Path | str") -> list[dict]:
    """Read a JSONL dataset, validating every record as it is read."""
    path = Path(path)
    records = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path} line {index + 1}: not valid JSON: {exc}") from exc
        validate_record(record, index=index, source=path)
        records.append(record)
    return records


def make_example(record: dict) -> dict:
    """Flatten one dataset record into the fields training and reporting need."""
    return {
        "id": str(record.get("id")),
        "kind": record.get("kind"),
        "split": record.get("split"),
        "family": record.get("family"),
        "function": record.get("function"),
        "label_source": record.get("label_source"),
        "label_confidence": record.get("label_confidence"),
        "budget_remaining": record.get("budget_remaining"),
        "messages": record["messages"],
        "completion": record["completion"],
        "acceptable_count": len(record.get("acceptable") or []),
    }


def select_records(records: list[dict], split: str, limit: int = 0):
    """Keep only the requested split. Returns (examples, split_tally, lines_read).

    `split="any"` exists for a plumbing canary on a dataset that has no split column yet. It is not
    a licence to train the experiment on dev or test: those records are the held-out set, and
    folding them in would make every later number a fit to the evaluation.
    """
    tally: dict[str, int] = {}
    for record in records:
        key = str(record.get("split"))
        tally[key] = tally.get(key, 0) + 1
    wanted = records if split in ("", "any", None) else [
        r for r in records if r.get("split") == split]
    if limit and limit > 0:
        wanted = wanted[:limit]
    return [make_example(r) for r in wanted], tally, len(records)


# --- tokenisation -------------------------------------------------------------


def _ids_of(encoded) -> list[int]:
    """Token ids from whatever `apply_chat_template` / the tokenizer returned.

    transformers 5 returns a `BatchEncoding` for `apply_chat_template(..., tokenize=True)`, NOT a
    list. `len()` of it is the number of KEYS -- 2 -- so a trainer that assumed a list would
    compute a 2-token prompt boundary for every example, mask the wrong span, and still report a
    falling loss. That happened in this project and was caught by an assertion, not by training.
    """
    if isinstance(encoded, dict):
        return list(encoded["input_ids"])
    if hasattr(encoded, "input_ids"):
        ids = encoded.input_ids
        if ids and isinstance(ids[0], (list, tuple)):
            return list(ids[0])
        return list(ids)
    return list(encoded)


def _single_ids_of(encoded) -> list[int]:
    """`_ids_of` for a plain `tokenizer(text)` call, which may be batched or not."""
    if isinstance(encoded, dict):
        ids = encoded["input_ids"]
    elif hasattr(encoded, "input_ids"):
        ids = encoded.input_ids
    else:
        ids = encoded
    if ids and isinstance(ids[0], (list, tuple)):
        return list(ids[0])
    return list(ids)


def assert_tokenizer_usable(tokenizer, probe: str = "x") -> None:
    """Fail loudly if the tokenizer cannot express a chat turn we can mask.

    A tokenizer that returns fewer ids than characters is not rendering the template, and every
    downstream number would be silently wrong.
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


def boundary_ok(prefill_ids: list[int], completion_ids: list[int]) -> bool:
    """Does the completion span start with exactly the ids the inference harness prefills?

    Split out of `render` so the check can be exercised on a constructed span without a tokenizer:
    `test_tool_action_sft.py` feeds it a completion whose first token is a fused `{"` and requires
    it to say no.
    """
    if not prefill_ids:
        raise ValueError("the prefill tokenized to nothing; there is no boundary to check")
    return completion_ids[:len(prefill_ids)] == list(prefill_ids)


def render(tokenizer, example: dict) -> tuple[list[int], int, bool]:
    """(ids, prompt boundary, matches_inference_boundary) for one example.

    The returned ids ARE the trained sequence: prompt (masked) followed by the prefilled `{`
    (trained), the rest of the action JSON (trained) and EOS (trained). The boundary is the number
    of masked positions, so the label span is `ids[boundary:]` by construction.

    `matches_inference_boundary` answers the question the run has to be able to state: did the
    completion ids come out starting with the prefill ids, i.e. is the first trained token the token
    inference prefills? See the comment on the check below for what that can and cannot catch.
    """
    prefix = _ids_of(tokenizer.apply_chat_template(
        example["messages"], tokenize=True, add_generation_prompt=True))
    completion = example["completion"]
    # TWO CALLS ON PURPOSE. See the module docstring: one call on the whole completion can merge
    # `{"` into a single token and move the first trained label.
    head = _single_ids_of(tokenizer(PREFILL, add_special_tokens=False))
    tail = _single_ids_of(tokenizer(completion[len(PREFILL):], add_special_tokens=False))
    eos = getattr(tokenizer, "eos_token_id", None)
    completion_ids = head + tail + ([] if eos is None else [eos])
    # THE REQUIRED REPORT LINE: how many examples' completion ids do NOT start with the prefill ids.
    # On this path the answer is always zero, and that is honest rather than reassuring: the span
    # was just BUILT as `head + tail`, and both training and inference (`ModelPolicy.choose`) build
    # it the same way, so this is a guard that fires if a later change reorders `render`, not a live
    # detector. The property that actually protects the experiment is the two calls themselves, and
    # the test that shows a one-call tokenization would fuse the prefill is
    # `test_the_trained_span_is_the_prefill_plus_the_rest_plus_eos`.
    matches = boundary_ok(head, completion_ids)
    ids = list(prefix) + completion_ids
    if not 0 < len(prefix) < len(ids):
        raise ValueError(f"degenerate rendering for {example.get('id')!r}: {len(prefix)} prompt "
                         f"tokens of {len(ids)}")
    return ids, len(prefix), matches


def one_call_differs(tokenizer, example: dict) -> bool:
    """Would a ONE-call tokenization of the completion disagree with the two-call span?

    Reported per example as a diagnostic, never as a gate. BPE is contextual and a fused `{"` is
    the ordinary case on a byte-level tokenizer, so a non-zero count says "this tokenizer merges
    bytes across the prefill", which is exactly why the span is built from two calls.
    """
    head = _single_ids_of(tokenizer(PREFILL, add_special_tokens=False))
    tail = _single_ids_of(tokenizer(example["completion"][len(PREFILL):],
                                    add_special_tokens=False))
    whole = _single_ids_of(tokenizer(example["completion"], add_special_tokens=False))
    return whole != (head + tail)[:len(whole)]


def tokenize_examples(tokenizer, examples, max_seq_len: int):
    """Tokenize, and DROP over-long examples rather than truncating them.

    Truncating an action JSON teaches the model to emit invalid JSON -- the exact failure the
    format exists to prevent -- and the damage is invisible in the loss. Dropped rows carry the
    reason and the length so the report can state both.
    """
    kept, dropped, rows = [], [], []
    for example in examples:
        ids, boundary, matches = render(tokenizer, example)
        row = {
            "id": example["id"], "tokens": len(ids), "prompt_tokens": boundary,
            "completion_tokens": len(ids) - boundary, "split": example.get("split"),
            "label_source": example.get("label_source"), "kind": example.get("kind"),
            "family": example.get("family"), "function": example.get("function"),
            "boundary_matches_inference": bool(matches),
            "one_call_tokenization_differs": bool(one_call_differs(tokenizer, example)),
        }
        rows.append(row)
        # A boundary mismatch is NOT a drop. The sequence is still trainable, but it is no longer
        # the inference task, so the run refuses after tokenisation rather than discarding the
        # evidence that a tokenizer change moved the label. Counted, not filtered.
        if len(ids) > max_seq_len:
            dropped.append({**row, "reason": f"exceeds max_seq_len {max_seq_len}"})
            continue
        kept.append((ids, boundary))
    return kept, dropped, rows


# --- collation ----------------------------------------------------------------


def collate(tokenizer):
    """Completion-only collator: prompt tokens are masked, completion tokens are not.

    Written by hand rather than by a library flag because the masking rule IS the experiment.
    `labels[i] = -100` for every position strictly before the prompt boundary and for every pad
    position; `attention_mask` is 0 on pad. -100 and not the pad id: a pad id in the labels is a
    loss term the model can never satisfy.
    """
    import torch

    def collate_fn(batch):
        input_ids, labels, attention = [], [], []
        for ids, boundary in batch:
            if not 0 < boundary < len(ids):
                raise ValueError(
                    f"a batch item has a prompt boundary of {boundary} in {len(ids)} tokens; the "
                    f"completion span would be empty and the step would train on nothing")
            lab = list(ids)
            for position in range(boundary):
                lab[position] = -100
            input_ids.append(list(ids))
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


# --- reporting ----------------------------------------------------------------


def _distribution(values: list[int]) -> dict:
    """min / mean / p50 / p90 / max. Reported for prompt and completion lengths separately.

    A mean alone hides the shape that matters here: action JSONs are short and similar, prompts
    are long and variable, and an over-long tail is what `max_seq_len` drops.
    """
    if not values:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p90": None, "max": None}
    ordered = sorted(values)

    def quantile(fraction: float) -> int:
        return ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))]

    return {"count": len(values), "min": ordered[0],
            "mean": round(sum(ordered) / len(ordered), 1),
            "p50": quantile(0.50), "p90": quantile(0.90), "max": ordered[-1]}


def _tally(rows: list[dict], field: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def summarize(rows: list[dict], dropped: list[dict], *, lines_read: int, split_tally: dict,
              split: str) -> dict:
    """Everything the summary and the receipt must state about the data.

    `rows` is the LENGTH TABLE -- one row per selected example, kept or not -- so `kept` is
    `len(rows) - len(dropped)` and never `len(rows)`. The first version of this function used
    `len(rows)` and reported every dropped example as trained on: a report that overstates the
    dataset by exactly the number of examples it dropped is worse than no report, because the
    dropped count next to it looks consistent.
    """
    kept_rows = [row for row in rows if row["id"] not in {d["id"] for d in dropped}]
    kept_ids = {row["id"] for row in kept_rows}
    return {
        "lines_read": lines_read,
        "records_by_split_in_file": split_tally,
        "requested_split": split,
        "examples_available": len(rows),
        "kept": len(kept_rows),
        "dropped": len(dropped),
        "kept_ids": sorted(kept_ids),
        "kept_by_split": _tally(kept_rows, "split"),
        "kept_by_label_source": _tally(kept_rows, "label_source"),
        "kept_by_kind": _tally(kept_rows, "kind"),
        "dropped_by_reason": _tally(dropped, "reason"),
        "dropped_rows": dropped,
        "tokens": {
            "sequence": _distribution([row["tokens"] for row in kept_rows]),
            "prompt": _distribution([row["prompt_tokens"] for row in kept_rows]),
            "completion": _distribution([row["completion_tokens"] for row in kept_rows]),
            "prompt_total": sum(row["prompt_tokens"] for row in kept_rows),
            "completion_total": sum(row["completion_tokens"] for row in kept_rows),
            "total": sum(row["tokens"] for row in kept_rows),
            # Stated separately because an over-long example is exactly the one a reader wants to
            # see the size of, and it is not in the distributions above.
            "dropped_sequence": _distribution([row["tokens"] for row in dropped]),
        },
        "label_boundary": {
            "prefill": PREFILL,
            "examples_checked": len(rows),
            # Filled in by `train`, which owns the mismatch list. The key exists here so a reader
            # of an older report cannot confuse "absent" with "zero".
            "examples_where_completion_ids_do_not_start_with_prefill_ids": None,
            "examples_where_one_call_tokenization_differs":
                sum(1 for row in rows if row["one_call_tokenization_differs"]),
            "note": ("the trained span is ids_of('{') + ids_of(completion[1:]) + EOS; the first "
                     "count is whether those completion ids start with ids_of('{'). Both training "
                     "and inference build the span the same way, so that count is a guard against "
                     "a later change to the render path rather than a live detector. The second "
                     "count is the diagnostic that says why the two-call render exists at all: a "
                     "one-call tokenization of the completion fuses bytes across the prefill on a "
                     "byte-level BPE tokenizer"),
        },
        "per_example": rows,
    }


def boundary_mismatches(rows: list[dict]) -> list[dict]:
    """Examples whose separately-tokenized prefill boundary disagrees with one-call tokenization."""
    return [row for row in rows if not row.get("boundary_matches_inference")]


def sha256_file(path: "Path | str") -> str | None:
    """The dataset digest for the receipt. `None` when the file cannot be read, never a guess."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _version(module_name: str, attribute: str = "__version__") -> str | None:
    """A package version, or None when the package is not installed. A receipt must not lie."""
    try:
        module = __import__(module_name)
    except Exception:                                     # noqa: BLE001 - a missing package is data
        return None
    return getattr(module, attribute, "unknown")


# --- the run ------------------------------------------------------------------


def train(cfg: TrainConfig) -> dict:
    """Run one tool-action SFT job and return the receipt (which is also written to --out).

    A dry run returns its report and touches no model. Everything that could refuse the run --
    malformed records, a missing split, a boundary mismatch, an empty dataset -- refuses before the
    model is loaded, so a data problem costs seconds rather than a checkpoint's worth of memory.
    """
    # Resource caps FIRST, before any CUDA allocation: on a shared box an uncapped run takes the
    # whole card and every core, and the machine stops being usable for anything else.
    # `eval.resource_limits` holds the knobs; the defaults leave half the card and half the cores.
    from eval import resource_limits
    applied_limits = resource_limits.apply()

    started = time.time()
    cfg.out = Path(cfg.out)
    cfg.out.mkdir(parents=True, exist_ok=True)

    records = load_records(cfg.dataset)
    examples, split_tally, lines_read = select_records(records, cfg.split, cfg.limit)
    if not examples:
        raise SystemExit(
            f"no records with split={cfg.split!r} in {cfg.dataset}; the file contains "
            f"{json.dumps(split_tally)}. Training on another split's records would fit the "
            f"held-out set.")
    print(json.dumps({"records": lines_read, "selected": len(examples), "split": cfg.split,
                      "records_by_split_in_file": split_tally}), flush=True)

    # The token report is written BEFORE anything is loaded, because it is the artifact that says
    # what the run would train on and it must exist even when the run stops early.
    tokenizer = _load_tokenizer(cfg)
    assert_tokenizer_usable(tokenizer)
    kept, dropped, rows = tokenize_examples(tokenizer, examples, cfg.max_seq_len)
    mismatched = boundary_mismatches(rows)
    summary = summarize(rows, dropped, lines_read=lines_read, split_tally=split_tally,
                        split=cfg.split)
    summary["label_boundary"]["examples_where_completion_ids_do_not_start_with_prefill_ids"] = \
        len(mismatched)
    # Written BEFORE either refusal below: when a run stops for a data reason, the report is the
    # only artifact that says WHICH records caused it, and a refusal that leaves no evidence is a
    # refusal someone re-runs blind.
    (cfg.out / TOKEN_REPORT_NAME).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("per_example", "dropped_rows", "kept_ids")}, indent=2),
          flush=True)

    if mismatched:
        # The trained token span is not the inference token span for these records, so the run
        # would measure something other than the task. Better to stop than to train it and report
        # the result as action-policy SFT.
        raise SystemExit(
            f"{len(mismatched)} example(s) tokenize with a different boundary when the prefill is "
            f"tokenized separately from the rest of the completion (e.g. "
            f"{[r['id'] for r in mismatched[:3]]}). Training these would not reproduce the "
            f"inference token boundary; refusing. See {cfg.out / TOKEN_REPORT_NAME}.")
    if not kept:
        raise SystemExit(
            f"every example exceeded max_seq_len {cfg.max_seq_len}; nothing to train on "
            f"({len(dropped)} dropped)")

    if cfg.dry_run:
        # No model is loaded: a dry run exists to price the data, and loading a 7B checkpoint to
        # learn nothing about it would cost a card's worth of memory for no measurement.
        report = {"dry_run": True, "dataset": str(cfg.dataset), "split": cfg.split,
                  "base": str(cfg.base), "out": str(cfg.out),
                  "examples_kept": len(kept), "examples_dropped": len(dropped),
                  "token_report": str(cfg.out / TOKEN_REPORT_NAME),
                  "resource_limits": applied_limits.as_dict(),
                  "seconds": round(time.time() - started, 1)}
        (cfg.out / "dry_run.json").write_text(json.dumps(report, indent=2) + "\n",
                                              encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return report

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, BitsAndBytesConfig, Trainer,
                             TrainingArguments)

    if not torch.cuda.is_available():
        raise SystemExit("no CUDA device visible; refusing to start a training run")
    torch.manual_seed(cfg.seed)
    torch.cuda.manual_seed_all(cfg.seed)
    print(f"resource limits: {json.dumps(applied_limits.as_dict())}", flush=True)

    quantisation = None
    if cfg.load_in_4bit:
        # QLoRA, as `eval/tool_agent_compare.py` loads the same checkpoint for inference: a 7B base
        # in bf16 plus activations does not fit beside another job, and the failure mode is a bare
        # CUDA OOM during backward rather than "this machine cannot do that". The precision is
        # recorded in the receipt, never hidden.
        quantisation = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        str(cfg.base), dtype=torch.bfloat16, quantization_config=quantisation,
        device_map=(cfg.device if quantisation else None), trust_remote_code=False)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()

    model = get_peft_model(model, LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"]))
    # `get_peft_model` rebuilds module wrappers, so placement is re-asserted AFTER wrapping.
    # Without this the embedding table stays on CPU while the batch goes to CUDA, and the failure
    # surfaces as an index_select device error deep inside the model rather than here.
    if not cfg.load_in_4bit:
        model.to(torch.device(cfg.device))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"trainable {trainable:,} / {total:,} ({100 * trainable / total:.3f}%)", flush=True)

    adapter_path = cfg.out / cfg.adapter_name
    steps_per_epoch = max(1, -(-len(kept) // max(1, cfg.batch_size * cfg.grad_accum)))
    args = TrainingArguments(
        output_dir=str(cfg.out / "trainer"),
        per_device_train_batch_size=cfg.batch_size,
        gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.lr,
        num_train_epochs=cfg.epochs,
        # -1 lets the epoch count decide; a positive value caps the run and is reported as such.
        max_steps=(cfg.max_steps if cfg.max_steps and cfg.max_steps > 0 else -1),
        logging_steps=max(1, cfg.logging_steps),
        logging_first_step=True,
        save_strategy="no",
        report_to=[],
        seed=cfg.seed,
        data_seed=cfg.seed,
        bf16=True,
        max_grad_norm=1.0,
        remove_unused_columns=False,   # the batch is a hand-built dict; do not let Trainer prune it
        disable_tqdm=True,
    )
    # No `data_collator=` flag for masking and no library collator: the masking rule IS the
    # experiment, so the labels are built by hand in `collate` and the Trainer only moves them.
    trainer = Trainer(model=model, args=args, train_dataset=kept,
                      data_collator=collate(tokenizer), processing_class=tokenizer)

    torch.cuda.reset_peak_memory_stats()
    result = trainer.train()
    # The adapter goes in its own directory. `mkdir` first because `save_pretrained` does not
    # create intermediate directories, and a run that trained for an hour must not fail at the one
    # step that produces the deliverable.
    adapter_path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    history = [row for row in trainer.state.log_history if "loss" in row]
    losses = [{"step": row.get("step"), "loss": row.get("loss"),
               "lr": row.get("learning_rate")} for row in history]

    receipt = {
        "schema_version": 1,
        "created_at": int(started),
        "seconds": round(time.time() - started, 1),
        "dataset": str(cfg.dataset),
        "dataset_sha256": sha256_file(cfg.dataset),
        "base": str(cfg.base),
        "adapter_path": str(adapter_path),
        "config": cfg.as_dict(),
        "hyperparameters": {
            "epochs": cfg.epochs, "lr": cfg.lr, "batch_size": cfg.batch_size,
            "grad_accum": cfg.grad_accum, "max_seq_len": cfg.max_seq_len,
            "max_steps": cfg.max_steps, "steps_per_epoch": steps_per_epoch,
            "lora_r": cfg.lora_r, "lora_alpha": cfg.lora_alpha,
            "lora_dropout": cfg.lora_dropout, "load_in_4bit": cfg.load_in_4bit,
            "optimizer": "adamw_torch", "scheduler": "linear", "bf16": True,
            "max_grad_norm": 1.0,
        },
        "seed": cfg.seed,
        "examples": {
            "lines_read": summary["lines_read"],
            "records_by_split_in_file": split_tally,
            "requested_split": cfg.split,
            "kept": len(kept),
            "dropped": len(dropped),
            "kept_by_split": summary["kept_by_split"],
            "kept_by_label_source": summary["kept_by_label_source"],
            "dropped_by_reason": summary["dropped_by_reason"],
        },
        "examples_used": len(kept),
        "length_distributions": {
            "sequence": summary["tokens"]["sequence"],
            "prompt": summary["tokens"]["prompt"],
            "completion": summary["tokens"]["completion"],
            "prompt_total": summary["tokens"]["prompt_total"],
            "completion_total": summary["tokens"]["completion_total"],
            "total": summary["tokens"]["total"],
        },
        "label_boundary": summary["label_boundary"],
        "examples_with_boundary_mismatch":
            summary["label_boundary"][
                "examples_where_completion_ids_do_not_start_with_prefill_ids"],
        "steps_run": int(trainer.state.global_step),
        "first_loss": losses[0]["loss"] if losses else None,
        "last_loss": losses[-1]["loss"] if losses else None,
        "losses": losses,
        "train_result": {k: v for k, v in (result.metrics or {}).items()
                         if isinstance(v, (int, float))},
        "trainable_parameters": trainable,
        "total_parameters": total,
        "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3),
        "resource_limits": applied_limits.as_dict(),
        "packages": {
            "torch": _version("torch"),
            "transformers": _version("transformers"),
            "peft": _version("peft"),
            "bitsandbytes": _version("bitsandbytes") if cfg.load_in_4bit else None,
        },
        "token_report": str(cfg.out / TOKEN_REPORT_NAME),
        "note": ("completion-only SFT for the tool-action policy. The trained span is the "
                 "prefilled '{' plus the action JSON plus EOS; prompt tokens (including every "
                 "tool result) are masked to -100. This receipt says what was trained on, not "
                 "that the policy improved -- held-out evaluation is separate."),
    }
    (cfg.out / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def _load_tokenizer(cfg: TrainConfig):
    """The base checkpoint's tokenizer, with a pad token.

    The tokenizer MUST be the one the inference harness uses: the masking boundary is a property of
    the tokenizer, so training with a different one (or a different revision) would put the label
    span somewhere the served model does not expect it.
    """
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(cfg.base), trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        # A pad token is needed for the collator. Taking the EOS is the convention the rest of this
        # project uses; it is safe here only because pad positions are masked to -100 in `collate`.
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def main(argv: list[str] | None = None) -> int:
    """Parse the CLI, run `train`, print the receipt without the per-step loss list."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", type=Path, required=True,
                    help="tool-action JSONL, one record per line")
    ap.add_argument("--base", type=Path,
                    default=Path.home() / "decomp" / "models" / "qwen2.5-coder-7b",
                    help="local base checkpoint; the default is the same 7B the inference harness "
                         "loads, so the trained boundary and the served boundary are one tokenizer")
    ap.add_argument("--out", type=Path, required=True,
                    help="run directory: adapter, token report and receipt are written here")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    ap.add_argument("--max-seq-len", type=int, default=4096,
                    help="examples longer than this are DROPPED and counted, never truncated")
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--limit", type=int, default=0,
                    help="train on at most this many records, after split filtering; 0 = all")
    ap.add_argument("--max-steps", type=int, default=-1,
                    help="cap optimiser steps; -1 lets --epochs decide")
    ap.add_argument("--split", default="train",
                    help="dataset split to train on; 'any' trains on every split (a plumbing "
                         "canary only -- never for a reported number)")
    ap.add_argument("--adapter-name", default="adapter",
                    help="subdirectory of --out to save the LoRA adapter into")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--logging-steps", type=int, default=5)
    ap.add_argument("--load-in-4bit", action=argparse.BooleanOptionalAction, default=True,
                    help="QLoRA 4-bit base weights; --no-load-in-4bit for bf16")
    ap.add_argument("--dry-run", action="store_true",
                    help="tokenize and report the plan; load no model and run no training")
    args = ap.parse_args(argv)

    cfg = TrainConfig(dataset=args.dataset, base=args.base, out=args.out, epochs=args.epochs,
                      lr=args.lr, lora_r=args.lora_r, lora_alpha=args.lora_alpha,
                      lora_dropout=args.lora_dropout, max_seq_len=args.max_seq_len,
                      batch_size=args.batch_size, grad_accum=args.grad_accum, seed=args.seed,
                      limit=args.limit, max_steps=args.max_steps, split=args.split,
                      adapter_name=args.adapter_name, load_in_4bit=args.load_in_4bit,
                      device=args.device, logging_steps=args.logging_steps,
                      dry_run=args.dry_run)
    report = train(cfg)
    print(json.dumps({k: v for k, v in report.items() if k != "losses"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
