"""Hosted-agent proposal provider: the Codex CLI behind the existing kernel.

`modelrepair` already takes any object with `provider_id` and `generate`, so a
second model needs an adapter, not a second search. This one posts to
`tools/codex_shim.py` on the Windows side (WSL interop is off here, and that is
also what keeps the reference decomp out of the agent's reach -- see the shim).

Two honest protocol differences from the Ollama path, both recorded in the
returned metadata rather than smoothed over, because they are exactly the kind
of detail that later reads as "the model did better" when it was the harness:

* **No seed, no temperature.** The CLI exposes neither. The seed still enters
  the cache key, so different seeds are independent draws and a replay of the
  same seed is exact, but a *fresh* run is not reproducible. `deterministic` is
  false in the metadata; do not describe these runs as seeded.
* **No assistant prefill.** Ollama's prefill works by opening an assistant turn,
  which has no equivalent here, so the text is appended to the user prompt as an
  explicit continuation instruction. `prefill_mode` says which was used.

The cache file layout deliberately matches `solver/llm.py` so receipts, cache
sweeps and replay tooling keep working across providers.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from solver import llm

DEFAULT_MODEL = "gpt-5.3-codex-spark"
DEFAULT_PORT = 11436

PREFILL_INSTRUCTION = (
    "\n\nBegin your answer with exactly the following text, then continue it:\n")


class QuotaExhausted(RuntimeError):
    """The account's model quota is spent.

    Raised rather than returned, because the kernel treats an empty answer as a
    model that declined. A spent quota silently filled four functions of a
    six-function cohort with `incomplete_responses` that looked exactly like a
    weak model failing on hard targets. A run must stop here, not continue and
    record nulls.
    """


def default_endpoint() -> str:
    """Same gateway hop the Ollama client uses, on the shim's port."""
    override = os.environ.get("CODEX_SHIM_ENDPOINT", "").strip()
    if override:
        return override.rstrip("/")
    host = llm.host()                       # http://<gateway>:11434
    return host.rsplit(":", 1)[0] + f":{DEFAULT_PORT}"


class CodexProvider:
    """Drop-in `ProposalProvider` for `eval.agentrepair --provider`."""

    provider_id = "codex-cli"

    def __init__(self, endpoint: str = "", model: str = "",
                 transport_attempts: int = 2):
        self.endpoint = (endpoint or default_endpoint()).rstrip("/")
        self.model = model or os.environ.get("CODEX_MODEL", "") or DEFAULT_MODEL
        self.transport_attempts = transport_attempts

    # ---- cache ---------------------------------------------------------
    def _cache_material(self, request, prompt: str) -> dict:
        return {
            "schema_version": 1,
            "provider": self.provider_id,
            "namespace": request.cache_namespace,
            "endpoint": self.endpoint,
            "model": self.model,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prefill_sha256": hashlib.sha256(request.prefill.encode()).hexdigest(),
            "seed": request.seed,
            "response_schema": request.response_schema,
        }

    def _cache_path(self, cache_dir, material: dict) -> tuple[Path, str]:
        encoded = json.dumps(material, sort_keys=True,
                             separators=(",", ":")).encode()
        key = hashlib.sha256(encoded).hexdigest()
        root = Path(cache_dir).expanduser()
        return root / key[:2] / f"{key}.json", key

    # ---- transport -----------------------------------------------------
    def _post(self, body: dict, timeout: int) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/generate", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())

    def health(self) -> dict:
        with urllib.request.urlopen(f"{self.endpoint}/health", timeout=60) as r:
            return json.loads(r.read())

    # ---- provider protocol ---------------------------------------------
    def generate(self, request) -> tuple[str, dict]:
        prompt = request.prompt
        prefill_mode = "none"
        if request.prefill:
            prompt = prompt + PREFILL_INSTRUCTION + request.prefill
            prefill_mode = "appended-to-user-prompt"

        cache_path = key = None
        material = None
        if request.cache_dir is not None:
            if request.seed is None:
                raise ValueError("generation caching requires an explicit seed")
            material = self._cache_material(request, prompt)
            cache_path, key = self._cache_path(request.cache_dir, material)
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("key_material") != material:
                    raise ValueError(
                        f"generation cache key mismatch: {cache_path}")
                text, meta = cached.get("text"), cached.get("meta")
                if not isinstance(text, str) or not isinstance(meta, dict):
                    raise ValueError(
                        f"invalid generation cache entry: {cache_path}")
                meta = dict(meta)
                meta.update({"_cache_hit": True, "_cache_key": key,
                             "_cache_path": str(cache_path), "_seed": request.seed,
                             "_transport_events": []})
                return text, meta

        body = {"prompt": prompt, "model": self.model,
                "timeout": int(request.timeout)}
        if request.response_schema is not None:
            body["response_schema"] = request.response_schema

        transport_events: list[dict] = []
        payload = None
        delay = 2.0
        for remaining in range(self.transport_attempts, 0, -1):
            started = time.monotonic()
            event = {"attempt": len(transport_events) + 1,
                     "socket_timeout_seconds": int(request.timeout)}
            try:
                payload = self._post(body, int(request.timeout) + 60)
                event.update(status="response",
                             elapsed_seconds=time.monotonic() - started)
                transport_events.append(event)
                break
            except Exception as exc:                       # noqa: BLE001
                event.update(status="error", error_type=type(exc).__name__,
                             elapsed_seconds=time.monotonic() - started)
                if isinstance(exc, urllib.error.HTTPError):
                    event["http_status"] = exc.code
                transport_events.append(event)
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    detail = ""
                    try:
                        detail = json.loads(exc.read()).get("error", "")
                    except Exception:                      # noqa: BLE001
                        pass
                    raise QuotaExhausted(
                        detail or "model quota exhausted") from exc
                if remaining == 1 or (isinstance(exc, urllib.error.HTTPError)
                                      and exc.code < 500):
                    exc.transport_events = list(transport_events)
                    raise
                time.sleep(delay)
                delay *= 2

        text = payload.get("text", "") or ""
        meta = dict(payload.get("meta") or {})
        meta.update({
            "_transport_events": transport_events,
            "_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "_seed": request.seed,
            "deterministic": False,     # the CLI exposes no seed or temperature
            "prefill_mode": prefill_mode,
            "requested_temperature": request.temperature,
            "honoured_temperature": None,
        })
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_name(
                f".{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            temporary.write_text(
                json.dumps({"key_material": material, "text": text,
                            "meta": meta}, indent=2) + "\n", encoding="utf-8")
            temporary.replace(cache_path)
            meta.update({"_cache_hit": False, "_cache_key": key,
                         "_cache_path": str(cache_path)})
        return text, meta


def provider() -> CodexProvider:
    """Factory for `--provider solver.codexprovider:provider`."""
    return CodexProvider()
