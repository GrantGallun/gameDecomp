"""OpenAI-compatible local inference service with complete generation receipts.

    python -m tools.lora_serve.server --model /home/grant/decomp/models/qwen2.5-coder-7b \\
        --adapter smoke=/home/grant/decomp/models/adapters/smoke --port 8100

Endpoints
---------
``GET  /health``                liveness + what is loaded
``GET  /v1/models``             OpenAI model list (base id and one id per adapter)
``POST /v1/chat/completions``   OpenAI chat, plus prefill + receipt extensions
``POST /v1/completions``        legacy text completion (no chat template)
``GET  /receipts/<request_id>`` the stored receipt for a request

Extensions (documented in the results README; unknown fields are rejected)
-------------------------------------------------------------------------
``prefill``                     partial ASSISTANT turn to continue (the mechanism
                                solver/llm.py uses to suppress refusals)
``continue_final_message``      vLLM-compatible spelling of the same thing
``echo_prefill``                return prefill+continuation (default true)
``receipt``                     true -> attach the receipt JSON to the response
``receipt_token_ids``           true -> include the full prompt token id list
``top_k``, ``repetition_penalty``  sampling controls OpenAI does not define

Why the HTTP layer is stdlib
----------------------------
The server is a bounded queue plus one GPU worker thread that batches. FastAPI
would add a dependency and an async layer that this design does not use, so the
service is built on ``http.server.ThreadingHTTPServer`` and imports nothing
outside the standard library plus the backend's own stack (torch/transformers,
and vLLM only when ``--backend vllm`` is selected).
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.lora_serve import receipts as receipts_mod
from tools.lora_serve.backends import (AdapterSpec, BackendError, SequenceResult,
                                       SequenceSpec, build_backend)
from tools.lora_serve.chatfmt import (ChatRenderError, RenderedPrompt, render_chat,
                                      render_completion)
from tools.lora_serve.sampler import RowSampling

DEFAULT_MODEL = "/home/grant/decomp/models/qwen2.5-coder-7b"
DEFAULT_RECEIPT_LOG = str(Path.home() / "lora_serve_receipts.jsonl")

# Defaults are the checkpoint's own generation_config.json values, so a caller
# that says nothing gets what the checkpoint was published with.
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.8
DEFAULT_TOP_K = 20
DEFAULT_REPETITION_PENALTY = 1.1

# Supported fields (implemented), tolerated fields (accepted and ignored because
# they cannot change the generated text) and rejected fields (they WOULD change
# the result and are not implemented, so accepting them would make the receipt a
# lie about what produced the text).
_KNOWN_FIELDS = {
    # implemented
    "model", "messages", "prompt", "temperature", "top_p", "top_k", "seed",
    "max_tokens", "max_completion_tokens", "repetition_penalty",
    "frequency_penalty", "presence_penalty", "stop", "n", "stream", "prefill",
    "continue_final_message", "echo_prefill", "receipt", "return_receipt",
    "receipt_token_ids", "add_generation_prompt", "chat_template",
    # tolerated: recorded in the receipt echo, no effect on the text
    "user", "metadata", "stream_options",
    # rejected with an explicit message
    "logprobs", "top_logprobs", "response_format", "tools", "tool_choice",
    "logit_bias", "best_of", "suffix", "echo",
}
_REJECTED_FIELDS = {
    "logprobs": "logprobs are not implemented by this local service",
    "top_logprobs": "logprobs are not implemented by this local service",
    "response_format": "response_format is not implemented; ask for 'receipt' instead",
    "tools": "tool calling is not implemented by this local service",
    "tool_choice": "tool calling is not implemented by this local service",
    "logit_bias": "logit_bias is not implemented by this local service",
    "best_of": "best_of is not implemented; send n separate requests instead",
    "suffix": "suffix (infilling) is not implemented by this local service",
    "echo": "'echo' is not implemented by this local service",
}


class ApiError(Exception):
    def __init__(self, message: str, *, status: int = 400, kind: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.param = param
        self.code = code

    def body(self) -> dict[str, Any]:
        return {"error": {"message": str(self), "type": self.kind,
                          "param": self.param, "code": self.code}}


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------
@dataclass
class Job:
    request_id: str
    specs: list[SequenceSpec]
    enqueued_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    results: dict[int, SequenceResult] = field(default_factory=dict)
    error: str | None = None
    batch_mates: list[str] = field(default_factory=list)
    batch_size: int = 0
    event: threading.Event = field(default_factory=threading.Event)

    def wait(self, timeout: float | None) -> bool:
        return self.event.wait(timeout)

    @property
    def queue_ms(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.started_at - self.enqueued_at) * 1000.0


class Scheduler:
    """One worker thread owns the GPU; HTTP threads only enqueue and wait."""

    def __init__(self, backend: Any, *, max_batch_size: int = 6,
                 batch_wait_ms: float = 25.0,
                 max_batch_tokens: int = 40000,
                 max_batch_prefill_tokens: int = 12000) -> None:
        self.backend = backend
        self.max_batch_size = max(1, int(max_batch_size))
        self.batch_wait_ms = max(0.0, float(batch_wait_ms))
        # A batch is bounded by TOTAL tokens (prompt + max_tokens) AND by the
        # summed PROMPT length. KV cache on this 16 GB card shared with another
        # GPU user costs ~56 KB per token; more importantly the prefill attention
        # cost is quadratic in the prompt length, so six 3k-token prompts in one
        # forward pass can allocate several GB of transient attention buffers.
        # That is how the first concurrency-6 run drove free VRAM to zero and
        # left a queued request waiting (measured: vram_free_gb 0.0).
        self.max_batch_tokens = max(1, int(max_batch_tokens))
        self.max_batch_prefill_tokens = max(1, int(max_batch_prefill_tokens))
        self._queue: "queue.Queue[Job | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="gpu-worker", daemon=True)
        self._stopping = threading.Event()
        self.batches_run = 0
        self.jobs_run = 0

    @staticmethod
    def _job_tokens(job: Job) -> tuple[int, int]:
        """(prompt tokens, prompt + max_tokens) for one job."""
        prompt = sum(len(spec.prompt_ids) for spec in job.specs)
        total = sum(len(spec.prompt_ids) + int(spec.max_tokens) for spec in job.specs)
        return prompt, total

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)
        self._thread.join(timeout=30)

    def submit(self, job: Job) -> None:
        if self._stopping.is_set():
            raise ApiError("server is shutting down", status=503, kind="server_error")
        self._queue.put(job)

    def _run(self) -> None:
        while not self._stopping.is_set():
            first = self._queue.get()
            if first is None:
                break
            batch = [first]
            prompt_tokens, tokens = self._job_tokens(first)
            deadline = time.monotonic() + self.batch_wait_ms / 1000.0
            while len(batch) < self.max_batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if tokens >= self.max_batch_tokens:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    self._stopping.set()
                    break
                item_prompt, item_total = self._job_tokens(item)
                if (batch and prompt_tokens + item_prompt
                        > self.max_batch_prefill_tokens):
                    # Too much prefill in one forward pass; run this one next.
                    self._queue.put(item)
                    break
                prompt_tokens += item_prompt
                tokens += item_total
                batch.append(item)
            self._run_batch([job for job in batch if job is not None])

    def _run_batch(self, jobs: Sequence[Job]) -> None:
        if not jobs:
            return
        started = time.monotonic()
        for job in jobs:
            job.started_at = started
        groups: dict[str | None, list[Job]] = {}
        for job in jobs:
            groups.setdefault(job.specs[0].adapter_name, []).append(job)
        for adapter_name, group in groups.items():
            specs = [spec for job in group for spec in job.specs]
            mates = [job.request_id for job in group]
            try:
                results = self.backend.generate_batch(specs)
            except Exception as exc:  # noqa: BLE001 - every failure is reported to a caller
                message = f"{type(exc).__name__}: {exc}"
                for job in group:
                    job.error = message
                    job.finished_at = time.monotonic()
                    job.event.set()
                continue
            by_request: dict[tuple[str, int], SequenceResult] = {
                (r.request_id, r.index): r for r in results}
            for job in group:
                job.batch_mates = mates
                job.batch_size = len(group)
                for spec in job.specs:
                    found = by_request.get((spec.request_id, spec.index))
                    if found is None:
                        job.error = "backend returned no result for this sequence"
                    else:
                        job.results[spec.index] = found
                job.finished_at = time.monotonic()
                job.event.set()
        self.batches_run += 1
        self.jobs_run += len(jobs)


@dataclass
class AdminOp:
    """A state change (sleep, wake, load adapter) run by the GPU worker when nothing is in flight."""
    fn: Any
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str | None = None


class ContinuousScheduler:
    """`--continuous` (vLLM backend): every sequence joins vLLM's running batch on arrival and leaves when IT is
    finished, instead of the whole batch waiting for its longest member (Scheduler). One worker thread still owns
    the engine; HTTP threads only enqueue and wait. Same interface as Scheduler."""

    def __init__(self, backend: Any, **_unused: Any) -> None:
        self.backend = backend
        self._queue: "queue.Queue[Job | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="gpu-worker", daemon=True)
        self._stopping = threading.Event()
        self.batches_run = 0          # engine steps
        self.jobs_run = 0
        self._inflight: dict[str, tuple[Job, SequenceSpec]] = {}
        self._pending_admin: list[AdminOp] = []
        self.asleep = False

    def admin(self, fn, timeout: float = 600.0) -> Any:
        """Run `fn()` on the worker once in-flight requests have finished; the engine is never touched by two
        threads. Raises on failure or timeout."""
        op = AdminOp(fn)
        self._queue.put(op)
        if not op.event.wait(timeout):
            raise ApiError("admin operation timed out", status=504, kind="server_error")
        if op.error:
            raise ApiError(op.error, status=500, kind="server_error")
        return op.result

    def _run_admin(self) -> None:
        while self._pending_admin and not self._inflight:
            op = self._pending_admin.pop(0)
            try:
                op.result = op.fn()
            except Exception as exc:  # noqa: BLE001 - reported to the admin caller
                op.error = f"{type(exc).__name__}: {exc}"
            op.event.set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)
        self._thread.join(timeout=30)

    def submit(self, job: Job) -> None:
        if self._stopping.is_set():
            raise ApiError("server is shutting down", status=503, kind="server_error")
        if self.asleep:
            raise ApiError("server is asleep (POST /admin/wake first)", status=503, kind="server_error")
        self._queue.put(job)

    def _admit(self, job: Job) -> None:
        job.started_at = time.monotonic()
        added = []
        try:
            for spec in job.specs:
                rid = self.backend.add(spec)
                added.append(rid)
                self._inflight[rid] = (job, spec)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never swallowed
            self.backend.abort(added)
            for rid in added:
                self._inflight.pop(rid, None)
            self._finish(job, f"{type(exc).__name__}: {exc}")

    def _finish(self, job: Job, error: str | None = None) -> None:
        if job.event.is_set():
            return
        job.error = error
        job.finished_at = time.monotonic()
        job.batch_size = len(self._inflight) + 1
        job.event.set()
        self.jobs_run += 1

    def _run(self) -> None:
        while not self._stopping.is_set():
            # Before blocking for work: an admin op that waited for in-flight requests must run once they are done,
            # not after the next request arrives (a test hung exactly there).
            self._run_admin()
            try:
                item = self._queue.get(timeout=None if not self._inflight else 0)
            except queue.Empty:
                item = False
            while item is not False:
                if item is None:
                    self._stopping.set()
                    break
                if isinstance(item, AdminOp):
                    self._pending_admin.append(item)
                else:
                    self._admit(item)
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    item = False
            self._run_admin()
            if self._stopping.is_set() or not self._inflight:
                continue
            if not self.backend.has_unfinished():
                # In flight here but unknown to the engine: an id mismatch or a lost request. Fail them now; a
                # blocking step() on an idle engine would hang every caller silently.
                jobs = {id(j): j for j, _s in self._inflight.values()}
                self._inflight.clear()
                for job in jobs.values():
                    self._finish(job, "engine has no record of this request (lost or id mismatch)")
                continue
            try:
                finished = self.backend.step()
            except Exception as exc:  # noqa: BLE001 - an engine failure fails every in-flight job, loudly
                message = f"{type(exc).__name__}: {exc}"
                jobs = {id(j): j for j, _s in self._inflight.values()}
                self.backend.abort(list(self._inflight))
                self._inflight.clear()
                for job in jobs.values():
                    self._finish(job, message)
                continue
            self.batches_run += 1
            running = len(self._inflight)
            for rid, output in finished:
                entry = self._inflight.pop(rid, None)
                if entry is None:
                    continue
                job, spec = entry
                try:
                    job.results[spec.index] = self.backend.result_of(spec, output, job.started_at, running)
                except Exception as exc:  # noqa: BLE001
                    self._finish(job, f"{type(exc).__name__}: {exc}")
                    continue
                if len(job.results) == len(job.specs):
                    self._finish(job)


# ---------------------------------------------------------------------------
# service state
# ---------------------------------------------------------------------------
class Service:
    """Everything a request handler needs: model identity, backend, receipts."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.started_at = time.time()
        self.served_name = args.served_name or Path(args.model).resolve().name
        self.adapter_specs: list[AdapterSpec] = []
        for raw in args.adapter:
            name, path = _parse_adapter_arg(raw)
            identity = receipts_mod.adapter_identity(path, hash_files=not args.no_hash_adapter)
            self.adapter_specs.append(AdapterSpec(name=name, path=str(Path(path).expanduser().resolve()),
                                                  identity=identity))
        names = [spec.name for spec in self.adapter_specs]
        if len(set(names)) != len(names):
            raise SystemExit(f"duplicate adapter names: {names}")

        self.default_adapter: str | None = resolve_default_adapter(
            args.default_adapter, names)

        self.model_identity = receipts_mod.model_identity(
            args.model, hash_weights=args.hash_weights)
        self.backend = build_backend(
            args.backend, args.model, adapters=self.adapter_specs,
            # --load-in-4bit is a transformers-backend default; the vLLM backend
            # quantises with --quantization instead (fp8 by default here).
            load_in_4bit=(args.load_in_4bit and args.backend == "transformers"),
            dtype=args.dtype, device=args.device,
            max_model_len=args.max_model_len, trust_remote_code=args.trust_remote_code,
            attn_implementation=args.attn_implementation,
            quantization=args.quantization,
            gpu_memory_utilization=args.gpu_memory_utilization,
            max_num_seqs=args.max_num_seqs,
            enforce_eager=args.enforce_eager,
            max_lora_rank=args.max_lora_rank,
            max_num_batched_tokens=getattr(args, "max_num_batched_tokens", None),
            kv_cache_gib=getattr(args, "kv_cache_gib", None),
            enable_sleep_mode=getattr(args, "sleep_mode", False),
            max_loras=getattr(args, "max_loras", None))
        if getattr(args, "continuous", False) and args.backend != "vllm":
            raise SystemExit("--continuous needs --backend vllm")
        scheduler_class = ContinuousScheduler if getattr(args, "continuous", False) else Scheduler
        self.scheduler = scheduler_class(self.backend, max_batch_size=args.max_batch_size,
                                   batch_wait_ms=args.batch_wait_ms,
                                   max_batch_tokens=args.max_batch_tokens,
                                   max_batch_prefill_tokens=args.max_batch_prefill_tokens)
        self.receipts: dict[str, dict[str, Any]] = {}
        self.receipt_lock = threading.Lock()
        self.receipt_log = Path(args.receipt_log).expanduser() if args.receipt_log else None
        self.receipt_log_lock = threading.Lock()
        self.model_ids: dict[str, str | None] = {self.served_name: None}
        for spec in self.adapter_specs:
            self.model_ids[f"{self.served_name}+{spec.name}"] = spec.name

    # -- lifecycle -------------------------------------------------------
    def load(self) -> dict[str, Any]:
        self.backend.load()
        report = self.backend.describe()
        if self.args.warmup:
            report["warmup"] = self.backend.warmup(tokens=self.args.warmup_tokens)
        self.scheduler.start()
        if self.receipt_log is not None:
            self.receipt_log.parent.mkdir(parents=True, exist_ok=True)
        return report

    def shutdown(self) -> None:
        self.scheduler.stop()

    # -- identity --------------------------------------------------------
    def server_identity(self) -> dict[str, Any]:
        return receipts_mod.server_identity(
            backend=self.args.backend,
            quantisation=getattr(self.backend, "quantisation", None),
            dtype=self.args.dtype,
            device=self.args.device,
            served_models=list(self.model_ids),
            max_model_len=self.args.max_model_len,
            max_batch_size=self.args.max_batch_size,
            started_at=self.started_at,
            extra={"default_adapter": self.default_adapter,
                   "max_batch_tokens": self.args.max_batch_tokens,
                   "max_batch_prefill_tokens": self.args.max_batch_prefill_tokens,
                   "scheduler": type(getattr(self, "scheduler", None)).__name__,
                   "enforce_eager": getattr(self.args, "enforce_eager", None),
                   "server_version": "1.0",
                   "receipt_schema": receipts_mod.RECEIPT_SCHEMA})

    def health(self) -> dict[str, Any]:
        info = {
            "status": "ok",
            "served_name": self.served_name,
            "model_path": self.model_identity["path"],
            "model_config_sha256": self.model_identity["config_sha256"],
            "default_adapter": self.default_adapter,
            "adapters": {spec.name: spec.identity.get("combined_sha256")
                         for spec in self.adapter_specs},
            "server": self.server_identity(),
            "uptime_s": round(time.time() - self.started_at, 3),
            "batches_run": self.scheduler.batches_run,
            "jobs_run": self.scheduler.jobs_run,
        }
        try:
            info["backend_report"] = self.backend.describe()
        except Exception as exc:  # noqa: BLE001
            info["backend_report"] = {"error": f"{type(exc).__name__}: {exc}"}
        return info

    def resolve_model(self, requested: str | None) -> tuple[str, str | None]:
        """Map an OpenAI ``model`` field to (model_id, adapter_name)."""
        if not requested or requested in ("default", self.served_name):
            return self.served_name, self.default_adapter
        if requested in self.model_ids:
            return requested, self.model_ids[requested]
        # Accept a bare adapter name as a convenience, but never guess silently.
        for spec in self.adapter_specs:
            if requested == spec.name:
                return f"{self.served_name}+{spec.name}", spec.name
        raise ApiError(
            f"unknown model {requested!r}; this server serves {sorted(self.model_ids)}",
            status=404, kind="not_found_error", param="model", code="model_not_found")

    # -- receipts --------------------------------------------------------
    def store_receipt(self, receipt: dict[str, Any]) -> None:
        with self.receipt_lock:
            self.receipts[receipt["request_id"]] = receipt
            limit = self.args.receipt_store
            if limit and len(self.receipts) > limit:
                for key in list(self.receipts)[: len(self.receipts) - limit]:
                    self.receipts.pop(key, None)
        if self.receipt_log is not None:
            line = json.dumps(receipt, ensure_ascii=False)
            with self.receipt_log_lock:
                with open(self.receipt_log, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")

    def get_receipt(self, request_id: str) -> dict[str, Any] | None:
        with self.receipt_lock:
            return self.receipts.get(request_id)


def _parse_adapter_arg(raw: str) -> tuple[str, str]:
    """``NAME=PATH`` or ``PATH`` (name taken from the directory)."""
    if "=" in raw:
        name, _, path = raw.partition("=")
        name = name.strip()
        if not name:
            raise SystemExit(f"--adapter {raw!r} has an empty name")
        return name, path.strip()
    path = Path(raw).expanduser()
    return path.name, str(path)


def resolve_default_adapter(requested: str | None, names: Sequence[str]) -> str | None:
    """Which adapter serves a request that does not name one.

    The default is BASE (``None``). Registering an adapter must never change what
    an unnamed request gets: a named adapter is used only when the request asks
    for it by model id. ``"auto"`` (first registered adapter) is still available,
    but it has to be asked for explicitly, because silently routing to an adapter
    turns an A/B comparison into a comparison of the same weights twice.
    """
    if requested is None or requested == "" or requested == "none":
        return None
    lowered = str(requested).lower()
    if lowered in ("none", "base", "no-adapter"):
        return None
    if lowered == "auto":
        return names[0] if names else None
    if requested not in names:
        raise SystemExit(
            f"--default-adapter {requested!r} is not among the registered adapters "
            f"{list(names)}; use 'none' for base, 'auto' for the first adapter")
    return requested


# ---------------------------------------------------------------------------
# request parsing
# ---------------------------------------------------------------------------
def _require_number(body: Mapping[str, Any], key: str, default: float | None,
                    low: float, high: float) -> float | None:
    value = body.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(f"{key} must be a number", param=key)
    value = float(value)
    if not low <= value <= high:
        raise ApiError(f"{key} must be in [{low}, {high}], got {value}", param=key)
    return value


def _require_int(body: Mapping[str, Any], key: str, default: int | None,
                 low: int, high: int) -> int | None:
    value = body.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f"{key} must be an integer", param=key)
    if not low <= value <= high:
        raise ApiError(f"{key} must be in [{low}, {high}], got {value}", param=key)
    return value


