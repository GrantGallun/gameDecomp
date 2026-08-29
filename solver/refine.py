"""The refine loop: propose, compile, diff, feed back, repeat.

The whole thesis in one file. A single-shot model gets a decent fraction of
functions and misses the rest for reasons the compiler will state outright --
"line 4: Syntax Error", or an instruction diff showing exactly which registers
disagree. Feeding that back is what turns a 37% single-shot rate into
something better, and it costs nothing but tokens because the verifier is
free and exact.

Every attempt is logged to the `attempts` table, including failures and
regressions. Per TRAINING.md that table is the debugging record now and the
refinement dataset later; the (n -> n+1) pairing is what makes it training
data, so rows are never overwritten or pruned.

Run:
    python3 -m solver.refine --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \\
        --functions getRaceItemEffectType --model gpt-oss:20b --max-iters 5
"""

from __future__ import annotations

import argparse
import re
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from patterns.catalog import CATALOG, hints_for_asm
from solver import context as kb_context
from solver import llm, workspace

BASE_TEMP = 0.2

# Opcodes that signal a catalogued idiom. Only confirmed patterns are wired in:
# a hypothesis does not get to steer the solver.
PATTERN_TRIGGERS = {
    "s16-sign-extend": ("sll", "sra"),
    "signed-div-power-of-2": ("bgez", "sra"),
    "bulk-struct-copy": ("sw", "lw"),
}


def catalog_hints(diff: str) -> str:
    """Surface relevant catalog prescriptions for what the diff actually shows.

    Where there is a pattern there is a function, and where there is a function
    there is use: the catalog exists so a recognised shape in the diff can tell
    the model what SOURCE produces it, rather than leaving it to guess.
    """
    if not diff:
        return ""
    ops = set(re.findall(r"^[+-]\s*([a-z][a-z0-9.]*)", diff, re.MULTILINE))

    hints = []
    for pid, triggers in PATTERN_TRIGGERS.items():
        pattern = CATALOG.get(pid)
        if not pattern or pattern.is_hypothesis or pattern.kind != "solver":
            continue
        if all(t in ops for t in triggers):
            hints.append(f"- {pattern.means} -> {pattern.prescription}")

    if not hints:
        return ""
    return ("\nKnown IDO idioms visible in this diff:\n" + "\n".join(hints) + "\n")

# FRAMING -- and a hypothesis that was TESTED AND FAILED.
#
# gpt-oss:20b emits refusal text ("I'm sorry, but I can't provide the source
# you're looking for") on 49% of large-function and 50% of huge-function
# generations, and NEVER on tiny or small ones. Those are recorded as ~0%
# failures, so the large and huge tier numbers measure a model that declined
# half the attempts rather than one that tried and failed.
#
# The obvious hypothesis was that "You are decompiling a Nintendo 64 game"
# tripped a copyright refusal. The wording below removed that framing and was
# tested on two previously-refusing functions. IT DID NOT HELP: the model still
# refused, merely adapting its wording to the new framing ("I can't provide the
# solution to this challenge"), and one variant wrote prose explaining the
# function was "not recoverable".
#
# So this is capability capitulation wearing refusal language, not a safety
# refusal. The reframing is kept because it describes the task more accurately,
# but it fixes nothing. The contamination is real and the remedy is elsewhere:
# a model that does not bail, or a task decomposed small enough that it does
# not want to.
FIRST_PROMPT = """\
You are reconstructing the original C source of one function from its compiled
output. The compiler is IDO 5.3 targeting MIPS at -O2.

This is a compiler-behaviour exercise: the source is judged solely by whether
recompiling it reproduces the target object byte for byte.

Write C that compiles to assembly matching the TARGET exactly.

Rules:
- Output ONE self-contained C file in a single ```c code block. No prose.
- It may only #include "common.h".
- "common.h" ALREADY defines u8, s8, u16, s16, u32, s32, u64, s64, f32, f64.
  Do NOT redeclare or typedef any of them -- redeclaring is a compile error.
- Supply any OTHER types (structs, unions, enums) and every extern declaration
  INLINE. Define a type BEFORE any declaration that uses it.
- C89 only: declare all variables at the start of a function or block.
- Do NOT write the `do` keyword -- the build rejects the token outright.
  For a POST-TESTED loop (body runs once before the condition is tested) write
      for (;;) {{ body; if (!cond) break; }}
  which compiles to the same shape and DOES match. Do NOT reach for
  `while (cond) {{ body }}` there: it adds a loop-entry test, so an extra branch,
  and cannot match. One exception: if the body needs `continue`, this rewrite
  changes behaviour (continue would skip the test), so restructure instead.
- No inline assembly, GLOBAL_ASM, or INCLUDE_ASM.

WRITE SOURCE, NOT REGISTERS. The assembly is what the compiler PRODUCED; your
job is to recover what a human WROTE. These rules follow from confirmed IDO
behaviour:
- Do NOT create locals named after registers (t6, v0, a1) and assign through
  them step by step. Operate directly on the globals, parameters and struct
  fields. Register-shaped C almost never matches.
- Use compound assignment where a human would: `x++;`, `x &= 0xFF;`, `p->n--;`.
- TWO STORES to the same address with no load between them mean TWO SEPARATE
  STATEMENTS, and each must READ THE VARIABLE BACK. Write `x++;` then
  `x &= 0xFF;`. Writing `x = n; x = n & 0xFF;` instead makes the first store
  dead, IDO deletes it, and you emit one store where the target has two.
- Prefer the shortest idiomatic C that could produce the target. Original game
  code is terse; a three-line function rarely had five temporaries.
- Source shape decides codegen. An intermediate local variable, the choice of
  for/while, and whether a value is s16 or s32 all change the output.

TARGET ASSEMBLY:
```
{asm}
```

M2C DRAFT (automated first pass; often does not compile, often wrong types):
```c
{draft}
```
{kb}{hints}
Produce the corrected C file now.
"""

