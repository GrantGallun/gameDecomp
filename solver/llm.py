"""Ollama client for the solver.

Local inference: the refine loop makes many calls per function, so throughput
matters more than peak capability, and per-call cost must be zero.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request

FENCE_RE = re.compile(r"```(?:c|cpp)?\s*\n(.*?)```", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?</think>|<analysis>.*?</analysis>",
                      re.DOTALL | re.IGNORECASE)
FUNC_DEF_RE = re.compile(r"\w+\s*\([^;]*\)\s*\{")


def host() -> str:
    """WSL reaches the Windows ollama through the default gateway."""
    out = subprocess.run(["bash", "-lc", "ip route show default | awk '{print $3}'"],
                         capture_output=True, text=True).stdout.strip()
    gw = out.splitlines()[0] if out else "127.0.0.1"
    return f"http://{gw}:11434"


def generate(endpoint: str, model: str, prompt: str, timeout: int = 900,
             num_thread: int = 0, num_gpu: int = -1,
             num_predict: int = 6000, think: str = "",
             temperature: float = 0.2) -> tuple[str, dict]:
    """One completion. Returns (text, raw response metadata).

    `num_predict` must be generous for reasoning models: they return the trace
    in a separate `thinking` field but it spends the SAME budget as the answer.
    Too small a budget and the model never gets to answer at all, which reads
    as a model failure when it is a configuration failure.
    """
    # Context must hold the prompt AND the answer AND, for reasoning models,
    # the trace -- they all share this budget. At 8192 large functions were
    # silently truncated mid-statement and scored 0: a 300-instruction target
    # plus KB facts and siblings leaves too little room to answer. That made
    # richer context actively harmful on exactly the functions it should help
    # most, which read as "the model cannot do large functions" when it was
    # never allowed to finish one.
    #
    # Sized from the prompt rather than fixed, because KV cache costs VRAM and
    # small functions do not need the headroom.
    needed = len(prompt) // 3 + num_predict + 1024
    ctx = min(32768, max(8192, 1 << (needed - 1).bit_length()))

    options = {"temperature": temperature, "num_predict": num_predict,
               "num_ctx": ctx}
    if num_thread:
        options["num_thread"] = num_thread
    if num_gpu >= 0:
        options["num_gpu"] = num_gpu

    body = {"model": model, "prompt": prompt, "stream": False, "options": options}
    if think:
        body["think"] = False if think == "false" else think

    def post(payload: dict) -> dict:
        req = urllib.request.Request(f"{endpoint}/api/generate",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def attempt(payload: dict) -> dict:
        """One call, with `think` dropped if the model rejects it."""
        try:
            return post(payload)
        except urllib.error.HTTPError as exc:
            # Non-reasoning models reject `think` with a 400. Drop it and retry
            # rather than scoring the model zero for a flag it never asked for.
            if exc.code == 400 and "think" in payload:
                payload.pop("think")
                return post(payload)
            raise

    # Ollama intermittently returns 5xx or stalls -- a trivial 5-token prompt
    # was once measured at 76s, and a single transient 500 previously killed a
    # whole experiment. A server hiccup must not be recorded as a model
    # failure, so retry transient errors with backoff. 4xx is not retried: that
    # is a bad request and repeating it changes nothing.
    delay = 2.0
    for remaining in range(3, 0, -1):
        try:
            data = attempt(dict(body))
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or remaining == 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if remaining == 1:
                raise
        time.sleep(delay)
        delay *= 2

    text = data.get("response", "") or ""
    if not text.strip():
        text = data.get("thinking", "") or ""
        data["_fell_back_to_thinking"] = True
    return text, data


def tokens_per_second(meta: dict) -> float:
    return meta.get("eval_count", 0) / ((meta.get("eval_duration", 0) or 1) / 1e9)


def extract_c(text: str) -> str:
    """Pull the C file out of a model response.

    Reasoning models emit fenced fragments and discarded attempts inside their
    trace. Taking the longest fence picks the essay; taking the last fence that
    actually contains a function definition picks the answer.
    """
    text = THINK_RE.sub("", text)
    candidates = [f.strip() for f in FENCE_RE.findall(text)]
    if not candidates:
        return text.strip()
    real = [c for c in candidates if FUNC_DEF_RE.search(c)]
    return real[-1] if real else max(candidates, key=len)