def _stop_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ApiError("stop must be a string or a list of strings", param="stop")


@dataclass
class PreparedRequest:
    endpoint: str
    model_id: str
    adapter_name: str | None
    rendered: RenderedPrompt
    prompt_ids: list[int]
    sampling: RowSampling
    max_tokens: int
    stop: tuple[str, ...]
    n: int
    echo_prefill: bool
    want_receipt: bool
    want_token_ids: bool
    stream: bool
    temperature_source: str
    tokenize_ms: float
    body: Mapping[str, Any]


def prepare(service: Service, body: Mapping[str, Any], endpoint: str) -> PreparedRequest:
    if not isinstance(body, Mapping):
        raise ApiError("request body must be a JSON object")
    unknown = sorted(set(body) - _KNOWN_FIELDS)
    if unknown:
        # Rejecting unknown keys keeps a typo like "topk" from silently sampling
        # with the default. The error names the field.
        raise ApiError(
            f"unknown request field(s) {unknown}; supported fields: "
            f"{sorted(_KNOWN_FIELDS)}")
    for field_name, reason in _REJECTED_FIELDS.items():
        if body.get(field_name):
            raise ApiError(reason, param=field_name)

    model_id, adapter_name = service.resolve_model(body.get("model"))

    temperature = _require_number(body, "temperature", DEFAULT_TEMPERATURE, 0.0, 5.0)
    top_p = _require_number(body, "top_p", DEFAULT_TOP_P, 0.0, 1.0)
    top_k = _require_int(body, "top_k", DEFAULT_TOP_K, 0, 1_000_000)
    repetition_penalty = _require_number(body, "repetition_penalty",
                                         DEFAULT_REPETITION_PENALTY, 0.0, 10.0)
    frequency_penalty = _require_number(body, "frequency_penalty", None, -10.0, 10.0)
    presence_penalty = _require_number(body, "presence_penalty", None, -10.0, 10.0)
    seed = body.get("seed")
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ApiError("seed must be an integer", param="seed")
    max_tokens = body.get("max_tokens", body.get("max_completion_tokens"))
    if max_tokens is None:
        max_tokens = service.args.default_max_tokens
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise ApiError("max_tokens must be an integer", param="max_tokens")
    if max_tokens < 1:
        raise ApiError("max_tokens must be >= 1", param="max_tokens")
    n = _require_int(body, "n", 1, 1, service.args.max_n) or 1
    stream = bool(body.get("stream", False))
    echo_prefill = bool(body.get("echo_prefill", True))
    receipt_field = body.get("receipt", body.get("return_receipt", False))
    want_receipt = bool(receipt_field)
    want_token_ids = bool(body.get("receipt_token_ids", False))
    stop = _stop_list(body.get("stop"))
    chat_template = body.get("chat_template")
    if chat_template is not None and not isinstance(chat_template, str):
        raise ApiError("chat_template must be a string", param="chat_template")

    tokenize_started = time.monotonic()
    if endpoint == "/v1/chat/completions":
        if "messages" not in body:
            raise ApiError("messages is required for chat completions", param="messages")
        rendered = render_chat(
            service.backend.tokenizer, body["messages"],
            prefill=body.get("prefill"),
            continue_final_message=bool(body.get("continue_final_message", False)),
            chat_template=chat_template,
            add_generation_prompt=bool(body.get("add_generation_prompt", True)),
            allow_missing_template=service.args.allow_missing_chat_template)
    else:
        rendered = render_completion(body.get("prompt"))
    prompt_ids = service.backend.encode(rendered.text)
    tokenize_ms = (time.monotonic() - tokenize_started) * 1000.0

    if not prompt_ids:
        raise ApiError("rendered prompt is empty after tokenization", param="messages")
    if len(prompt_ids) + max_tokens > service.args.max_model_len:
        raise ApiError(
            f"prompt ({len(prompt_ids)} tokens) + max_tokens ({max_tokens}) exceeds "
            f"--max-model-len {service.args.max_model_len}; raise the server limit or "
            "shorten the request", param="max_tokens", code="context_length_exceeded")

    sampling = RowSampling(
        temperature=temperature, top_p=top_p, top_k=top_k,
        repetition_penalty=repetition_penalty,
        frequency_penalty=frequency_penalty, presence_penalty=presence_penalty,
        seed=seed)
    return PreparedRequest(
        endpoint=endpoint, model_id=model_id, adapter_name=adapter_name,
        rendered=rendered, prompt_ids=prompt_ids, sampling=sampling,
        max_tokens=int(max_tokens), stop=stop, n=int(n), echo_prefill=echo_prefill,
        want_receipt=want_receipt, want_token_ids=want_token_ids, stream=stream,
        temperature_source="request" if "temperature" in body else "server_default",
        tokenize_ms=tokenize_ms, body=body)


