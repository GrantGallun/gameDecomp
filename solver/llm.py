"""Ollama client for the solver.

Local inference: the refine loop makes many calls per function, so throughput
matters more than peak capability, and per-call cost must be zero.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
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


def _read_body(resp, deadline: float, started: float) -> bytes:
    """`resp.read()` bounded by a WALL-CLOCK deadline, not by the socket timeout.

    `urllib.request.urlopen(req, timeout=T)` sets a PER-SOCKET-OPERATION timeout. It bounds connect
    and each individual recv, not the request. A server that sends one byte inside every T-second
    window therefore keeps `resp.read()` alive indefinitely -- and the parameter reads as though it
    were an overall bound. The transport event even called it `socket_timeout_seconds`, which was the
    code knowing and the callers not: `solver/pipeline.py`, `eval/trajectory_factory.py` and every
    pilot pass a `timeout` and reasonably expect a wall clock.

    Measured cost, 2026-09-16: a re-run of the never-compiled admission bucket wedged inside a model
    call for over twenty minutes, twice, with `timeout=300` and then `--timeout 240`, producing no
    output at all. It was indistinguishable from a slow function, a slow `m2c` invocation, or a
    blocking workspace flock until a step trace was added. The campaign runs three workers behind one
    inference slot, so one hung call there parks a worker indefinitely.

    Chunked reads make the deadline observable: if data trickles, the clock ends it; if data stops
    entirely, the socket timeout still does.
    """
    buf = bytearray()
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(
                "model request exceeded its wall-clock budget of "
                f"{deadline - started:.0f}s (socket timeout is per-operation, not total)")
        chunk = resp.read(65536)
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)


def _throttle() -> tuple[float, int]:
    """Opt-in limits so the machine stays usable while a run is going.

    The GPU sits at ~99% during generation, which is what makes video stutter;
    CPU priority barely touches that. Two knobs, both no-ops unless set, so
    nothing changes for existing runs:

      SOLVER_GAP_MS   idle milliseconds after every call, giving the compositor
                      a regular window instead of a solid block of work.
      SOLVER_NUM_GPU  layers to keep on the GPU. Lower means more of the model
                      runs on CPU: markedly slower, but it stops the GPU being
                      saturated. Try ~24 of 33 before going lower.

    Deliberately env-driven rather than arguments: throttling is a property of
    the machine at that moment, not of the experiment, and it must never end up
    baked into a recorded run configuration where it could look like a variable
    under test.
    """
    import os
    gap = float(os.environ.get("SOLVER_GAP_MS", "0") or 0) / 1000.0
    ngpu = os.environ.get("SOLVER_NUM_GPU", "")
    return gap, (int(ngpu) if ngpu.strip() else -1)


def generate(endpoint: str, model: str, prompt: str, timeout: int = 900,
             num_thread: int = 0, num_gpu: int = -1,
             num_predict: int = 6000, think: str = "",
             temperature: float = 0.2,
             prefill: str = "", *, seed: int | None = None,
             cache_dir: str | Path | None = None,
             cache_namespace: str = "", cache_only: bool = False,
             response_schema: dict | None = None, transport_attempts: int = 3,
             num_ctx: int | None = None
             ) -> tuple[str, dict]:
    """One completion. Returns (text, raw response metadata).

    `timeout` is an overall WALL-CLOCK budget for the request. It used to be handed straight to
    `urlopen`, where it is only a per-socket-operation timeout, so a server that trickled any byte
    inside each window could hold a call open forever -- see `read_body`.

    `num_predict` must be generous for reasoning models: they return the trace
    in a separate `thinking` field but it spends the SAME budget as the answer.
    Too small a budget and the model never gets to answer at all, which reads
    as a model failure when it is a configuration failure.
    """
    if not 1 <= transport_attempts <= 3:
        raise ValueError('transport_attempts must be between one and three')
    # GPT-OSS ignores boolean thinking controls; never silently request its
    # default effort when a caller intended a short emission phase.
    # https://docs.ollama.com/capabilities/thinking
    if think == "false" and model.lower().rsplit("/", 1)[-1].startswith("gpt-oss"):
        think = "low"
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
    from solver.prompt_budget import context_budget
    budget = context_budget(prompt, num_predict, num_ctx=num_ctx, prefill=prefill,
                            response_schema=response_schema)
    ctx = budget['capacity']
    # A dedicated campaign server must keep one allocation across requests:
    # switching num_ctx can unload/reload the whole model on Ollama. Never let
    # an explicit allocation reduce the existing prompt/answer headroom.

    gap_s, env_gpu = _throttle()
    if env_gpu >= 0:
        num_gpu = env_gpu

    options = {"temperature": temperature, "num_predict": num_predict,
               "num_ctx": ctx}
    if seed is not None:
        options["seed"] = int(seed)
    if num_thread:
        options["num_thread"] = num_thread
    if num_gpu >= 0:
        options["num_gpu"] = num_gpu

    # A partial ASSISTANT turn stops refusals outright: 9/9 -> 0/9 measured on
    # functions that refuse every draw. It only works through /api/chat --
    # appending the same text to /api/generate puts it inside the USER message,
    # where it does nothing (18/18 still refused). Which turn it lands in is
    # the whole effect.
    if prefill or response_schema is not None:
        msgs = [{"role": "user", "content": prompt}]
        if prefill:
            msgs.append({"role": "assistant", "content": prefill})
        body = {"model": model, "messages": msgs, "stream": False,
                "options": options}
    else:
        body = {"model": model, "prompt": prompt, "stream": False,
                "options": options}
    if think:
        body["think"] = False if think == "false" else think
    if response_schema is not None:
        body["format"] = response_schema

    # Seeded generations may be cached; unseeded ones never are. Prompt-only
    # caching would turn repeated stochastic draws into duplicate evidence and
    # silently fabricate sample size. Include every request setting that can
    # change output, not just the minimum tuple in the S2 pre-registration.
    cache_path = None
    cache_material = None
    cache_key = ""
    if cache_dir is not None:
        if seed is None:
            raise ValueError("generation caching requires an explicit seed")
        cache_material = {
            "schema_version": 1,
            "namespace": cache_namespace,
            "endpoint": endpoint,
            "api": "/api/chat" if "messages" in body else "/api/generate",
            "model": model,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prefill_sha256": hashlib.sha256(prefill.encode()).hexdigest(),
            "think": think,
            "options": options,
        }
        if response_schema is not None:
            cache_material["response_schema"] = response_schema
        encoded = json.dumps(
            cache_material, sort_keys=True, separators=(",", ":")).encode()
        cache_key = hashlib.sha256(encoded).hexdigest()
        cache_root = Path(cache_dir).expanduser()
        cache_path = cache_root / cache_key[:2] / f"{cache_key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("key_material") != cache_material:
                raise ValueError(f"generation cache key mismatch: {cache_path}")
            text = cached.get("text")
            meta = cached.get("meta")
            if not isinstance(text, str) or not isinstance(meta, dict):
                raise ValueError(f"invalid generation cache entry: {cache_path}")
            meta = dict(meta)
            meta['_cached_transport_events'] = meta.get('_transport_events', [])
            meta['_transport_events'] = []
            meta.update({"_cache_hit": True, "_cache_key": cache_key,
                         "_cache_path": str(cache_path), "_seed": seed})
            return text, meta
        if cache_only:
            raise FileNotFoundError(
                f"generation cache miss in cache-only mode: {cache_path}")
    elif cache_only:
        raise ValueError("cache-only generation requires cache_dir")

    transport_events = []

    def post(payload: dict) -> dict:
        path = "/api/chat" if "messages" in payload else "/api/generate"
        req = urllib.request.Request(f"{endpoint}{path}",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        started = time.monotonic()
        event = {'attempt':len(transport_events)+1,'socket_timeout_seconds':timeout,
                 'wall_clock_budget_seconds':timeout,
                 'think_present':'think' in payload}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(_read_body(resp, started + timeout, started))
        except Exception as exc:
            event.update(status='error',error_type=type(exc).__name__,
                         elapsed_seconds=time.monotonic()-started)
            if isinstance(exc,urllib.error.HTTPError):
                event['http_status'] = exc.code
            transport_events.append(event)
            exc.transport_events = list(transport_events)
            raise
        event.update(status='response',elapsed_seconds=time.monotonic()-started)
        transport_events.append(event)
        return result

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
    for remaining in range(transport_attempts, 0, -1):
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

    if gap_s:
        time.sleep(gap_s)

    data['_transport_events'] = transport_events
    data['_request_options'] = dict(options)
    data['_prompt_sha256'] = hashlib.sha256(prompt.encode()).hexdigest()
    budget = dict(budget)
    if isinstance(data.get('prompt_eval_count'), int):
        budget['actual_prompt_tokens'] = data['prompt_eval_count']
        budget['actual_output_headroom'] = ctx - data['prompt_eval_count']
        budget['actual_allowance_fits'] = budget['actual_output_headroom'] >= num_predict
    data['_context_budget'] = budget

    if "message" in data:                      # /api/chat shape
        msg = data.get("message") or {}
        text = msg.get("content") or ""
        if not text.strip():
            text = msg.get("thinking") or ""
            data["_fell_back_to_thinking"] = True
        # the prefill is not echoed back; without it the opening fence is
        # missing and extract_c sees an unterminated block
        text = prefill + text
    else:
        text = data.get("response", "") or ""
        if not text.strip():
            text = data.get("thinking", "") or ""
            data["_fell_back_to_thinking"] = True
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"key_material": cache_material, "text": text,
                   "meta": data}
        temporary = cache_path.with_name(
            f".{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(cache_path)
        data = dict(data)
        data.update({"_cache_hit": False, "_cache_key": cache_key,
                     "_cache_path": str(cache_path), "_seed": seed})
    return text, data


def tokens_per_second(meta: dict) -> float:
    return meta.get("eval_count", 0) / ((meta.get("eval_duration", 0) or 1) / 1e9)


# --- generation receipts ------------------------------------------------------

# The receipt is a DATA STRUCTURE, not a convention, because every previous attempt
# to carry generation provenance as "the caller will remember to pass it" lost it.
# `OllamaGenerator.sample` returned `list[str]` and dropped the meta dict on the
# floor; `WorkspaceScorer.score` therefore had nothing to store; and the result was
# 29 `factory-refine` attempts with no prompt, no model and no raw response -- real
# work that cannot be audited or repurposed. A generator that cannot describe what
# it asked and what came back is not a component of a data factory.
#
# `status` is the reconciliation key. Every model call ends in exactly one of these,
# and a call that is not `ok` still has a receipt: an errored or refused call is
# evidence about the target, not an absence of evidence.

RECEIPT_STATUSES = ("ok", "refusal", "no-extract", "error", "timeout", "empty")

# Top-level response keys worth keeping verbatim. Ollama adds timing and token
# counters; naming them is what makes token accounting possible downstream. The
# full response is still retained in `raw_meta` -- this list is a convenience
# projection, never the source of truth.
_META_TOKEN_KEYS = (
    "eval_count", "prompt_eval_count", "eval_duration", "prompt_eval_duration",
    "total_duration", "load_duration", "done_reason", "done",
)


@dataclass
class GenerationReceipt:
    """Everything one model call must leave behind, in one object.

    `prompt` is stored IN FULL rather than as a hash alone. A hash proves a prompt
    is unchanged and proves nothing about its content; the experiment needs to read
    back what the model was actually asked, and reconstructing it later from a
    workspace that has since been rebuilt is not reconstruction, it is a guess.
    """

    prompt: str
    raw_response: str
    status: str = "ok"
    model: str = ""
    digest: str = ""
    sampling: dict = field(default_factory=dict)
    token_cost: int = 0
    wall_ms: int = 0
    error: str = ""
    extracted: str = ""
    extract_status: str = ""
    done_reason: str = ""
    raw_meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in RECEIPT_STATUSES:
            raise ValueError(
                f"unknown receipt status {self.status!r}; every call must "
                f"reconcile to one of {RECEIPT_STATUSES}")

    @property
    def produced_c(self) -> bool:
        return bool(self.extracted)

    def as_sampling(self) -> dict:
        """The projection stored in `attempts.sampling` / `model_proposals.sampling`."""
        out = {"status": self.status}
        if self.digest:
            out["model_digest"] = self.digest
        if self.done_reason:
            out["done_reason"] = self.done_reason
        if self.extract_status:
            out["extract_status"] = self.extract_status
        if self.token_cost:
            out["token_cost"] = self.token_cost
        out["wall_ms"] = self.wall_ms
        return out

    def as_row(self) -> dict:
        """A JSON-safe row for a durable proposal record."""
        return {
            "status": self.status,
            "model": self.model,
            "model_digest": self.digest,
            "prompt_sha256": hashlib.sha256(self.prompt.encode("utf-8")).hexdigest(),
            "raw_response_sha256": hashlib.sha256(
                (self.raw_response or "").encode("utf-8")).hexdigest(),
            "raw_response_bytes": len((self.raw_response or "").encode("utf-8")),
            "extracted_sha256": (hashlib.sha256(self.extracted.encode("utf-8")).hexdigest()
                                 if self.extracted else None),
            "extract_status": self.extract_status,
            "done_reason": self.done_reason,
            "sampling": dict(self.sampling),
            "token_cost": self.token_cost,
            "wall_ms": self.wall_ms,
            "error": self.error[:1000],
            "meta": {k: self.raw_meta.get(k) for k in _META_TOKEN_KEYS
                     if k in self.raw_meta},
        }


def classify_generation(text: str, extracted: str, error: str = "") -> str:
    """One terminal status per call, in priority order.

    Priority matters and is not arbitrary: an error outranks a refusal (the model
    was never asked), a refusal outranks an extraction failure (there was never
    anything to extract), and `empty` is distinguished from `no-extract` because a
    model that says nothing at all and a model that answers in the wrong shape have
    different cures.
    """
    if error:
        return "timeout" if "timeout" in error.lower() else "error"
    if not (text or "").strip():
        return "empty"
    if is_refusal(text):
        return "refusal"
    if not extracted:
        return "no-extract"
    return "ok"


def generation_receipt(text: str, meta: dict, *, prompt: str, model: str,
                       extracted: str, sampling: dict | None = None,
                       wall_ms: int = 0, error: str = "",
                       timeout_seconds: int | None = None) -> GenerationReceipt:
    """Assemble a receipt from a `generate` result, classifying the outcome.

    `wall_ms` is supplied by the caller because only the caller has the clock that
    spans the retry loop; `generate` reports per-transport-attempt timings.
    """
    meta = meta or {}
    error_text = error
    if not error_text and timeout_seconds is not None:
        # A wall-clock overrun is not a model failure and must be labelled as a
        # timeout so it is not averaged into a quality metric.
        events = meta.get("_transport_events") or []
        if events and events[-1].get("status") == "error" and not meta.get("done"):
            error_text = ""
    status = classify_generation(text, extracted, error_text)
    sampling_out = dict(sampling or {})
    sampling_out.update(meta.get("_request_options") or {})
    return GenerationReceipt(
        prompt=prompt,
        raw_response=text or "",
        status=status,
        model=model,
        digest=str(meta.get("model") or ""),
        sampling=sampling_out,
        token_cost=int(meta.get("eval_count") or 0),
        wall_ms=int(wall_ms),
        error=error_text,
        extracted=extracted or "",
        extract_status=classify_extraction(text or "", extracted or ""),
        done_reason=str(meta.get("done_reason") or ""),
        raw_meta=meta,
    )


# A candidate is assembly, not C, if it is mostly MIPS instruction lines.
ASM_LINE = re.compile(r"^\s*(/\*[^*]*\*/)?\s*[a-z][a-z0-9.]{1,7}\s+\$\w+", re.M)
OPEN_FENCE = re.compile(r"```(?:c|cpp)?[ \t]*\n")


def _looks_like_asm(block: str) -> bool:
    """True when the block is disassembly the model echoed back.

    32 stored 'sources' were literally the target assembly. The old fallback
    chose max(candidates, key=len), and the echoed assembly is always the
    longest block -- so the fallback reliably picked the one thing that can
    never compile.
    """
    lines = [l for l in block.splitlines() if l.strip()]
    if not lines:
        return False
    return len(ASM_LINE.findall(block)) >= max(3, len(lines) // 3)


REFUSAL_RE = re.compile(
    r"\b(i'm sorry|i’m sorry|i am sorry|i cannot|i can't|i can’t|"
    r"cannot provide|can't provide|can’t provide|cannot assist|"
    r"can't help with|unable to provide)\b", re.I)


def is_refusal(text: str) -> bool:
    """True when the text is an abstention rather than a candidate.

    Measured 2026-08-28 on a real eval run: 84 of 157 attempts (54%) stored
    refusal prose as their "source", and ALL 84 were handed to the compiler.
    "I'm sorry, but I can't provide that." became a syntax error, which became
    a model failure with score 0, which dragged the reported mean down.

    CLAUDE.md already requires refusals to be excluded from failure statistics.
    Nothing implemented it, so every aggregate reported so far is computed over
    a denominator that is mostly non-attempts.

    Checked against the head of the text only: real C can legitimately contain
    the word "cannot" in a comment further down.
    """
    return bool(REFUSAL_RE.search((text or "")[:300]))


def classify_extraction(text: str, extracted: str) -> str:
    """Why extraction produced what it did -- stored so failures stay legible.

    Only the POST-extraction source was ever kept, so when extraction failed
    there was no way to tell a refusal from a truncation from a format slip.
    13.8% of all compile failures lived in exactly that blind spot, and the fix
    for it had to be validated against a proxy instead of real output.
    """
    low = text.lower()[:400]
    if any(w in low for w in ("i'm sorry", "i’m sorry", "cannot provide",
                              "can't provide", "can’t provide",
                              "can't produce")):
        return "refusal"
    if not extracted:
        return "empty"
    if extracted.lstrip().startswith("```"):
        return "fence"
    if _looks_like_asm(extracted):
        return "asm"
    if "```" in text and not re.search(r"```(?:c|cpp)?\s*\n[\s\S]*?```", text):
        return "unterminated"
    return "ok"


# Includes the build cannot resolve. Measured on a real run: 20 of 38
# unclassified compile failures were "Cannot open file X for #include" --
# stddef.h, common_structs.h, gbi.h, stdint.h, global.h, menu.h, Gfx.h. The
# prompt already forbids them and the model ignores it; stripping needs no
# cooperation. Anything those headers would define is unavailable regardless,
# which is precisely why the include fails, so nothing is lost by removing it.
BAD_INCLUDE = re.compile(r'^[ \t]*#\s*include\s*[<"]([^>"]+)[>"][^\n]*\n?',
                         re.M)


def strip_unresolvable_includes(code: str) -> str:
    """Keep common.h; drop every other #include."""
    def keep(m: "re.Match[str]") -> str:
        name = m.group(1).strip().lower()
        return m.group(0) if name.endswith("common.h") else ""
    return BAD_INCLUDE.sub(keep, code)


