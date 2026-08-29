"""Benchmark local models on the actual decompilation task.

Not generic coding benchmarks -- the real job: given target MIPS assembly and
an m2c draft, produce C that compiles under IDO 5.3 -O2 and matches byte for
byte. Scored by the repo's own per-function oracle (`build.sh`), which reports
a percentage and an exact-match verdict.

CONTAMINATION CONTROL, and it matters here more than usual:

    The workspace's generated `ctx.c` contains the preprocessed translation
    unit -- INCLUDING the target function's own source. Feeding it to a model
    on an already-matched function hands over the answer and produces a
    meaningless 100%. This is harmless in the upstream workflow (there the
    function is genuinely undone) but fatal for benchmarking.

    So the prompt carries only: target assembly, the m2c draft, and the task
    rules. `base.c` is safe -- m2c derives it from the assembly, and its output
    demonstrably differs from ground truth. Nothing else from the repo is
    included, and `assert_uncontaminated` re-checks that at runtime rather
    than trusting this comment.

Run (from WSL, with ollama reachable on the Windows host):
    python3 -m eval.bench_models --repo ~/decomp/sbk1 \
        --functions getRaceItemEffectType,setBootFadeColor \
        --models qwen2.5-coder:14b,gpt-oss:20b
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GLABEL_RE = re.compile(r"^glabel\s+(\w+)\s*$", re.MULTILINE)
SCORE_RE = re.compile(r"^Score:\s*([\d.]+)%", re.MULTILINE)
EXACT_RE = re.compile(r"^Verified exact match:\s*(\w+)", re.MULTILINE)
FENCE_RE = re.compile(r"```(?:c|cpp)?\s*\n(.*?)```", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?</think>|<analysis>.*?</analysis>", re.DOTALL | re.IGNORECASE)

PROMPT = """\
You are decompiling a Nintendo 64 game compiled with IDO 5.3 at -O2 for MIPS.

Write C that compiles to assembly matching the TARGET exactly.