COMPILE_FAIL_PROMPT = """\
Your previous attempt DID NOT COMPILE.

```c
{code}
```

Compiler output:
```
{errors}
```

Fix the error and output the complete corrected C file in one ```c block.
Remember: define every type BEFORE any declaration that uses it, `common.h`
already provides the primitive types, and C89 requires declarations at the
start of a block.
"""

DIFF_PROMPT = """\
This is the BEST attempt so far, scoring {score:.2f}% against the target.
It is not yet a byte-exact match.

```c
{code}
```

Instruction diff (`-` = TARGET expects, `+` = this attempt produced):
```
{diff}
```
{history}{hints}

Revise the C so the generated instructions match exactly. Think about what
SOURCE SHAPE produces the target's instructions:
- extra sign-extension (sll 16 / sra 16) means a value should be s16, not s32
- a bias then shift (bgez / addiu 2^n-1 / sra n) is signed division `/ 2^n`,
  never `>> n`
- an intermediate local variable changes register allocation and instruction
  order; adding or removing one is often the whole difference
- loop form (for vs while) and operand order change codegen

Output the complete revised C file in one ```c block.
"""


@dataclass
class Trajectory:
    function: str
    iterations: int = 0
    best_score: float = 0.0
    best_code: str = ""
    exact: bool = False
    wall_s: float = 0.0
    tokens: int = 0
    history: list = field(default_factory=list)


def ensure_schema(conn: sqlite3.Connection) -> None:
    schema = Path(__file__).parent.parent / "kb" / "schema.sql"
    conn.executescript(schema.read_text())


def func_addr(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT addr FROM functions WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def log_attempt(conn, addr, func, i, code, prompt, att, meta, strategy, model,
                wall_ms, temperature=None, run_id=None):
    """Record one attempt. Never overwrite, never prune -- see TRAINING.md.

    The sampling parameters must be the ones actually used. This previously
    hardcoded temperature 0.2 while the pipeline sampled at 0.7, and passed
    wall_ms=0 for every pipeline attempt. TRAINING.md's premise is that this
    table becomes the refinement dataset; rows carrying the wrong sampling
    parameters and no timing are unusable for exactly that purpose, so every
    run was quietly writing damaged training data.

    `run_id` distinguishes invocations, because `iteration` restarts at 1 on
    every call and would otherwise collide across runs of the same function.
    """
    if addr is None:
        return
    sampling = {
        "temperature": temperature,
        "num_predict": meta.get("_num_predict"),
        "eval_count": meta.get("eval_count"),
        "prompt_eval_count": meta.get("prompt_eval_count"),
        "run_id": run_id,
    }
    conn.execute(
        "INSERT INTO attempts (func_addr, iteration, source_code, prompt_context,"
        " compiled, compiler_stderr, score, diff_summary, strategy, model,"
        " sampling, wall_ms, token_cost, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (addr, i, code, prompt, int(att.compiled), att.compiler_stderr,
         att.score, att.diff, strategy, model, json.dumps(sampling),
         wall_ms, meta.get("eval_count", 0), int(time.time())),
    )
    conn.commit()


