"""Model/adapter identity hashing and the generation receipt schema.

A receipt is the only durable evidence that a recorded number came from a
particular model, adapter, prompt and sampler. Everything here is pure stdlib so
that receipts can be validated without torch, a GPU, or the server.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "RECEIPT_SCHEMA",
    "adapter_identity",
    "model_identity",
    "sha256_file",
    "sha256_text",
    "server_identity",
    "build_receipt",
]

RECEIPT_SCHEMA = "gameDecomp.local_inference.receipt/1"

_CHUNK = 1 << 20

# Files that identify a PEFT adapter. Everything that can change a draw is
# hashed; weights first because that is what "adapter hash" means to a reader.
_ADAPTER_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt")


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tree_files(root: Path, limit: int = 200) -> list[Path]:
    if root.is_file():
        return [root]
    files = [p for p in sorted(root.rglob("*")) if p.is_file()
             and p.suffix not in (".pyc",)]
    if len(files) > limit:
        raise ValueError(f"adapter directory {root} has {len(files)} files; refusing to hash")
    return files


def adapter_identity(path: str | os.PathLike[str] | None,
                     *, hash_files: bool = True) -> dict[str, Any] | None:
    """Identify a PEFT adapter directory by content, not by path.

    ``combined_sha256`` is a sha256 over the sorted ``<relative name>:<sha256>``
    lines of every file in the adapter directory, so it changes if the weights,
    the config, or an added tokenizer file changes. The per-file hashes are kept
    so a reader can verify any single artifact.
    """
    if path is None:
        return None
    root = Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"adapter path does not exist: {root}")
    files = _tree_files(root)
    entries: list[dict[str, Any]] = []
    lines: list[str] = []
    for item in files:
        relative = item.name if root.is_file() else str(item.relative_to(root))
        record: dict[str, Any] = {
            "name": relative,
            "bytes": item.stat().st_size,
        }
        if hash_files:
            digest = sha256_file(item)
            record["sha256"] = digest
            lines.append(f"{relative}:{digest}")
        else:
            record["sha256"] = None
            lines.append(f"{relative}:{item.stat().st_size}:{int(item.stat().st_mtime)}")
        entries.append(record)
    combined = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    config: dict[str, Any] = {}
    config_path = root / "adapter_config.json" if root.is_dir() else None
    if config_path is not None and config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            config = {"_unreadable": f"{type(exc).__name__}: {exc}"}
    weight_files = [e for e in entries
                    if Path(e["name"]).suffix in _ADAPTER_WEIGHT_SUFFIXES]
    return {
        "path": str(root.resolve()),
        "name": root.name,
        "combined_sha256": combined,
        "files_hashed": bool(hash_files),
        "files": entries,
        "weights": [e["name"] for e in weight_files],
        "config": config,
        "r": config.get("r"),
        "lora_alpha": config.get("lora_alpha"),
        "target_modules": config.get("target_modules"),
        "peft_type": config.get("peft_type"),
        "base_model_name_or_path": config.get("base_model_name_or_path"),
    }


def model_identity(path: str | os.PathLike[str], *, hash_weights: bool = False,
                   weight_hash_limit_gb: float = 40.0) -> dict[str, Any]:
    """Identify the base checkpoint.

    Hashing 15 GB of shards costs tens of seconds at startup, so by default only
    the config, the index and the file sizes/mtimes are recorded and
    ``weights_hashed`` is false. ``--hash-weights`` turns on full shard hashing
    (needed when a number has to be tied to byte-exact weights).
    """
    root = Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"model path does not exist: {root}")
    config: dict[str, Any] = {}
    config_sha = None
    config_path = root / "config.json"
    if config_path.exists():
        raw = config_path.read_bytes()
        config_sha = hashlib.sha256(raw).hexdigest()
        try:
            config = json.loads(raw.decode("utf-8"))
        except ValueError:  # pragma: no cover - defensive
            config = {}
    weights: list[dict[str, Any]] = []
    total_bytes = 0
    total_gb = sum(p.stat().st_size for p in sorted(root.glob("*.safetensors"))
                   if p.is_file()) / 2**30
    if hash_weights and total_gb > weight_hash_limit_gb:
        raise ValueError(
            f"refusing to hash {total_gb:.1f} GB of weights (limit {weight_hash_limit_gb} GB)")
    for item in sorted(root.glob("*.safetensors")):
        if not item.is_file():
            continue
        size = item.stat().st_size
        total_bytes += size
        record = {"name": item.name, "bytes": size}
        if hash_weights:
            record["sha256"] = sha256_file(item)
        weights.append(record)
    generation_config: dict[str, Any] = {}
    gc_path = root / "generation_config.json"
    if gc_path.exists():
        try:
            generation_config = json.loads(gc_path.read_text(encoding="utf-8"))
        except ValueError:  # pragma: no cover - defensive
            generation_config = {}
    return {
        "path": str(root.resolve()),
        "name": root.name,
        "architectures": config.get("architectures"),
        "model_type": config.get("model_type"),
        "torch_dtype": config.get("torch_dtype"),
        "num_hidden_layers": config.get("num_hidden_layers"),
        "hidden_size": config.get("hidden_size"),
        "vocab_size": config.get("vocab_size"),
        "max_position_embeddings": config.get("max_position_embeddings"),
        "config_sha256": config_sha,
        "weights": weights,
        "weights_bytes": total_bytes,
        "weights_hashed": bool(hash_weights),
        "checkpoint_generation_config": generation_config,
    }


def server_identity(*, backend: str, quantisation: dict[str, Any] | None,
                    dtype: str, device: str, served_models: Sequence[str],
                    max_model_len: int, max_batch_size: int,
                    started_at: float, extra: Mapping[str, Any] | None = None
                    ) -> dict[str, Any]:
    """Static identity of the serving process, embedded in every receipt."""
    payload: dict[str, Any] = {
        "backend": backend,
        "quantisation": dict(quantisation) if quantisation else None,
        "dtype": dtype,
        "device": device,
        "served_models": list(served_models),
        "max_model_len": int(max_model_len),
        "max_batch_size": int(max_batch_size),
        "pid": os.getpid(),
        "host": platform.node(),
        "python": platform.python_version(),
        "started_at": started_at,
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
    }
    if extra:
        payload.update(dict(extra))
    return payload


@dataclass
class GenerationTimings:
    """Wall-clock breakdown for one request, all in milliseconds."""

    queue_ms: float = 0.0
    tokenize_ms: float = 0.0
    prefill_ms: float = 0.0
    decode_ms: float = 0.0
    total_ms: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "queue_ms": round(self.queue_ms, 3),
            "tokenize_ms": round(self.tokenize_ms, 3),
            "prefill_ms": round(self.prefill_ms, 3),
            "decode_ms": round(self.decode_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


def build_receipt(
    *,
    request_id: str,
    created: float,
    endpoint: str,
    model: dict[str, Any],
    adapter: dict[str, Any] | None,
    adapter_requested: str | None,
    rendered: Mapping[str, Any],
    prompt_tokens: int,
    prompt_token_ids_sha256: str,
    prompt_token_ids: Sequence[int] | None,
    completion_tokens: int,
    finish_reason: str,
    stop_reason: Mapping[str, Any],
    text: str,
    continuation: str,
    sampling: Mapping[str, Any],
    timings: GenerationTimings,
    server: Mapping[str, Any],
    batch: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the receipt returned inline and/or written to the receipt log."""
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "request_id": request_id,
        "created": created,
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created)),
        "endpoint": endpoint,
        "model": model,
        "adapter": adapter,
        "adapter_requested": adapter_requested,
        "adapter_hash": (adapter or {}).get("combined_sha256"),
        "rendered_prompt": rendered.get("rendered_prompt"),
        "rendered_history": rendered.get("rendered_history"),
        "rendered_prompt_sha256": sha256_text(str(rendered.get("rendered_prompt") or "")),
        "rendered_prompt_chars": len(str(rendered.get("rendered_prompt") or "")),
        "prefill": rendered.get("prefill"),
        "templated": rendered.get("templated"),
        "template_source": rendered.get("template_source"),
        "messages": rendered.get("messages"),
        "render_notes": rendered.get("render_notes"),
        "prompt_tokens": int(prompt_tokens),
        "prompt_token_ids_sha256": prompt_token_ids_sha256,
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(prompt_tokens) + int(completion_tokens),
        "finish_reason": finish_reason,
        "stop_reason": dict(stop_reason),
        "sampling": dict(sampling),
        "raw_response_text": text,
        "continuation_text": continuation,
        "raw_response_sha256": sha256_text(text),
        "raw_response_chars": len(text),
        "timings_ms": timings.as_dict(),
        "wall_ms": round(timings.total_ms, 3),
        "tokens_per_second": (
            round(completion_tokens / (timings.decode_ms / 1000.0), 3)
            if timings.decode_ms > 0 and completion_tokens else None),
        "server": dict(server),
        "batch": dict(batch) if batch else None,
    }
    if prompt_token_ids is not None:
        receipt["prompt_token_ids"] = list(prompt_token_ids)
    if extra:
        receipt.update(dict(extra))
    return receipt