def sampling_receipt(sampling: RowSampling, body: Mapping[str, Any]) -> dict[str, Any]:
    """What was actually used, and whether it came from the request or a default."""
    used = sampling.as_dict()
    used["temperature_source"] = ("request" if "temperature" in body else "server_default")
    used["top_p_source"] = "request" if "top_p" in body else "server_default"
    used["top_k_source"] = "request" if "top_k" in body else "server_default"
    used["repetition_penalty_source"] = ("request" if "repetition_penalty" in body
                                         else "server_default")
    used["seed_source"] = "request" if body.get("seed") is not None else "unseeded"
    used["penalty_scope"] = "prompt+generated"
    used["stop_strings"] = list(_stop_list(body.get("stop")))
    return used


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gameDecomp-lora-serve/1.0"
    service: Service  # set on the server class

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.service.args.quiet:
            return
        sys.stderr.write("[lora-serve] %s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload: Mapping[str, Any], status: int = 200,
                   headers: Mapping[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, error: ApiError) -> None:
        self._send_json(error.body(), status=error.status)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ApiError("request body is empty")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ApiError(f"request body is not valid JSON: {exc}") from exc

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/health", "/healthz"):
                self._send_json(self.service.health())
            elif path == "/v1/models":
                self._send_json(self._models())
            elif path.startswith("/receipts/") or path.startswith("/v1/receipts/"):
                request_id = path.rsplit("/", 1)[-1]
                receipt = self.service.get_receipt(request_id)
                if receipt is None:
                    raise ApiError(f"no receipt for request id {request_id!r}",
                                   status=404, kind="not_found_error")
                self._send_json(receipt)
            else:
                raise ApiError(f"no such endpoint: {path}", status=404, kind="not_found_error")
        except ApiError as error:
            self._send_error(error)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_error(ApiError(f"internal error: {type(exc).__name__}: {exc}",
                                      status=500, kind="server_error"))

    def _models(self) -> dict[str, Any]:
        created = int(self.service.started_at)
        data = []
        for model_id, adapter in self.service.model_ids.items():
            entry = {
                "id": model_id,
                "object": "model",
                "created": created,
                "owned_by": "local",
                "meta": {
                    "model_path": self.service.model_identity["path"],
                    "adapter": adapter,
                    "adapter_hash": next((spec.identity.get("combined_sha256")
                                          for spec in self.service.adapter_specs
                                          if spec.name == adapter), None),
                    "default": model_id == self.service.served_name
                    and adapter == self.service.default_adapter,
                },
            }
            data.append(entry)
        return {"object": "list", "data": data}

    def _admin(self, path: str) -> None:
        """POST /admin/sleep {"level": 1|2} | /admin/wake | /admin/adapter {"name", "path"}: state changes without a
        restart (`--admin`, continuous scheduler only). The server binds 127.0.0.1; nothing here is exposed beyond
        the machine."""
        service = self.service
        sched = service.scheduler
        if not getattr(service.args, "admin", False) or not isinstance(sched, ContinuousScheduler):
            raise ApiError("admin endpoints need --admin and --continuous", status=403, kind="permission_error")
        body = self._read_body()
        backend = service.backend
        if path == "/admin/sleep":
            sched.admin(lambda: backend.sleep(int(body.get("level", 1))))
            sched.asleep = True
        elif path == "/admin/wake":
            sched.admin(backend.wake_up)
            sched.asleep = False
        elif path == "/admin/adapter":
            name, apath = str(body["name"]), str(Path(str(body["path"])).expanduser().resolve())
            spec = AdapterSpec(name=name, path=apath,
                               identity=receipts_mod.adapter_identity(apath, hash_files=not service.args.no_hash_adapter))
            sched.admin(lambda: backend.add_adapter(spec))
            service.adapter_specs.append(spec)
            service.model_ids[f"{service.served_name}+{name}"] = name
        else:
            raise ApiError(f"no such endpoint: {path}", status=404, kind="not_found_error")
        self._send_json({"ok": True, "path": path, "asleep": sched.asleep,
                         "adapters": [s.name for s in service.adapter_specs]})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/admin/"):
                self._admin(path)
                return
            if path not in ("/v1/chat/completions", "/v1/completions"):
                raise ApiError(f"no such endpoint: {path}", status=404, kind="not_found_error")
            body = self._read_body()
            prepared = prepare(self.service, body, path)
            payload = self._generate(prepared)
            if payload.pop("__stream__", False):
                self._send_sse(payload, prepared)
                return
            self._send_json(payload, headers={"X-Request-Id": payload["id"]})
        except ApiError as error:
            self._send_error(error)
        except (BrokenPipeError, ConnectionResetError):  # client gave up; nothing to report
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_error(ApiError(f"internal error: {type(exc).__name__}: {exc}",
                                      status=500, kind="server_error"))

    # -- generation ------------------------------------------------------
    def _send_sse(self, payload: dict[str, Any], prepared: PreparedRequest) -> None:
        """Emit an OpenAI-shaped SSE stream in ONE chunk.

        Streaming here is not incremental: the GPU worker batches, and a streamed
        request cannot be batched with others. The whole completion is emitted as
        a single delta followed by a finish chunk and ``[DONE]``, which is what
        OpenAI-shaped clients concatenate correctly. The receipt rides on the
        final chunk under the ``receipt`` key. This is a deliberate trade:
        exact receipts and batching over token-by-token UX.
        """
        is_chat = prepared.endpoint == "/v1/chat/completions"
        base = {"id": payload["id"], "created": payload["created"],
                "model": payload["model"]}
        events: list[dict[str, Any]] = []
        for choice in payload["choices"]:
            if is_chat:
                events.append({**base, "object": "chat.completion.chunk",
                               "choices": [{"index": choice["index"],
                                            "delta": {"role": "assistant",
                                                      "content": choice["message"]["content"]},
                                            "finish_reason": None}]})
            else:
                events.append({**base, "object": "text_completion",
                               "choices": [{"index": choice["index"], "text": choice["text"],
                                            "finish_reason": None}]})
        final: dict[str, Any] = {**base,
                                 "object": "chat.completion.chunk" if is_chat
                                 else "text_completion",
                                 "choices": [{"index": choice["index"],
                                              "delta": {} if is_chat else None,
                                              "text": None if is_chat else "",
                                              "finish_reason": choice["finish_reason"]}
                                             for choice in payload["choices"]],
                                 "usage": payload["usage"]}
        if "receipt" in payload:
            final["receipt"] = payload["receipt"]
        events.append(final)

        chunks = []
        for event in events:
            chunks.append(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
        chunks.append(b"data: [DONE]\n\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        for key, value in {"X-Request-Id": payload["id"]}.items():
            self.send_header(key, value)
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def _generate(self, prepared: PreparedRequest) -> dict[str, Any]:
        request_id = f"cmpl-{uuid.uuid4().hex[:24]}"
        created = time.time()
        specs = []
        for index in range(prepared.n):
            seed = prepared.sampling.seed
            if seed is not None and index:
                seed = seed + index
            specs.append(SequenceSpec(
                request_id=request_id, index=index, prompt=prepared.rendered.text,
                prompt_ids=prepared.prompt_ids, prefill=prepared.rendered.prefill,
                adapter_name=prepared.adapter_name,
                sampling=RowSampling(
                    temperature=prepared.sampling.temperature,
                    top_p=prepared.sampling.top_p, top_k=prepared.sampling.top_k,
                    repetition_penalty=prepared.sampling.repetition_penalty,
                    frequency_penalty=prepared.sampling.frequency_penalty,
                    presence_penalty=prepared.sampling.presence_penalty, seed=seed),
                max_tokens=prepared.max_tokens, stop=prepared.stop))
        job = Job(request_id=request_id, specs=specs)
        self.service.scheduler.submit(job)
        timeout = self.service.args.request_timeout
        if not job.wait(timeout):
            raise ApiError(
                f"request timed out after {timeout}s waiting for the GPU worker",
                status=504, kind="timeout_error")
        if job.error:
            raise ApiError(f"generation failed: {job.error}", status=500, kind="server_error")

        choices = []
        receipts_choices = []
        prompt_tokens = len(prepared.prompt_ids)
        completion_tokens = 0
        for index in range(prepared.n):
            result = job.results[index]
            text = result.text if prepared.echo_prefill else result.continuation
            completion_tokens += result.completion_tokens
            if prepared.endpoint == "/v1/chat/completions":
                choices.append({
                    "index": index,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": result.finish_reason,
                })
            else:
                choices.append({
                    "index": index, "text": text,
                    "finish_reason": result.finish_reason,
                })
            receipts_choices.append({
                "index": index,
                "finish_reason": result.finish_reason,
                "stop_reason": result.stop_reason,
                "generated_tokens": result.generated_tokens,
                "completion_tokens": result.completion_tokens,
                "raw_response_text": text,
                "continuation_text": result.continuation,
                "timing_source": result.extra.get("timing_source"),
                "batch_size": result.batch_size,
                "token_ids_sha256": receipts_mod.sha256_text(
                    ",".join(str(t) for t in result.token_ids)),
            })
        first = job.results[0]
        timings = receipts_mod.GenerationTimings(
            queue_ms=job.queue_ms, tokenize_ms=prepared.tokenize_ms,
            prefill_ms=first.prefill_ms, decode_ms=first.decode_ms,
            total_ms=(job.finished_at - job.enqueued_at) * 1000.0)
        adapter_identity = next((spec.identity for spec in self.service.adapter_specs
                                 if spec.name == prepared.adapter_name), None)
        receipt = receipts_mod.build_receipt(
            request_id=request_id, created=created, endpoint=prepared.endpoint,
            model=self.service.model_identity, adapter=adapter_identity,
            adapter_requested=prepared.adapter_name,
            rendered=prepared.rendered.receipt_fields(),
            prompt_tokens=prompt_tokens,
            prompt_token_ids_sha256=receipts_mod.sha256_text(
                ",".join(str(t) for t in prepared.prompt_ids)),
            prompt_token_ids=(prepared.prompt_ids if prepared.want_token_ids else None),
            completion_tokens=completion_tokens,
            finish_reason=first.finish_reason,
            stop_reason=first.stop_reason,
            text=(first.text if prepared.echo_prefill else first.continuation),
            continuation=first.continuation,
            sampling=sampling_receipt(prepared.sampling, prepared.body),
            timings=timings,
            server=self.service.server_identity(),
            batch={"size": job.batch_size, "request_ids": job.batch_mates,
                   "prompt_tokens_padded": first.extra.get("batch_padded_prompt_tokens"),
                   "prompt_tokens_total": first.extra.get("batch_prompt_tokens")},
            extra={
                "echo_prefill": prepared.echo_prefill,
                "model_id": prepared.model_id,
                "rendered_prompt_tokens": prompt_tokens,
                "max_tokens": prepared.max_tokens,
                "n": prepared.n,
                "choices": receipts_choices,
                "stop_strings_applied_after_decode": True,
                "request_echo": _echo_request(prepared.body),
            })
        self.service.store_receipt(receipt)

        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens}
        obj = ("chat.completion" if prepared.endpoint == "/v1/chat/completions"
               else "text_completion")
        payload: dict[str, Any] = {
            "id": request_id, "object": obj, "created": int(created),
            "model": prepared.model_id, "choices": choices, "usage": usage,
        }
        if prepared.want_receipt:
            payload["receipt"] = receipt
        if prepared.stream:
            payload["__stream__"] = True
        return payload


def _echo_request(body: Mapping[str, Any]) -> dict[str, Any]:
    """The request as received, minus message bodies (already in the receipt)."""
    echo = {k: v for k, v in body.items() if k not in ("messages", "prompt")}
    if "prompt" in body:
        echo["prompt_chars"] = len(str(body["prompt"]))
    if "messages" in body:
        echo["messages_count"] = len(body["messages"] or [])
    return echo


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.lora_serve.server",
        description="OpenAI-compatible local inference server with generation receipts")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="path to the HuggingFace checkpoint directory")
    parser.add_argument("--adapter", action="append", default=[],
                        metavar="[NAME=]PATH",
                        help="PEFT LoRA adapter to load; repeatable")
    parser.add_argument("--default-adapter", default="none",
                        help="adapter used when a request omits 'model'. Default is "
                             "'none' = the BASE checkpoint, so registering an adapter "
                             "never changes what an unnamed request gets. Use 'auto' "
                             "for the first registered adapter, or an adapter name.")
    parser.add_argument("--served-name", default=None,
                        help="model id reported by /v1/models (default: model dir name)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--backend", default="transformers", choices=["transformers", "vllm"])
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--load-in-4bit", dest="load_in_4bit", action="store_true",
                        default=True,
                        help="quantise the base weights to 4-bit NF4 at load "
                             "(default: on; the 15 GB bf16 checkpoint does not fit "
                             "in 16 GB alongside other GPU users)")
    parser.add_argument("--no-load-in-4bit", dest="load_in_4bit", action="store_false")
    parser.add_argument("--attn-implementation", default="sdpa")
    parser.add_argument("--quantization", default="auto",
                        help="vLLM quantisation: 'fp8' (default when 'auto' and "
                             "--backend vllm), 'bitsandbytes', or 'none'")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.72,
                        help="vLLM memory fraction; 0.72 of 15.9 GB leaves room for "
                             "the Windows-side ollama holding ~2-3.5 GB")
    parser.add_argument("--max-num-seqs", type=int, default=8,
                        help="vLLM scheduler concurrency")
    parser.add_argument("--enforce-eager", dest="enforce_eager", action="store_true",
                        default=True,
                        help="vLLM: skip CUDA graph capture (default on; saves memory "
                             "and load time at some throughput cost)")
    parser.add_argument("--no-enforce-eager", dest="enforce_eager", action="store_false")
    parser.add_argument("--max-lora-rank", type=int, default=32)
    parser.add_argument("--max-num-batched-tokens", type=int, default=None,
                        help="vLLM: tokens per engine step (bounds the activation profile; default vLLM's)")
    parser.add_argument("--kv-cache-gib", type=float, default=None,
                        help="vLLM: fixed KV cache size, skipping profile-based sizing (unstable under WSL)")
    parser.add_argument("--max-model-len", type=int, default=12288)
    parser.add_argument("--max-batch-size", type=int, default=6)
    parser.add_argument("--batch-wait-ms", type=float, default=25.0)
    parser.add_argument("--admin", action="store_true",
                        help="enable POST /admin/{sleep,wake,adapter} (needs --continuous)")
    parser.add_argument("--sleep-mode", action="store_true", help="vLLM: allow sleep/wake (frees the GPU)")
    parser.add_argument("--max-loras", type=int, default=None,
                        help="vLLM: LoRA slots, including adapters added later through /admin/adapter")
    parser.add_argument("--continuous", action="store_true",
                        help="vLLM: sequences join/leave the running batch individually (no batch-wide waits)")
    parser.add_argument("--max-batch-tokens", type=int, default=40000,
                        help="bound a batch by total prompt+max_tokens tokens "
                             "(KV cache is ~56 KB/token on this model)")
    parser.add_argument("--max-batch-prefill-tokens", type=int, default=12000,
                        help="bound a batch by summed PROMPT tokens; prefill "
                             "attention is quadratic in prompt length, so this is "
                             "the knob that keeps a 6-way batch of long prompts "
                             "from exhausting VRAM")
    parser.add_argument("--max-n", type=int, default=8,
                        help="largest accepted value of the OpenAI 'n' field")
    parser.add_argument("--default-max-tokens", type=int, default=1024)
    parser.add_argument("--request-timeout", type=float, default=900.0)
    parser.add_argument("--receipt-log", default=DEFAULT_RECEIPT_LOG,
                        help="JSONL file every receipt is appended to ('' disables)")
    parser.add_argument("--receipt-store", type=int, default=2000,
                        help="receipts kept in memory for GET /receipts/<id>")
    parser.add_argument("--hash-weights", action="store_true",
                        help="sha256 the model shards at startup (slow, ~15 GB of I/O)")
    parser.add_argument("--no-hash-adapter", action="store_true",
                        help="skip sha256 of adapter files (size+mtime identity only)")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--allow-missing-chat-template", action="store_true",
                        help="fall back to 'role: content' rendering when the "
                             "tokenizer has no chat template")
    parser.add_argument("--warmup", dest="warmup", action="store_true", default=True)
    parser.add_argument("--no-warmup", dest="warmup", action="store_false")
    parser.add_argument("--warmup-tokens", type=int, default=8)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"[lora-serve] loading model from {args.model} (backend={args.backend}, "
          f"adapter={[a for a in args.adapter]})", flush=True)
    service = Service(args)
    report = service.load()
    print(f"[lora-serve] backend report: {json.dumps(report, default=str)}", flush=True)
    HealthHandler = type("HealthHandler", (Handler,), {"service": service})
    httpd = ThreadingHTTPServer((args.host, args.port), HealthHandler)
    httpd.daemon_threads = True
    print(f"[lora-serve] listening on http://{args.host}:{args.port} "
          f"models={list(service.model_ids)}", flush=True)

    stopping = threading.Event()

    def _stop(signum: int, _frame: Any) -> None:
        print(f"[lora-serve] signal {signum}: shutting down", flush=True)
        stopping.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        httpd.serve_forever(poll_interval=0.25)
    finally:
        service.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