def sample_one(repo: Path, conn, func: str, model: str, endpoint: str,
               n: int, timeout: int, think: str, num_thread: int,
               pace: float, temperature: float, verbose: bool = False,
               use_kb: bool = False) -> Trajectory:
    """Best-of-N: N independent samples from the same prompt, verifier picks.

    The alternative to sequential refinement, and on the evidence the better
    one. Measured across 8 functions, diff-guided refinement rescued exactly
    zero -- every match landed on the first attempt. Yet resampling the SAME
    function gave 87.61%, 82.42% and 100% on three runs, so the right answer is
    inside the model's distribution and the way to reach it is to sample again,
    not to argue with the model about a diff it cannot localise.

    This works here only because the verifier is free and exact: N candidates
    cost N generations and one build each, and the compiler settles it.
    """
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    draft = workspace.m2c_draft(ws)
    addr = func_addr(conn, func)

    kb = kb_context.for_function(conn, func) if use_kb else ""
    prompt = FIRST_PROMPT.format(asm=asm, draft=draft, kb=kb,
                                hints=hints_for_asm(asm))
    workspace.assert_uncontaminated(prompt, repo, func)

    traj = Trajectory(function=func)
    t_start = time.time()

    for i in range(1, n + 1):
        t0 = time.time()
        text, meta = llm.generate(endpoint, model, prompt, timeout=timeout,
                                  think=think, num_thread=num_thread,
                                  temperature=temperature)
        wall_ms = int((time.time() - t0) * 1000)
        code = llm.extract_c(text)
        traj.tokens += meta.get("eval_count", 0)

        att = workspace.score(ws, repo, f"sample_{i}", code)
        traj.iterations = i
        traj.history.append((i, att.compiled, att.score, "sample"))
        log_attempt(conn, addr, func, i, code, prompt, att, meta, "sample",
                    model, wall_ms)

        if att.score > traj.best_score:
            traj.best_score, traj.best_code = att.score, code

        if verbose:
            state = "EXACT" if att.exact else (f"{att.score:6.2f}%" if att.compiled
                                               else "no compile")
            print(f"    sample {i} [temp {temperature:.2f}] {state}", flush=True)

        if att.exact:
            traj.exact = True
            break
        if pace:
            time.sleep(pace)

    traj.wall_s = time.time() - t_start
    return traj