def extract_c(text: str) -> str:
    """Pull the C file out of a model response, or return "" if there is none.

    Returning "" matters: it lets the caller record an EXTRACTION FAILURE
    instead of handing the compiler garbage and booking the result as a model
    error. 13.8% of all compile failures were this -- fences, echoed assembly,
    and truncated output scored as if the model had written bad C.

    Reasoning models emit fenced fragments and discarded attempts inside their
    trace, so the last fence containing a function definition is the answer.
    """
    text = THINK_RE.sub("", text)
    candidates = [f.strip() for f in FENCE_RE.findall(text)]

    # A truncated generation has an opening fence and no closing one, so
    # FENCE_RE matches nothing and the old code returned the whole response --
    # leading ``` included, which the compiler reports as "Unknown character `".
    if not candidates:
        m = OPEN_FENCE.search(text)
        if m:
            candidates = [text[m.end():].strip()]
        else:
            candidates = [text.strip()]

    candidates = [c for c in candidates if c and not _looks_like_asm(c)]
    if not candidates:
        return ""

    real = [c for c in candidates if FUNC_DEF_RE.search(c)]
    best = real[-1] if real else max(candidates, key=len)

    # Belt and braces: never hand a stray fence marker to the compiler.
    best = strip_unresolvable_includes(best)
    best = re.sub(r"^```(?:c|cpp)?[ \t]*\n?", "", best)
    best = re.sub(r"\n?```\s*$", "", best)
    return best.strip()
