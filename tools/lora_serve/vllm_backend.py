"""vLLM backend: continuous batching, fp8 weights, LoRA adapters.

Two WSL2-specific workarounds are REQUIRED on this host and are baked into
:func:`apply_wsl_workarounds` (called by :class:`VllmBackend` on construction),
because without them vLLM 0.29 dies during engine startup:

1. ``VLLM_USE_V2_MODEL_RUNNER=0`` -- the V2 GPU model runner allocates a
   ``UvaBuffer`` and raises ``RuntimeError: UVA is not available`` on WSL2
   (the CUDA device attribute for host-memory mapping is not exposed through
   ``/dev/dxg``; ``vllm.utils.platform_utils.is_uva_available()`` returns
   False even though ``torch.zeros(..., pin_memory=True)`` works). The V1
   runner does not need UVA.
2. ``VLLM_USE_FLASHINFER_SAMPLER=0`` -- otherwise the first sampling call
   JIT-compiles a FlashInfer kernel and fails with
   ``RuntimeError: Could not find nvcc and default cuda_home='/usr/local/cuda'
   doesn't exist``. The sampler falls back to the native torch path.

``CUDA_HOME`` is pointed at the CUDA toolkit that ships inside the venv
(``site-packages/nvidia/cu13``) so any remaining JIT step can find ``nvcc``.

Measured on this host 2026-09-20: fp8 + LoRA, max_model_len 12288,
gpu_memory_utilization 0.72 -> engine ready in ~21 s, generation correct.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from tools.lora_serve.backends import (AdapterSpec, BackendError, SequenceResult,
                                       SequenceSpec)
from tools.lora_serve.sampler import RowSampling

__all__ = ["VllmBackend", "apply_wsl_workarounds"]


def apply_wsl_workarounds() -> dict[str, Any]:
    """Set the env vars vLLM needs on WSL2. Returns what was set, for receipts."""
    applied: dict[str, Any] = {}
    defaults = {
        "VLLM_USE_V2_MODEL_RUNNER": "0",
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    }
    for key, value in defaults.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
        else:
            applied[key] = os.environ[key]
    if not os.environ.get("CUDA_HOME"):
        try:
            import nvidia  # noqa: F401  (namespace package shipped by the wheels)
            candidate = Path(nvidia.__path__[0]) / "cu13"
            if (candidate / "bin" / "nvcc").exists():
                os.environ["CUDA_HOME"] = str(candidate)
                os.environ["PATH"] = f"{candidate / 'bin'}{os.pathsep}" + os.environ["PATH"]
        except Exception:  # noqa: BLE001 - nvcc is only needed for JIT kernels
            pass
    applied["CUDA_HOME"] = os.environ.get("CUDA_HOME")
    return applied


class VllmBackend:
    """Batched vLLM backend. One ``generate`` call carries the whole batch.

    vLLM does its own continuous batching, so the server's scheduler only has to
    hand over the specs it wants; per-request timings come from vLLM's own
    metrics (arrival -> first token = prefill, first token -> finished = decode).
    """

    name = "vllm"

    def __init__(self, model_path: str, *, adapters: Sequence[AdapterSpec] = (),
                 load_in_4bit: bool = False, dtype: str = "bfloat16",
                 device: str = "cuda:0", max_model_len: int = 12288,
                 trust_remote_code: bool = False, attn_implementation: str | None = None,
                 quantization: str = "fp8", gpu_memory_utilization: float = 0.72,
                 max_num_seqs: int = 8, enforce_eager: bool = True,
                 max_lora_rank: int = 32, tensor_parallel_size: int = 1,
                 max_num_batched_tokens: int | None = None,
                 kv_cache_gib: float | None = None,
                 enable_sleep_mode: bool = False, max_loras: int | None = None,
                 **unused: Any) -> None:
        self.model_path = str(Path(model_path).expanduser().resolve())
        self.adapters = list(adapters)
        self.dtype_name = dtype
        self.device = device
        self.max_model_len = int(max_model_len)
        self.trust_remote_code = bool(trust_remote_code)
        self.quantization = quantization
        self.gpu_memory_utilization = float(gpu_memory_utilization)
        self.max_num_seqs = int(max_num_seqs)
        self.enforce_eager = bool(enforce_eager)
        self.max_lora_rank = int(max_lora_rank)
        self.tensor_parallel_size = int(tensor_parallel_size)
        self.max_num_batched_tokens = max_num_batched_tokens
        self.kv_cache_gib = kv_cache_gib
        self.enable_sleep_mode = bool(enable_sleep_mode)
        self.max_loras = max_loras
        self.llm: Any = None
        self.tokenizer: Any = None
        self.eos_token_ids: list[int] = []
        self.pad_token_id: int | None = None
        self.quantisation: dict[str, Any] | None = None
        self.load_report: dict[str, Any] = {}
        self.load_seconds = 0.0
        self._lora_requests: dict[str, Any] = {}
        self._load_in_4bit_requested = bool(load_in_4bit)

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        self.env_workarounds = apply_wsl_workarounds()
        from vllm import LLM

        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model_path,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "max_model_len": self.max_model_len,
            "max_num_seqs": self.max_num_seqs,
            "enforce_eager": self.enforce_eager,
            "trust_remote_code": self.trust_remote_code,
            # Per-request metrics (arrival / first token / finished) are what the
            # receipt uses for prefill_ms and decode_ms; leaving stats ON is the
            # price of a truthful receipt.
            "disable_log_stats": False,
            "tensor_parallel_size": self.tensor_parallel_size,
        }
        if self.max_num_batched_tokens:
            kwargs["max_num_batched_tokens"] = int(self.max_num_batched_tokens)
        if self.enable_sleep_mode:
            kwargs["enable_sleep_mode"] = True
        if self.kv_cache_gib:
            # A fixed KV cache instead of one sized by profiling: on this WSL host the profiled number swung by ~3 GiB
            # between identical starts (other GPU users move free memory during the profile), failing startups.
            kwargs["kv_cache_memory_bytes"] = int(float(self.kv_cache_gib) * 2**30)
        quantization = self.quantization
        if quantization in (None, "", "auto"):
            quantization = "fp8"          # the default this host was measured with
        if self._load_in_4bit_requested and quantization == "fp8":
            # The caller asked for the 4-bit budget the transformers backend
            # defaults to; vLLM spells that 'bitsandbytes'.
            quantization = "bitsandbytes"
        if quantization and quantization not in ("none", "auto"):
            kwargs["quantization"] = quantization
        if self.adapters or self.max_loras:
            # max_loras > len(adapters) leaves slots for adapters loaded later (add_adapter) without a restart.
            kwargs.update({"enable_lora": True, "max_lora_rank": self.max_lora_rank,
                           "max_loras": max(1, len(self.adapters), int(self.max_loras or 0))})
        self.llm = LLM(**kwargs)
        self.tokenizer = self.llm.get_tokenizer()
        eos = self.tokenizer.eos_token_id
        self.eos_token_ids = sorted({int(e) for e in ([eos] if isinstance(eos, int)
                                                      else list(eos or []))})
        self.pad_token_id = self.tokenizer.pad_token_id
        if self.adapters or self.max_loras:
            from vllm.lora.request import LoRARequest
            self._lora_requests = {
                spec.name: LoRARequest(spec.name, index + 1, spec.path)
                for index, spec in enumerate(self.adapters)}
        self.quantisation = (None if not kwargs.get("quantization")
                             else {"method": kwargs["quantization"],
                                   "source": "on-the-fly at load"})
        self.load_seconds = time.monotonic() - started
        self.load_report = {
            "model_path": self.model_path,
            "adapters": [a.name for a in self.adapters],
            "quantisation": self.quantisation,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "max_model_len": self.max_model_len,
            "max_num_seqs": self.max_num_seqs,
            "enforce_eager": self.enforce_eager,
            "load_seconds": round(self.load_seconds, 3),
            "env": self.env_workarounds,
        }

    # -- encoding --------------------------------------------------------
    def encode(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    # -- generation ------------------------------------------------------
    def generate_batch(self, specs: Sequence[SequenceSpec]) -> list[SequenceResult]:
        from vllm import SamplingParams

        if not specs:
            return []
        adapters = {spec.adapter_name for spec in specs}
        if len(adapters) != 1:
            raise BackendError(
                f"generate_batch requires one adapter per batch, got {sorted(adapters)}")
        adapter_name = adapters.pop()
        if adapter_name is not None and adapter_name not in self._lora_requests:
            raise BackendError(f"adapter {adapter_name!r} was not loaded by this backend")

        prompts = [{"prompt_token_ids": list(spec.prompt_ids)} for spec in specs]
        sampling = [self._sampling_params(spec) for spec in specs]
        lora_request: Any = (self._lora_requests[adapter_name]
                             if adapter_name is not None else None)
        started = time.monotonic()
        outputs = self.llm.generate(prompts, sampling, lora_request=lora_request)
        finished = time.monotonic()
        return [self._result(spec, output, started, finished, len(specs), adapter_name,
                             sum(len(s.prompt_ids) for s in specs))
                for spec, output in zip(specs, outputs)]

    # -- live state changes (server --admin): no restart ---------------------
    def add_adapter(self, spec: AdapterSpec) -> None:
        """Serve another LoRA from now on. vLLM loads it on its first request (a new int id, so a re-trained adapter
        under a new name never reuses a cached older one)."""
        from vllm.lora.request import LoRARequest
        if spec.name in self._lora_requests:
            raise BackendError(f"adapter {spec.name!r} is already loaded; use a new name for a new version")
        next_id = max((r.lora_int_id for r in self._lora_requests.values()), default=0) + 1
        self._lora_requests[spec.name] = LoRARequest(spec.name, next_id, spec.path)
        self.adapters.append(spec)

    def sleep(self, level: int = 1) -> None:
        """Free the GPU (level 1: weights to CPU memory, KV cache dropped; level 2: weights dropped too) so another
        process (a policy-gradient update) can use it; wake_up() restores in seconds instead of a cold start."""
        if not self.enable_sleep_mode:
            raise BackendError("server was started without sleep mode (--sleep-mode)")
        self.llm.sleep(level=int(level))
        self._slept_level = int(level)

    def wake_up(self) -> None:
        self.llm.wake_up()
        if getattr(self, "_slept_level", 1) == 2:
            # Level 2 DISCARDS the weights; wake_up only reallocates. Reload them from the checkpoint, or the model
            # would run on uninitialised memory. LoRA weights went with them: unload every adapter so each reloads
            # from disk on its next request (their LoRARequests stay registered).
            self.llm.collective_rpc("reload_weights")
            engine = self.llm.llm_engine
            for lora_id in list(engine.list_loras()):
                engine.remove_lora(lora_id)
        self.llm.reset_prefix_cache()
        self._slept_level = 0

    # -- continuous batching (server --continuous) -------------------------
    # The offline generate() above runs a batch to completion: one long answer holds every slot of its batch
    # (measured 2026-10-04: 1-2 of 4 sequences running, 25-90 tok/s on an RTX 5080). Here sequences join and leave
    # vLLM's running batch individually, which is what vLLM's scheduler is built for.
    def add(self, spec: SequenceSpec) -> str:
        if spec.adapter_name is not None and spec.adapter_name not in self._lora_requests:
            raise BackendError(f"adapter {spec.adapter_name!r} was not loaded by this backend")
        # add_request RETURNS an internal id (the external one plus a suffix), while finished RequestOutputs carry
        # the EXTERNAL id passed here; keying on the return value stalled the first live run. Key on ours.
        rid = f"{spec.request_id}#{spec.index}"
        self.llm.llm_engine.add_request(rid, {"prompt_token_ids": list(spec.prompt_ids)},
                                        self._sampling_params(spec),
                                        lora_request=(self._lora_requests[spec.adapter_name]
                                                      if spec.adapter_name is not None else None))
        return rid

    def step(self) -> list[tuple[str, Any]]:
        """One engine step; (engine request id, finished RequestOutput) for each sequence that finished."""
        return [(out.request_id, out) for out in self.llm.llm_engine.step() if out.finished]

    def has_unfinished(self) -> bool:
        return bool(self.llm.llm_engine.has_unfinished_requests())

    def abort(self, request_ids: list[str]) -> None:
        try:
            self.llm.llm_engine.abort_request(list(request_ids))
        except Exception:  # noqa: BLE001 - abort is best effort; the caller already reports the failure
            pass

    def result_of(self, spec: SequenceSpec, output: Any, started: float, running: int) -> SequenceResult:
        return self._result(spec, output, started, time.monotonic(), running, spec.adapter_name,
                            len(spec.prompt_ids))

    def _result(self, spec: SequenceSpec, output: Any, started: float, finished: float, batch_size: int,
                adapter_name: str | None, batch_prompt_tokens: int) -> SequenceResult:
        completion = output.outputs[0]
        token_ids = [int(t) for t in completion.token_ids]
        continuation = completion.text
        finish_reason = completion.finish_reason or "length"
        stop_reason: dict[str, Any] = {"type": "none"}
        if finish_reason == "stop":
            stop_reason = {"type": "eos_token" if completion.stop_reason is None
                           else "stop",
                           "detail": completion.stop_reason}
        elif finish_reason == "length":
            stop_reason = {"type": "max_tokens"}
        prefill_ms, decode_ms = self._timings(output, started, finished)
        metrics = self._metrics_dict(output)
        returned = len(self.tokenizer(continuation, add_special_tokens=False)["input_ids"])
        return SequenceResult(
            request_id=spec.request_id, index=spec.index,
            continuation=continuation, token_ids=token_ids,
            finish_reason=finish_reason, stop_reason=stop_reason,
            prefill_ms=prefill_ms, decode_ms=decode_ms,
            batch_size=batch_size, adapter_name=adapter_name,
            extra={"prefill": spec.prefill, "returned_tokens": returned,
                   "generated_tokens": len(token_ids),
                   "timing_source": ("vllm_metrics" if metrics
                                     else "batch_wallclock"),
                   "batch_prompt_tokens": batch_prompt_tokens,
                   "metrics": metrics})

    def _sampling_params(self, spec: SequenceSpec) -> Any:
        from vllm import SamplingParams

        row: RowSampling = spec.sampling
        kwargs: dict[str, Any] = {
            "n": 1,
            "max_tokens": int(spec.max_tokens),
            "temperature": float(row.temperature if row.temperature is not None else 0.0),
            "top_p": float(row.top_p if row.top_p is not None else 1.0),
            "top_k": int(row.top_k if row.top_k is not None else -1),
            "repetition_penalty": float(row.repetition_penalty
                                        if row.repetition_penalty is not None else 1.0),
            "ignore_eos": False,
            "skip_special_tokens": True,
        }
        if row.frequency_penalty is not None:
            kwargs["frequency_penalty"] = float(row.frequency_penalty)
        if row.presence_penalty is not None:
            kwargs["presence_penalty"] = float(row.presence_penalty)
        if row.seed is not None:
            kwargs["seed"] = int(row.seed)
        if spec.stop:
            kwargs["stop"] = list(spec.stop)
        if spec.stop:
            kwargs["include_stop_str_in_output"] = False
        return SamplingParams(**kwargs)

    @staticmethod
    def _metrics_dict(output: Any) -> dict[str, Any]:
        metrics = getattr(output, "metrics", None)
        if metrics is None:
            return {}
        return {key: getattr(metrics, key) for key in
                ("arrival_time", "first_scheduled_time", "first_token_time",
                 "finished_time", "scheduler_time", "queued_time")
                if getattr(metrics, key, None) is not None}

    @staticmethod
    def _timings(output: Any, started: float, finished: float) -> tuple[float, float]:
        """(prefill_ms, decode_ms) from vLLM's own per-request metrics.

        When metrics are unavailable the whole batch wall clock is reported as
        decode time and prefill as 0.0 -- never a guess dressed up as a
        measurement; the receipt carries ``timing_source`` so a reader can tell.
        """
        metrics = getattr(output, "metrics", None)
        if metrics is not None:
            arrival = getattr(metrics, "arrival_time", None)
            first = getattr(metrics, "first_token_time", None)
            done = getattr(metrics, "finished_time", None)
            if first is not None and done is not None:
                origin = arrival if arrival is not None else started
                return (first - origin) * 1000.0, (done - first) * 1000.0
        return 0.0, (finished - started) * 1000.0

    # -- reporting -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "backend": self.name,
            "model_path": self.model_path,
            "adapters": [a.name for a in self.adapters],
            "quantisation": self.quantisation,
            "dtype": self.dtype_name,
            "device": self.device,
            "max_model_len": self.max_model_len,
            "max_num_seqs": self.max_num_seqs,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "enforce_eager": self.enforce_eager,
            "eos_token_ids": self.eos_token_ids,
            "pad_token_id": self.pad_token_id,
            "load_seconds": round(self.load_seconds, 3),
            "env_workarounds": getattr(self, "env_workarounds", None),
        }
        try:
            import torch
            free, total = torch.cuda.mem_get_info()
            info["vram_free_gb"] = round(free / 2**30, 3)
            info["vram_total_gb"] = round(total / 2**30, 3)
        except Exception:  # noqa: BLE001
            pass
        return info

    def warmup(self, tokens: int = 8) -> dict[str, Any]:
        spec = SequenceSpec(request_id="warmup", index=0, prompt="warmup",
                            prompt_ids=self.encode("def f():\n    "),
                            sampling=RowSampling(temperature=0.0, top_p=None, top_k=None,
                                                 repetition_penalty=None, seed=1),
                            max_tokens=tokens)
        started = time.monotonic()
        self.generate_batch([spec])
        return {"warmup_seconds": round(time.monotonic() - started, 3)}