def refine_one(repo: Path, conn, func: str, model: str, endpoint: str,
               max_iters: int, timeout: int, think: str, num_thread: int,
               pace: float, verbose: bool = False) -> Trajectory:
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    draft = workspace.m2c_draft(ws)
    addr = func_addr(conn, func)

    traj = Trajectory(function=func)
    prompt = FIRST_PROMPT.format(asm=asm, draft=draft, kb="",
                                hints=hints_for_asm(asm))
    workspace.assert_uncontaminated(prompt, repo, func)

    strategy = "draft"
    t_start = time.time()

    best_att: workspace.Attempt | None = None
    best_code = ""
    no_improve = 0
    temperature = BASE_TEMP
    tried: list[tuple[int, float]] = []

    for i in range(1, max_iters + 1):
        t0 = time.time()
        text, meta = llm.generate(endpoint, model, prompt, timeout=timeout,
                                  think=think, num_thread=num_thread,
                                  temperature=temperature)
        wall_ms = int((time.time() - t0) * 1000)
        code = llm.extract_c(text)
        traj.tokens += meta.get("eval_count", 0)

        att = workspace.score(ws, repo, f"refine_{i}", code)
        traj.iterations = i
        traj.history.append((i, att.compiled, att.score, strategy))
        tried.append((i, att.score))

        log_attempt(conn, addr, func, i, code, prompt, att, meta, strategy,
                    model, wall_ms)

        improved = att.score > traj.best_score
        if improved:
            traj.best_score, traj.best_code = att.score, code
            best_att, best_code = att, code
            no_improve = 0
            temperature = BASE_TEMP
        else:
            # Same prompt at a low temperature reproduces the same answer, so
            # repeat attempts were pure waste. Widen the search instead.
            no_improve += 1
            temperature = min(0.95, BASE_TEMP + 0.25 * no_improve)

        if verbose:
            state = "EXACT" if att.exact else (f"{att.score:6.2f}%" if att.compiled
                                               else "no compile")
            flag = "" if improved or i == 1 else f"  (no gain, temp {temperature:.2f})"
            print(f"    iter {i} [{strategy:11}] {state}{flag}", flush=True)

        if att.exact:
            traj.exact = True
            break

        # Always refine from the BEST attempt, never the latest. Feeding back
        # the latest lets one bad step poison every step after it -- observed
        # as 87.61 -> 71.00 -> 23.60, a loop walking away from its own answer.
        anchor_att = best_att if best_att is not None else att
        anchor_code = best_code if best_code else code

        history = ""
        if len(tried) > 1:
            scored = ", ".join(f"#{n} scored {s:.1f}%" for n, s in tried)
            history = (f"\nAttempts so far: {scored}. Do not repeat an approach "
                       f"that already scored the same; try a different source shape.\n")

        if not anchor_att.compiled:
            prompt = COMPILE_FAIL_PROMPT.format(code=anchor_code,
                                                errors=anchor_att.compiler_stderr)
            strategy = "fix-compile"
        else:
            hints = catalog_hints(anchor_att.diff)
            prompt = DIFF_PROMPT.format(score=anchor_att.score, code=anchor_code,
                                        diff=anchor_att.diff[:4000],
                                        history=history, hints=hints)
            strategy = "fix-diff"

        if pace:
            time.sleep(pace)

    traj.wall_s = time.time() - t_start
    return traj


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--functions", required=True)
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--max-iters", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--think", default="low")
    ap.add_argument("--num-thread", type=int, default=12)
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--mode", choices=["refine", "sample"], default="refine",
                    help="refine = sequential diff feedback; "
                         "sample = best-of-N independent draws")
    ap.add_argument("--temp", type=float, default=0.7,
                    help="sampling temperature for --mode sample")
    args = ap.parse_args()

    repo = args.repo.expanduser()
    conn = sqlite3.connect(args.db.expanduser())
    ensure_schema(conn)
    endpoint = llm.host()

    funcs = [f.strip() for f in args.functions.split(",") if f.strip()]
    print(f"ollama: {endpoint}   model: {args.model}   max-iters: {args.max_iters}\n",
          flush=True)

    trajectories = []
    for func in funcs:
        print(f"=== {func} ===", flush=True)
        try:
            if args.mode == "sample":
                traj = sample_one(repo, conn, func, args.model, endpoint,
                                  args.max_iters, args.timeout, args.think,
                                  args.num_thread, args.pace, args.temp,
                                  verbose=True)
            else:
                traj = refine_one(repo, conn, func, args.model, endpoint,
                                  args.max_iters, args.timeout, args.think,
                                  args.num_thread, args.pace, verbose=True)
        except Exception as exc:
            print(f"    FAILED: {exc}", flush=True)
            continue
        trajectories.append(traj)
        verdict = "EXACT" if traj.exact else f"best {traj.best_score:.2f}%"
        print(f"  -> {verdict} in {traj.iterations} iters, "
              f"{traj.wall_s:.1f}s, {traj.tokens} tokens\n", flush=True)

    exact = sum(1 for t in trajectories if t.exact)
    n = len(trajectories)
    print("=" * 64)
    print(f"exact: {exact}/{n}" + (f"  ({100.0*exact/n:.1f}%)" if n else ""))
    if trajectories:
        solved = [t for t in trajectories if t.exact]
        if solved:
            print(f"mean iters to match : {sum(t.iterations for t in solved)/len(solved):.1f}")
        first_shot = sum(1 for t in trajectories if t.exact and t.iterations == 1)
        print(f"exact on first draw : {first_shot}")
        print(f"gained by iterating : {exact - first_shot}")
        print(f"mean best score     : {sum(t.best_score for t in trajectories)/n:.2f}%")


if __name__ == "__main__":
    main()
