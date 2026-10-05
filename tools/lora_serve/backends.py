"""Inference backends: a batched transformers server (and an optional vLLM one).

The server never talks to a model directly; it hands a batch of
:class:`SequenceSpec` to a backend and gets :class:`SequenceResult` back. That
seam is what makes the receipt fields (prompt token ids, finish reason, timings,
batch composition) the backend's responsibility rather than the HTTP layer's.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tools.lora_serve.sampler import PerRowSampler, RowSampling, StepTiming

__all__ = [
    "AdapterSpec",
    "BackendError",
    "SequenceResult",
    "SequenceSpec",
    "TransformersBackend",
    "build_backend",
]


class BackendError(RuntimeError):
    """The backend cannot serve the request (never a silent fallback)."""


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    path: str
    identity: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class SequenceSpec:
    """One sequence to generate (a request with n>1 becomes n specs)."""

    request_id: str
    index: int
    prompt: str
    prompt_ids: list[int]
    prefill: str = ""
    adapter_name: str | None = None
    sampling: RowSampling = field(default_factory=RowSampling)
    max_tokens: int = 512
    stop: tuple[str, ...] = ()

    @property
    def row_key(self) -> tuple[str, int]:
        return (self.request_id, self.index)


@dataclass
class SequenceResult:
    request_id: str
    index: int
    continuation: str
    token_ids: list[int]
    finish_reason: str
    stop_reason: dict[str, Any]
    prefill_ms: float
    decode_ms: float
    batch_size: int
    adapter_name: str | None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.extra.get("prefill", "") + self.continuation

    @property
    def generated_tokens(self) -> int:
        return len(self.token_ids)

    @property
    def completion_tokens(self) -> int:
        return int(self.extra.get("returned_tokens", len(self.token_ids)))


class _BaseBackend:
    name = "base"

    def describe(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def generate_batch(self, specs: Sequence[SequenceSpec]) -> list[SequenceResult]:
        raise NotImplementedError


def _apply_stop_strings(text: str, stops: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """Truncate at the earliest stop string.

    Stop strings are applied to the DECODED text, not inside the decode loop, so
    the receipt reports both the tokens generated and the tokens returned. A
    token-level stop would burn fewer tokens; it is not implemented and is
    recorded as such rather than pretended.
    """
    earliest: tuple[int, str] | None = None
    for stop in stops:
        if not stop:
            continue
        where = text.find(stop)
        if where >= 0 and (earliest is None or where < earliest[0]):
            earliest = (where, stop)
    if earliest is None:
        return text, {"type": "none"}
    return text[:earliest[0]], {"type": "stop_string", "matched": earliest[1],
                                "char_index": earliest[0]}


class TransformersBackend(_BaseBackend):
    """Batched HF transformers backend with optional 4-bit base weights and LoRA.

    A single worker thread owns the GPU; batching happens in the scheduler, which
    is why ``generate_batch`` takes a whole batch and holds the model for its
    duration.
    """

    name = "transformers"

    def __init__(self, model_path: str, *, adapters: Sequence[AdapterSpec] = (),
                 load_in_4bit: bool = True, dtype: str = "bfloat16",
                 device: str = "cuda:0", max_model_len: int = 8192,
                 trust_remote_code: bool = False,
                 attn_implementation: str = "sdpa",
                 quant_type: str = "nf4", double_quant: bool = True) -> None:
        self.model_path = str(Path(model_path).expanduser().resolve())
        self.adapters = list(adapters)
        self.load_in_4bit = bool(load_in_4bit)
        self.dtype_name = dtype
        self.device = device
        self.max_model_len = int(max_model_len)
        self.trust_remote_code = bool(trust_remote_code)
        self.attn_implementation = attn_implementation
        self.quant_type = quant_type
        self.double_quant = bool(double_quant)
        self.model: Any = None
        self.tokenizer: Any = None
        self.peft_model: Any = None
        self.eos_token_ids: list[int] = []
        self.pad_token_id: int | None = None
        self.load_report: dict[str, Any] = {}
        self.load_seconds: float = 0.0

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        started = time.monotonic()
        torch_dtype = getattr(torch, self.dtype_name)
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, trust_remote_code=self.trust_remote_code)
        self.pad_token_id = (self.tokenizer.pad_token_id
                             if self.tokenizer.pad_token_id is not None
                             else self.tokenizer.eos_token_id)
        eos = self.tokenizer.eos_token_id
        self.eos_token_ids = sorted({int(e) for e in ([eos] if isinstance(eos, int)
                                                      else list(eos or []))})

        kwargs: dict[str, Any] = {
            "trust_remote_code": self.trust_remote_code,
            "low_cpu_mem_usage": True,
            "attn_implementation": self.attn_implementation,
        }
        quantisation: dict[str, Any] | None = None
        if self.load_in_4bit and str(self.device).startswith("cuda"):
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type=self.quant_type,
                bnb_4bit_compute_dtype=torch_dtype,
                bnb_4bit_use_double_quant=self.double_quant,
            )
            quantisation = {"method": "bitsandbytes-4bit",
                            "quant_type": self.quant_type,
                            "double_quant": self.double_quant,
                            "compute_dtype": self.dtype_name}
        if str(self.device).startswith("cuda"):
            kwargs["device_map"] = {"": self.device}
        try:
            model = AutoModelForCausalLM.from_pretrained(
                self.model_path, dtype=torch_dtype, **kwargs)
        except TypeError:
            # transformers < 5 spells it torch_dtype
            model = AutoModelForCausalLM.from_pretrained(
                self.model_path, torch_dtype=torch_dtype, **kwargs)
        model.eval()
        # The checkpoint's own generation_config.json lists eos_token_id
        # [151645, 151643] while the tokenizer exposes only 151645. Stop on both,
        # so this service stops where the checkpoint says it stops.
        config_eos = getattr(getattr(model, "generation_config", None),
                             "eos_token_id", None)
        if config_eos is not None:
            extra = [config_eos] if isinstance(config_eos, int) else list(config_eos)
            self.eos_token_ids = sorted(set(self.eos_token_ids) | {int(e) for e in extra})

        if self.adapters:
            from peft import PeftModel
            first = self.adapters[0]
            model = PeftModel.from_pretrained(model, first.path,
                                              adapter_name=first.name,
                                              is_trainable=False)
            self.peft_model = model
            for extra in self.adapters[1:]:
                model.load_adapter(extra.path, adapter_name=extra.name,
                                   is_trainable=False)
        self.model = model
        self.quantisation = quantisation if self.load_in_4bit else None
        self.load_seconds = time.monotonic() - started
        self.load_report = {
            "model_path": self.model_path,
            "adapters": [a.name for a in self.adapters],
            "load_in_4bit": self.load_in_4bit,
            "quantisation": self.quantisation,
            "load_seconds": round(self.load_seconds, 3),
            "eos_token_ids": self.eos_token_ids,
            "pad_token_id": self.pad_token_id,
        }

    # -- adapter switching ----------------------------------------------
    @contextmanager
    def _use_adapter(self, adapter_name: str | None) -> Iterator[None]:
        if self.peft_model is None:
            if adapter_name is not None:
                raise BackendError(f"adapter {adapter_name!r} requested but no adapters loaded")
            yield
            return
        if adapter_name is None:
            with self.peft_model.disable_adapter():
                yield
            return
        previous = getattr(self.peft_model, "active_adapter", None)
        if isinstance(previous, (list, tuple)):
            previous = previous[0] if previous else None
        if previous == adapter_name:
            yield
            return
        self.peft_model.set_adapter(adapter_name)
        try:
            yield
        finally:
            if previous:
                self.peft_model.set_adapter(previous)

    # -- generation ------------------------------------------------------
    def encode(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def generate_batch(self, specs: Sequence[SequenceSpec]) -> list[SequenceResult]:
        import torch

        if not specs:
            return []
        adapters = {spec.adapter_name for spec in specs}
        if len(adapters) != 1:
            raise BackendError(
                f"generate_batch requires one adapter per batch, got {sorted(adapters)}")
        adapter_name = adapters.pop()

        lengths = [len(spec.prompt_ids) for spec in specs]
        max_prompt = max(lengths)
        batch = len(specs)
        max_new = max(int(spec.max_tokens) for spec in specs)
        if max_prompt + max_new > self.max_model_len:
            raise BackendError(
                f"prompt+max_tokens = {max_prompt}+{max_new} exceeds --max-model-len "
                f"{self.max_model_len}")

        pad = self.pad_token_id
        input_ids = torch.full((batch, max_prompt), int(pad), dtype=torch.long)
        attention = torch.zeros_like(input_ids)
        for row, spec in enumerate(specs):
            ids = torch.tensor(spec.prompt_ids, dtype=torch.long)
            input_ids[row, max_prompt - ids.numel():] = ids
            attention[row, max_prompt - ids.numel():] = 1

        rows = [RowSampling(temperature=spec.sampling.temperature,
                            top_p=spec.sampling.top_p,
                            top_k=spec.sampling.top_k,
                            repetition_penalty=spec.sampling.repetition_penalty,
                            frequency_penalty=spec.sampling.frequency_penalty,
                            presence_penalty=spec.sampling.presence_penalty,
                            seed=spec.sampling.seed) for spec in specs]
        timing = StepTiming(started=time.monotonic())
        sampler = PerRowSampler(rows, eos_token_ids=self.eos_token_ids,
                                device=self.device, timing=timing)

        from transformers import GenerationConfig
        generation_config = GenerationConfig(
            do_sample=True, temperature=1.0, top_k=0, top_p=1.0,
            repetition_penalty=1.0, max_new_tokens=max_new, use_cache=True,
            pad_token_id=int(pad), eos_token_id=list(self.eos_token_ids),
            # The sampler in sampler.py draws the token; these warpers are left
            # at their no-op values on purpose so nothing double-applies a
            # penalty or re-normalises the forced distribution.
            renormalize_logits=False,
        )

        started = time.monotonic()
        with torch.inference_mode(), self._use_adapter(adapter_name):
            output = self.model.generate(
                input_ids=input_ids.to(self.device),
                attention_mask=attention.to(self.device),
                generation_config=generation_config,
                logits_processor=[sampler],
            )
        finished = time.monotonic()
        sequences = getattr(output, "sequences", output)
        generated = sequences[:, max_prompt:].tolist()
        # timings are per BATCH: prefill_ms/decode_ms belong to the batch, and are
        # reported per request too because a request's latency is the batch's.
        prefill_ms = timing.prefill_ms
        decode_ms = timing.decode_ms
        if prefill_ms <= 0.0:
            prefill_ms = (finished - started) * 1000.0

        results: list[SequenceResult] = []
        for row, spec in enumerate(specs):
            ids = generated[row]
            stop_reason: dict[str, Any] = {"type": "none"}
            cut = len(ids)
            for position, token in enumerate(ids):
                if token in self.eos_token_ids:
                    cut = position
                    stop_reason = {"type": "eos_token", "token_id": int(token),
                                   "position": position}
                    break
            kept = ids[:cut]
            finish_reason = "stop" if cut < len(ids) else "length"
            continuation = self.tokenizer.decode(kept, skip_special_tokens=True)
            continuation, stop_info = _apply_stop_strings(continuation, spec.stop)
            if stop_info["type"] != "none":
                stop_reason = stop_info
                finish_reason = "stop"
            returned_tokens = len(self.tokenizer(continuation, add_special_tokens=False)["input_ids"])
            results.append(SequenceResult(
                request_id=spec.request_id, index=spec.index,
                continuation=continuation, token_ids=kept,
                finish_reason=finish_reason, stop_reason=stop_reason,
                prefill_ms=prefill_ms, decode_ms=decode_ms, batch_size=batch,
                adapter_name=adapter_name,
                extra={"prefill": spec.prefill, "returned_tokens": returned_tokens,
                       "generated_tokens": len(kept),
                       "batch_prompt_tokens": int(sum(lengths)),
                       "batch_padded_prompt_tokens": int(max_prompt * batch)}))
        return results

    # -- reporting -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        import torch
        info: dict[str, Any] = {
            "backend": self.name,
            "model_path": self.model_path,
            "adapters": [a.name for a in self.adapters],
            "load_in_4bit": self.load_in_4bit,
            "quantisation": getattr(self, "quantisation", None),
            "dtype": self.dtype_name,
            "device": self.device,
            "max_model_len": self.max_model_len,
            "eos_token_ids": self.eos_token_ids,
            "pad_token_id": self.pad_token_id,
            "load_seconds": round(self.load_seconds, 3),
        }
        if str(self.device).startswith("cuda") and torch.cuda.is_available():
            info["vram_allocated_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
            info["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
            free, total = torch.cuda.mem_get_info()
            info["vram_free_gb"] = round(free / 2**30, 3)
            info["vram_total_gb"] = round(total / 2**30, 3)
        return info

    def warmup(self, tokens: int = 8) -> dict[str, Any]:
        spec = SequenceSpec(request_id="warmup", index=0,
                            prompt="warmup", prompt_ids=self.encode("def f():\n    "),
                            sampling=RowSampling(temperature=0.0, top_p=None, top_k=None,
                                                 repetition_penalty=None, seed=1),
                            max_tokens=tokens)
        started = time.monotonic()
        self.generate_batch([spec])
        return {"warmup_seconds": round(time.monotonic() - started, 3)}


def build_backend(kind: str, model_path: str, **kwargs: Any) -> _BaseBackend:
    """Construct a backend by name. ``vllm`` must import cleanly or it raises."""
    if kind == "transformers":
        accepted = {"adapters", "load_in_4bit", "dtype", "device", "max_model_len",
                    "trust_remote_code", "attn_implementation", "quant_type",
                    "double_quant"}
        return TransformersBackend(
            model_path, **{key: value for key, value in kwargs.items()
                           if key in accepted})
    if kind == "vllm":
        from tools.lora_serve.vllm_backend import VllmBackend
        return VllmBackend(model_path, **kwargs)
    raise BackendError(f"unknown backend {kind!r}; expected 'transformers' or 'vllm'")
