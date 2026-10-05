"""Client for the local inference service. Standard library only.

Downstream tools import this rather than hand-rolling HTTP::

    from tools.lora_serve import InferenceClient

    client = InferenceClient("http://127.0.0.1:8100")           # or from_env()
    result = client.chat(
        [{"role": "user", "content": prompt}],
        prefill="```c\\n",                                      # partial assistant turn
        temperature=0.2, top_p=0.8, top_k=20, seed=11,
        max_tokens=1500, repetition_penalty=1.1,
    )
    print(result.text)                       # prefill + continuation
    print(result.receipt["prompt_tokens"])   # exact prompt token count
    print(result.receipt["wall_ms"])         # wall-clock generation ms

Notes that matter operationally:

* ``receipt=True`` (the default) asks the server to attach the full generation
  receipt to the response, and the client FAILS LOUDLY if the server does not
  return one. A silent missing receipt is exactly the kind of thing that makes a
  recorded experiment unauditable later.
* ``timeout`` is a WALL-CLOCK budget for the whole call. ``urllib``'s own timeout
  bounds each socket operation, not the request, so a trickling server can hold
  a call open forever; ``solver/llm.py`` documents a twenty-minute wedge caused
  by exactly that mistake. The reader here enforces the deadline itself.
* ``prefill`` is the service extension; ``continue_final_message`` is the
  vLLM-compatible spelling. Passing ``prefill`` puts the text in the ASSISTANT
  turn, which is the thing that suppresses refusals (see ``solver/llm.py``).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "AdapterNotFound",
    "GenerationResult",
    "InferenceClient",
    "InferenceError",
    "ServerNotRunning",
]

DEFAULT_URL = "http://127.0.0.1:8100"
_ENV_URL = "LOCAL_INFERENCE_URL"


class InferenceError(RuntimeError):
    """The service answered with an error, or answered wrongly."""

    def __init__(self, message: str, *, status: int | None = None,
                 body: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = dict(body) if body else None


class ServerNotRunning(InferenceError):
    """No server is listening at the configured base URL."""


class AdapterNotFound(InferenceError):
    """The requested model id / adapter is not served."""


@dataclass
class GenerationResult:
    """One completion, with the receipt that documents how it was produced."""

    text: str
    continuation: str
    finish_reason: str
    request_id: str
    model: str
    usage: dict[str, int]
    receipt: dict[str, Any] | None
    choices: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    # -- convenience views on the receipt --------------------------------
    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens", 0))

    @property
    def completion_tokens(self) -> int:
        return int(self.usage.get("completion_tokens", 0))

    @property
    def rendered_prompt(self) -> str | None:
        return (self.receipt or {}).get("rendered_prompt")

    @property
    def adapter_hash(self) -> str | None:
        return (self.receipt or {}).get("adapter_hash")

    def require_receipt(self) -> dict[str, Any]:
        if not self.receipt:
            raise InferenceError(
                f"request {self.request_id} returned no receipt; the service must be "
                "started without --no-receipts and the request must set receipt=true")
        return self.receipt


def _read_with_deadline(response: Any, deadline: float) -> bytes:
    """Read a whole HTTP body bounded by a wall clock, not a socket timeout."""
    buffer = bytearray()
    while True:
        if time.monotonic() > deadline:
            raise InferenceError(
                "local inference call exceeded its wall-clock budget "
                f"({deadline - time.monotonic():.1f}s remaining)")
        chunk = response.read(1 << 16)
        if not chunk:
            return bytes(buffer)
        buffer.extend(chunk)


class InferenceClient:
    """Blocking HTTP client for ``tools.lora_serve.server``."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 900.0,
                 receipt: bool = True, require_receipt: bool = True,
                 prefill: str | None = None,
                 defaults: Mapping[str, Any] | None = None) -> None:
        self.base_url = (base_url or os.environ.get(_ENV_URL) or DEFAULT_URL).rstrip("/")
        self.timeout = float(timeout)
        self.receipt = bool(receipt)
        self.require_receipt = bool(require_receipt)
        self.default_prefill = prefill
        self.defaults: dict[str, Any] = dict(defaults or {})

    # -- transport -------------------------------------------------------
    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None,
                 timeout: float | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        budget = float(timeout if timeout is not None else self.timeout)
        deadline = time.monotonic() + budget
        try:
            with urllib.request.urlopen(request, timeout=budget) as response:
                raw = _read_with_deadline(response, deadline)
        except urllib.error.HTTPError as exc:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - the status code is the real signal
                payload = {}
            message = (payload.get("error") or {}).get("message") or exc.reason
            if exc.code == 404 and "model" in str(payload):
                raise AdapterNotFound(f"{url}: {message}", status=exc.code,
                                      body=payload) from exc
            raise InferenceError(f"{url}: HTTP {exc.code}: {message}",
                                 status=exc.code, body=payload) from exc
        except urllib.error.URLError as exc:
            raise ServerNotRunning(
                f"no local inference server at {self.base_url} ({exc.reason}); start it "
                "with: python -m tools.lora_serve.server --model <path>") from exc
        except TimeoutError as exc:
            raise InferenceError(f"{url}: timed out after {budget}s") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise InferenceError(f"{url}: response was not JSON: {raw[:200]!r}") from exc

    # -- endpoints -------------------------------------------------------
    def health(self, *, timeout: float = 30.0) -> dict[str, Any]:
        return self._request("GET", "/health", timeout=timeout)

    def models(self, *, timeout: float = 30.0) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/models", timeout=timeout)["data"]

    def receipt_for(self, request_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
        return self._request("GET", f"/receipts/{request_id}", timeout=timeout)

    def is_ready(self, *, timeout: float = 5.0) -> bool:
        try:
            return self.health(timeout=timeout).get("status") == "ok"
        except InferenceError:
            return False

    def wait_until_ready(self, *, timeout: float = 600.0, interval: float = 1.0) -> dict[str, Any]:
        """Poll /health until ready or the wall-clock budget expires."""
        deadline = time.monotonic() + float(timeout)
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                info = self.health(timeout=min(10.0, max(1.0, deadline - time.monotonic())))
                if info.get("status") == "ok":
                    return info
            except InferenceError as exc:
                last = exc
            time.sleep(interval)
        raise InferenceError(
            f"server at {self.base_url} did not become ready within {timeout}s"
            + (f" (last error: {last})" if last else ""))

    # -- generation ------------------------------------------------------
    def _build_payload(self, base: dict[str, Any], overrides: Mapping[str, Any],
                       prefill: str | None, echo_prefill: bool | None,
                       receipt: bool | None, token_ids: bool) -> dict[str, Any]:
        payload: dict[str, Any] = dict(self.defaults)
        payload.update(base)
        for key, value in overrides.items():
            if value is not None:
                payload[key] = value
        chosen_prefill = self.default_prefill if prefill is None else prefill
        if chosen_prefill:
            payload["prefill"] = chosen_prefill
        if echo_prefill is not None:
            payload["echo_prefill"] = bool(echo_prefill)
        payload["receipt"] = bool(self.receipt if receipt is None else receipt)
        if token_ids:
            payload["receipt_token_ids"] = True
        return payload

    def _post_generation(self, path: str, payload: Mapping[str, Any],
                         timeout: float | None, started: float) -> GenerationResult:
        raw = self._request("POST", path, payload, timeout=timeout)
        latency_ms = (time.monotonic() - started) * 1000.0
        choices = raw.get("choices") or []
        if not choices:
            raise InferenceError(f"{path}: response contained no choices: {raw}")
        first = choices[0]
        is_chat = "message" in first
        content = first["message"]["content"] if is_chat else first.get("text", "")
        receipt = raw.get("receipt")
        result = GenerationResult(
            text=content,
            continuation=(receipt or {}).get("continuation_text", content),
            finish_reason=first.get("finish_reason") or "",
            request_id=raw.get("id", ""),
            model=raw.get("model", ""),
            usage=dict(raw.get("usage") or {}),
            receipt=receipt,
            choices=list(choices),
            raw=raw,
            latency_ms=latency_ms,
        )
        if payload.get("receipt") and self.require_receipt and not receipt:
            raise InferenceError(
                "server returned no receipt although receipt=true was requested; "
                "refusing to hand back an undocumented generation")
        return result

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        prefill: str | None = None,
        continue_final_message: bool | None = None,
        echo_prefill: bool | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        repetition_penalty: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        stream: bool = False,
        receipt: bool | None = None,
        receipt_token_ids: bool = False,
        timeout: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> GenerationResult:
        """One chat completion. ``prefill`` is a partial ASSISTANT turn."""
        overrides: dict[str, Any] = {
            "model": model, "temperature": temperature, "top_p": top_p, "top_k": top_k,
            "seed": seed, "max_tokens": max_tokens,
            "repetition_penalty": repetition_penalty,
            "frequency_penalty": frequency_penalty, "presence_penalty": presence_penalty,
            "stop": list(stop) if isinstance(stop, (list, tuple)) else stop,
            "n": n, "stream": True if stream else None,
            "continue_final_message": True if continue_final_message else None,
        }
        if extra:
            overrides.update(extra)
        payload = self._build_payload({"messages": list(messages)}, overrides, prefill,
                                      echo_prefill, receipt, receipt_token_ids)
        started = time.monotonic()
        if payload.get("stream"):
            return self._stream(path="/v1/chat/completions", payload=payload,
                                timeout=timeout, started=started)
        return self._post_generation("/v1/chat/completions", payload, timeout, started)

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        repetition_penalty: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        stream: bool = False,
        receipt: bool | None = None,
        receipt_token_ids: bool = False,
        timeout: float | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> GenerationResult:
        """One legacy text completion: the prompt is sent verbatim, no template."""
        overrides: dict[str, Any] = {
            "model": model, "temperature": temperature, "top_p": top_p, "top_k": top_k,
            "seed": seed, "max_tokens": max_tokens,
            "repetition_penalty": repetition_penalty,
            "stop": list(stop) if isinstance(stop, (list, tuple)) else stop,
            "n": n, "stream": True if stream else None,
        }
        if extra:
            overrides.update(extra)
        payload = self._build_payload({"prompt": prompt}, overrides, None, None,
                                      receipt, receipt_token_ids)
        payload.pop("prefill", None)
        started = time.monotonic()
        if payload.get("stream"):
            return self._stream(path="/v1/completions", payload=payload,
                                timeout=timeout, started=started)
        return self._post_generation("/v1/completions", payload, timeout, started)

    def generate(self, prompt_or_messages: Any, **kwargs: Any) -> GenerationResult:
        """Dispatch on type: ``str`` -> /v1/completions, message list -> chat."""
        if isinstance(prompt_or_messages, str):
            return self.complete(prompt_or_messages, **kwargs)
        return self.chat(prompt_or_messages, **kwargs)

    # -- streaming (single-chunk SSE; see server.py) ----------------------
    def _stream(self, *, path: str, payload: Mapping[str, Any], timeout: float | None,
                started: float) -> GenerationResult:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        budget = float(timeout if timeout is not None else self.timeout)
        deadline = time.monotonic() + budget
        pieces: list[str] = []
        receipt: dict[str, Any] | None = None
        finish_reason = ""
        raw: dict[str, Any] = {}
        try:
            with urllib.request.urlopen(request, timeout=budget) as response:
                body = _read_with_deadline(response, deadline).decode("utf-8")
        except urllib.error.URLError as exc:
            raise ServerNotRunning(f"no local inference server at {self.base_url}") from exc
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            raw = event
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                pieces.append(delta["content"])
            elif choice.get("text"):
                pieces.append(choice["text"])
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            if event.get("receipt"):
                receipt = event["receipt"]
        text = "".join(pieces)
        return GenerationResult(
            text=text, continuation=(receipt or {}).get("continuation_text", text),
            finish_reason=finish_reason, request_id=raw.get("id", ""),
            model=raw.get("model", ""), usage=dict(raw.get("usage") or {}),
            receipt=receipt, choices=[{"index": 0, "text": text}], raw=raw,
            latency_ms=(time.monotonic() - started) * 1000.0)

    # -- helpers ---------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> "InferenceClient":
        """``LOCAL_INFERENCE_URL`` (default http://127.0.0.1:8100)."""
        return cls(os.environ.get(_ENV_URL), **kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"InferenceClient({self.base_url!r}, timeout={self.timeout})"


def generate(messages_or_prompt: Any, *, url: str | None = None,
             **kwargs: Any) -> GenerationResult:
    """Module-level one-shot call, for tools that do not want to hold a client."""
    return InferenceClient(url).generate(messages_or_prompt, **kwargs)