Rules:
- Output ONE self-contained C file in a single ```c code block. No prose.
- It may only #include "common.h".
- "common.h" ALREADY defines the primitive types: u8, s8, u16, s16, u32, s32,
  u64, s64, f32, f64. Do NOT redeclare or typedef any of them -- redeclaring
  is a compile error.
- Supply any OTHER types (game structs, unions, enums) and every extern
  declaration INLINE in the file.
- C89 only: declare all variables at the start of a function or block.
- Do NOT write the `do` keyword -- the build rejects the token outright.
  For a POST-TESTED loop (body runs once before the condition is tested) write
      for (;;) { body; if (!cond) break; }
  which compiles to the same shape and DOES match. Do NOT reach for
  `while (cond) { body }` there: it adds a loop-entry test, so an extra branch,
  and cannot match. One exception: if the body needs `continue`, this rewrite
  changes behaviour (continue would skip the test), so restructure instead.
- No inline assembly, GLOBAL_ASM, or INCLUDE_ASM.
- Source shape decides codegen. An intermediate local variable, the choice of
  for/while, and whether a value is s16 or s32 all change the output. If the
  draft's shape does not produce the target, restructure it.

TARGET ASSEMBLY:
```
{asm}
```

M2C DRAFT (an automated first pass; often does not compile and often has the
wrong types or shape):
```c
{draft}
```

Produce the corrected C file now.
"""


@dataclass
class Result:
    model: str
    function: str
    compiled: bool
    score: float
    exact: bool
    tok_s: float
    gen_tokens: int
    wall_s: float
    error: str = ""


def sh(cmd: str, cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def ollama_host() -> str:
    """WSL reaches the Windows ollama through the default gateway."""
    rc, out = sh("ip route show default | awk '{print $3}'")
    gw = out.strip().splitlines()[0] if out.strip() else "127.0.0.1"
    return f"http://{gw}:11434"


def bootstrap(repo: Path, func: str) -> Path:
    ws = repo / "nonmatchings" / func
    if not (ws / "target.s").exists():
        rc, out = sh(f". .venv/bin/activate && ./tools/claude --bootstrap-only {func}",
                     cwd=repo, timeout=600)
        if not (ws / "target.s").exists():
            raise RuntimeError(f"bootstrap failed for {func}:\n{out[-800:]}")
    return ws


def target_asm(ws: Path, func: str) -> str:
    """Just the function body, without the macro preamble target.s carries."""
    text = (ws / "target.s").read_text(errors="replace")
    start = text.find(f"glabel {func}")
    end = text.find(f"endlabel {func}")
    if start == -1:
        raise RuntimeError(f"glabel {func} not found in target.s")
    return text[start:end if end != -1 else None].strip()


def assert_uncontaminated(prompt: str, repo: Path, func: str) -> None:
    """Fail loudly if the real source leaked into the prompt.

    Checks the function's actual body from src/ against the prompt. A cheap
    guard, but the failure it prevents -- a fabricated 100% -- would silently
    invalidate every number the benchmark produces.
    """
    rc, out = sh(f"grep -rn --include=*.c -A 8 '^[a-zA-Z_].*{func}(' src/ | head -20",
                 cwd=repo)
    for line in out.splitlines():
        body = line.split(":", 2)[-1].strip()
        # Look for distinctive statement lines, not the signature itself.
        if len(body) > 25 and body.endswith(";") and body in prompt:
            raise RuntimeError(
                f"CONTAMINATION: ground-truth line leaked into prompt for {func}:\n"
                f"  {body}")


def generate(host: str, model: str, prompt: str, timeout: int,
             num_thread: int = 0, num_gpu: int = -1,
             num_predict: int = 4096, think: str = "") -> tuple[str, dict]:
    """Generate one completion.

    `num_thread` caps CPU threads so the desktop keeps some cores. `num_gpu`
    caps how many layers are offloaded to the GPU -- lowering it moves work to
    the CPU, which is the only way short of a power limit to stop inference
    saturating the card.

    `num_predict` must be generous for reasoning models. They return their
    trace in a separate `thinking` field, and the trace spends the SAME token
    budget as the answer: at 1400 tokens gpt-oss:20b burned the entire budget
    reasoning and returned an empty `response` on every function. That reads as
    "the model failed" when the model was never allowed to answer.

    `think` ("low" / "medium" / "high" / "false") bounds that trace directly
    where a model supports it.
    """
    options = {"temperature": 0.2, "num_predict": num_predict, "num_ctx": 8192}
    if num_thread:
        options["num_thread"] = num_thread
    if num_gpu >= 0:
        options["num_gpu"] = num_gpu

    body = {"model": model, "prompt": prompt, "stream": False, "options": options}
    if think:
        body["think"] = False if think == "false" else think

    def post(payload: dict) -> dict:
        req = urllib.request.Request(f"{host}/api/generate",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    try:
        data = post(body)
    except urllib.error.HTTPError as exc:
        # Non-reasoning models reject `think` with a 400. Drop it and retry
        # rather than scoring the model zero for a flag it never asked for.
        if exc.code == 400 and "think" in body:
            body.pop("think")
            data = post(body)
        else:
            raise

    text = data.get("response", "") or ""
    if not text.strip():
        # Budget exhausted mid-trace. The answer, if any, is in the trace.
        text = data.get("thinking", "") or ""
        data["_fell_back_to_thinking"] = True
    return text, data


def extract_c(text: str) -> str:
    """Pull the C file out of a model response.

    Reasoning models (gpt-oss and friends) emit thinking before the answer,
    often with their own fenced blocks containing fragments and discarded
    attempts. Taking the longest fence picks the essay; taking the last fence
    that actually looks like a compilable file picks the answer. Getting this
    wrong shows up as "no text symbols" -- a harness failure that reads exactly
    like a model failure.
    """
    text = THINK_RE.sub("", text)

    candidates = [f.strip() for f in FENCE_RE.findall(text)]
    if not candidates:
        return text.strip()

    def looks_like_file(code: str) -> bool:
        # A function definition: a signature followed by a brace-delimited body.
        return bool(re.search(r"\w+\s*\([^;]*\)\s*\{", code))

    real = [c for c in candidates if looks_like_file(c)]
    return (real[-1] if real else max(candidates, key=len))


def score_attempt(ws: Path, repo: Path, name: str, code: str) -> tuple[bool, float, bool, str]:
    path = ws / f"{name}.c"
    path.write_text(code)
    rc, out = sh(f". {repo}/.venv/bin/activate && ./build.sh {name}.c", cwd=ws, timeout=300)

    m = SCORE_RE.search(out)
    if not m:
        first_err = next((l for l in out.splitlines() if "Error" in l or "ERROR" in l), "")
        return False, 0.0, False, first_err[:160]

    exact = bool(EXACT_RE.search(out) and EXACT_RE.search(out).group(1) == "yes")
    return True, float(m.group(1)), exact, ""


def run(repo: Path, functions: list[str], models: list[str], timeout: int,
        pace: float = 0.0, num_thread: int = 0, num_gpu: int = -1,
        num_predict: int = 4096, think: str = "") -> list[Result]:
    host = ollama_host()
    print(f"ollama: {host}", flush=True)
    if pace:
        print(f"pacing: {pace}s idle between generations", flush=True)
    print(flush=True)

    # Prepare every prompt up front so the model loop never waits on bootstrap.
    prepared = []
    for func in functions:
        ws = bootstrap(repo, func)
        asm = target_asm(ws, func)
        draft = (ws / "base.c").read_text(errors="replace") if (ws / "base.c").exists() else ""
        prompt = PROMPT.format(asm=asm, draft=draft)
        assert_uncontaminated(prompt, repo, func)
        prepared.append((func, ws, asm, prompt))

    results: list[Result] = []

    # Models OUTER, functions inner. The reverse forces ollama to swap a 9GB
    # and a 13GB model in and out of VRAM on every call -- minutes of pure
    # reload that inflates wall-clock and tells you nothing about the model.
    for model in models:
        # Load the model before timing anything. A 13GB load inside the first
        # request blew the timeout last run and poisoned its tok/s -- that is a
        # measure of disk speed, not of the model.
        print(f"### {model}", flush=True)
        t_load = time.time()
        try:
            generate(host, model, "int main(void){return 0;}", timeout,
                     num_thread, num_gpu, 64, think)
            print(f"  (warm-up load: {time.time() - t_load:.1f}s)", flush=True)
        except Exception as exc:
            print(f"  warm-up failed, skipping model: {exc}", flush=True)
            continue

        for func, ws, asm, prompt in prepared:
            tag = re.sub(r"[^a-z0-9]+", "_", model.lower())
            t0 = time.time()
            try:
                text, meta = generate(host, model, prompt, timeout, num_thread,
                                      num_gpu, num_predict, think)
            except Exception as exc:
                results.append(Result(model, func, False, 0.0, False, 0.0, 0, 0.0,
                                      f"generate failed: {exc}"))
                print(f"  {func:34} GENERATE FAILED: {exc}", flush=True)
                continue
            wall = time.time() - t0

            gen_tokens = meta.get("eval_count", 0)
            eval_ns = meta.get("eval_duration", 0) or 1
            tok_s = gen_tokens / (eval_ns / 1e9)

            compiled, score, exact, err = score_attempt(ws, repo, f"bench_{tag}",
                                                        extract_c(text))
            results.append(Result(model, func, compiled, score, exact, tok_s,
                                  gen_tokens, wall, err))

            verdict = "EXACT" if exact else (f"{score:6.2f}%" if compiled else "no compile")
            print(f"  {func:34} {verdict:12} {tok_s:5.1f} tok/s  {wall:5.1f}s"
                  + (f"  [{err}]" if err else ""), flush=True)

            # Idle gap so the desktop compositor gets the GPU back between
            # generations. Costs wall-clock on batch work and nothing else.
            if pace:
                time.sleep(pace)
        print(flush=True)

    return results


def report(results: list[Result]) -> None:
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    models = sorted({r.model for r in results})
    print(f"{'model':24} {'exact':>6} {'compiled':>9} {'mean score':>11} {'tok/s':>7}")
    print("-" * 72)
    for m in models:
        rs = [r for r in results if r.model == m]
        exact = sum(1 for r in rs if r.exact)
        comp = sum(1 for r in rs if r.compiled)
        mean = sum(r.score for r in rs) / len(rs) if rs else 0.0
        tps = sum(r.tok_s for r in rs) / len(rs) if rs else 0.0
        print(f"{m:24} {exact:3}/{len(rs):<2} {comp:6}/{len(rs):<2} "
              f"{mean:10.2f}% {tps:7.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--functions", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds to idle between generations, so the desktop "
                         "gets the GPU back on batch runs")
    ap.add_argument("--num-thread", type=int, default=0,
                    help="cap CPU threads (0 = ollama default). Leaves cores "
                         "free on a 16-thread box.")
    ap.add_argument("--num-gpu", type=int, default=-1,
                    help="cap GPU-offloaded layers (-1 = all). Lower values "
                         "move work to CPU and reduce GPU saturation.")
    ap.add_argument("--num-predict", type=int, default=4096,
                    help="token budget; reasoning models spend it on their "
                         "trace before answering, so keep it generous")
    ap.add_argument("--think", default="",
                    help="reasoning effort for models that support it: "
                         "low|medium|high|false")
    args = ap.parse_args()

    results = run(args.repo.expanduser(),
                  [f.strip() for f in args.functions.split(",") if f.strip()],
                  [m.strip() for m in args.models.split(",") if m.strip()],
                  args.timeout, args.pace, args.num_thread, args.num_gpu,
                  args.num_predict, args.think)
    report(results)


if __name__ == "__main__":
    main()
