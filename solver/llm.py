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
             prefill: str = "") -> tuple[str, dict]:
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

    gap_s, env_gpu = _throttle()
    if env_gpu >= 0:
        num_gpu = env_gpu

    options = {"temperature": temperature, "num_predict": num_predict,
               "num_ctx": ctx}
    if num_thread:
        options["num_thread"] = num_thread
    if num_gpu >= 0:
        options["num_gpu"] = num_gpu

    # A partial ASSISTANT turn stops refusals outright: 9/9 -> 0/9 measured on
    # functions that refuse every draw. It only works through /api/chat --
    # appending the same text to /api/generate puts it inside the USER message,
    # where it does nothing (18/18 still refused). Which turn it lands in is
    # the whole effect.
    if prefill:
        msgs = [{"role": "user", "content": prompt},
                {"role": "assistant", "content": prefill}]
        body = {"model": model, "messages": msgs, "stream": False,
                "options": options}
    else:
        body = {"model": model, "prompt": prompt, "stream": False,
                "options": options}
    if think:
        body["think"] = False if think == "false" else think

    def post(payload: dict) -> dict:
        path = "/api/chat" if "messages" in payload else "/api/generate"
        req = urllib.request.Request(f"{endpoint}{path}",
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

    if gap_s:
        time.sleep(gap_s)

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
    return text, data


def tokens_per_second(meta: dict) -> float:
    return meta.get("eval_count", 0) / ((meta.get("eval_duration", 0) or 1) / 1e9)


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
