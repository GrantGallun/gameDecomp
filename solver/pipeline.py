"""Triage pipeline: route each function by what is actually wrong with it.

WHY THIS SHAPE. The first design sampled uniformly and hoped. Measured on a
stratified 41-function set it scored 19.4%, with every single match in the
`tiny` tier and zero above it. Breaking the failures down by score showed they
are not one problem:

    100%      matched
    >=95%     register allocation      -> decomp-permuter
    80-95%    wrong types / struct size -> stride + KB facts + siblings
    <80%      wrong overall shape       -> mirror a matched sibling

Those need different tools, and the score says which. Treating them uniformly
means applying the wrong tool three times out of four.

The evidence for each route:

- PERMUTE only above 95%. The project's own skill notes say the permuter
  moves register allocation and never control flow or types. Confirmed the
  hard way: unlockRelocatableHeapBlock sat at 99.167% because the model
  declared an 18-byte struct against a real 20-byte one, and 300s of permuting
  moved it nowhere. A near-miss is not automatically a polish job.

- RETYPE in the 80-95% band, because that band is dominated by type errors and
  the stride is arithmetically decodable from the target's own index
  arithmetic (`sll 2; addu; sll 2` is unambiguously x20).

- RESHAPE below 80% by mirroring a matched sibling, which the project's
  learnings call the fastest path to a match.

Humans do not one-shot any of this either: 4,015 commits over three months,
full of "Improve X match to 90.704%" then "to 96.626%" then "to 99.210%".
Converging over several passes is the normal case, so a function that is not
solved is PARKED with its best attempt rather than counted as lost.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import diagnose
from solver import llm, refine, siblings, workspace

PERMUTE_FLOOR = 95.0   # below this the permuter cannot help
RETYPE_FLOOR = 80.0    # below this the shape itself is wrong


@dataclass
class Outcome:
    function: str
    verdict: str = ""          # workbench verdict, or "" if none was obtained
    exact: bool = False
    best_score: float = 0.0
    best_code: str = ""
    route: str = ""
    stages: list = field(default_factory=list)
    tokens: int = 0
    generations: int = 0
    wall_s: float = 0.0


# A workbench verdict beats a score band, because the score is blind to whole
# classes of mismatch. Only `allocation` and `register-permutation` are things
# the permuter can actually move; everything else needs a source change.
VERDICT_ROUTE = {
    "allocation": "permute",
    "allocation-mismatch": "permute",
    "register-permutation": "permute",
    "phase-shift": "permute",
    # Instructions are right, symbols are wrong. Permuting cannot help.
    "words-identical": "relocation",
    "relocation-layout-mismatch": "relocation",
    "unknown-relocation": "relocation",
    # Source-shape problems.
    "frame-layout": "retype",
    "frame-layout-mismatch": "retype",
    "constant": "retype",
    "constant-mismatch": "retype",
    "operand-mismatch": "retype",
    "commutative-order": "retype",
    "schedule": "retype",
    "schedule-mismatch": "retype",
    "structure": "reshape",
    "structure-mismatch": "reshape",
}


def triage(score: float) -> str:
    """Fallback routing when no verdict is available."""
    if score >= 100.0:
        return "matched"
    if score >= PERMUTE_FLOOR:
        return "permute"
    if score >= RETYPE_FLOOR:
        return "retype"
    return "reshape"


def route_for(verdict: str, score: float) -> str:
    """Verdict decides; score is only the fallback.

    A `words-identical` candidate scores ~100% and must NOT go to the permuter:
    its instructions already match and only the relocations are wrong.
    """
    if score >= 100.0:
        return "matched"
    if verdict:
        # Mixed verdicts read like "mixed(structural:4, register:8)".
        for key, route in VERDICT_ROUTE.items():
            if verdict.startswith(key):
                return route
    return triage(score)


def build_prompt(repo: Path, conn, func: str, asm: str, draft: str,
                 route: str, use_siblings: bool,
                 historical_siblings: bool = False) -> str:
    """Assemble context appropriate to the route.

    Everything here is derived from the binary or from other already-matched
    functions. The target's own source is never read.
    """
    kb = kb_context.for_function(conn, func)
    hints = hints_for_asm(asm)          # includes decoded array strides

    sib = ""
    if use_siblings and route in ("retype", "reshape"):
        sib = siblings.context_block(repo, func,
                                     top=2 if route == "reshape" else 1,
                                     historical=historical_siblings)

    return refine.FIRST_PROMPT.format(asm=asm, draft=draft, kb=kb,
                                      hints=hints + sib)


def run_permuter(repo: Path, func: str, source: Path, seconds: int,
                 ws: Path | None = None) -> tuple[float, bool, str]:
    """Run the permuter and RE-VERIFY every candidate through the oracle.

    Returns (best_score, exact, best_code).

    The previous version parsed a score out of the permuter's output DIRECTORY
    NAME and declared exact at >= 100. That is a false-positive generator and it
    violates the project's foundational invariant: byte-exact object comparison
    is the only source of truth. A fabricated EXACT is worse than a missed
    match -- it poisons every downstream number, and the ratchet would lock it
    in as ground truth.

    A directory name is a claim. `workspace.score` is the verdict.
    """
    workspace.sh(
        f". .venv/bin/activate && timeout {seconds}s ./tools/permuter "
        f"--source-file {source} {func}", cwd=repo, timeout=seconds + 120)

    if ws is None:
        return 0.0, False, ""

    best_score, best_exact, best_code = 0.0, False, ""
    # Permuter writes winners to nonmatchings/<func>-N/output-<score>-<n>/source.c
    for i, cand in enumerate(sorted((repo / "nonmatchings").glob(
            f"{func}-*/output-*/source.c"))[:8]):
        try:
            code = cand.read_text(errors="replace")
        except OSError:
            continue
        att = workspace.score(ws, repo, f"permcheck_{i}", code)
        if att.score > best_score:
            best_score, best_exact, best_code = att.score, att.exact, code
        if att.exact:
            break

    return best_score, best_exact, best_code


SAMPLE_TEMP = 0.7

# A partial assistant turn. Measured on six functions that refused every
# draw: refusals 17/18 -> 0/18, compiling candidates 0 -> 5, max score
# 71.5%. Without it those functions are structurally excluded.
PREFILL = '```c\n#include "common.h"\n'


def solve(repo: Path, conn, func: str, model: str, endpoint: str,
          samples: int, permute_s: int, use_siblings: bool,
          timeout: int, think: str, num_thread: int,
          verbose: bool = True, historical_siblings: bool = False) -> Outcome:
    t0 = time.time()
    run_id = f"{int(t0)}-{func}"
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    draft = workspace.m2c_draft(ws)
    addr = refine.func_addr(conn, func)

    out = Outcome(function=func)

    # --- pass 1: sample with full context -------------------------------
    prompt = build_prompt(repo, conn, func, asm, draft, "reshape", use_siblings=False)
    workspace.assert_uncontaminated(prompt, repo, func)

    best_file = None
    best_obj = None
    # `model` may name several proposers, comma-separated. With a perfect
    # verifier, running more than one is FREE: the oracle cannot be fooled, so
    # a weaker model that succeeds on DIFFERENT functions is strictly additive.
    # Measured 2026-08-29 over 8 functions: gpt-oss:20b 23.81, qwen2.5-coder:14b
    # 18.00, UNION 32.86 -- the union beats the better model alone by 38%, and
    # qwen scored 68.27 on a function where gpt-oss scored 0.
    models = [m.strip() for m in model.split(",") if m.strip()] or [model]

    for i in range(1, samples + 1):
        t_gen = time.time()
        this_model = models[(i - 1) % len(models)]
        text, meta = llm.generate(endpoint, this_model, prompt, timeout=timeout,
                                  think=think, num_thread=num_thread,
                                  temperature=SAMPLE_TEMP, prefill=PREFILL)
        wall_ms = int((time.time() - t_gen) * 1000)
        code = llm.extract_c(text)
        if llm.is_refusal(code) or llm.is_refusal(text):
            # An abstention is not an attempt. Compiling it produces a syntax
            # error that then reads as a model failure and drags every mean
            # down -- 54% of a real eval run, measured 2026-08-28.
            out.refusals = getattr(out, "refusals", 0) + 1
            continue
        out.tokens += meta.get("eval_count", 0)
        out.generations += 1
        att = workspace.score(ws, repo, f"pipe_{i}", code)
        refine.log_attempt(conn, addr, func, i, code, prompt, att, meta,
                           "pipeline-sample", this_model, wall_ms,
                           temperature=SAMPLE_TEMP, run_id=run_id, raw_response=text)
        if att.score > out.best_score:
            out.best_score, out.best_code = att.score, code
            best_file = ws / f"pipe_{i}.c"
            best_obj = ws / f"pipe_{i}.o"
        if att.exact:
            out.exact = True
            break

    out.stages.append(("sample", out.best_score))
    if out.exact:
        out.route = "matched"
        out.wall_s = time.time() - t0
        if verbose:
            print(f"    sample -> EXACT", flush=True)
        return out

    # Diagnose FIRST, for every compiled non-exact candidate. Score alone
    # cannot see a relocation error: a candidate with zero instruction
    # differences and a wrong symbol reference scores ~100% and a score-band
    # router hands it to the permuter, which only permutes register allocation
    # and can never touch a symbol. The workbench names that case outright
    # (`words-identical` -> relocation-only), so the verdict decides the route
    # and the score is only a fallback.
    dx, verdict = "", ""
    if best_obj is not None and best_obj.exists():
        d = diagnose.run(repo, ws / "target.o", best_obj)
        if d is not None:
            verdict = d.verdict
            dx = diagnose.prompt_block(repo, d, asm_len=len(asm))
            out.stages.append((f"verdict:{verdict}", out.best_score))

    route = route_for(verdict, out.best_score)
    out.route = route
    # Record the verdict separately. `reshape` is also the score<80 fallback,
    # so the route alone cannot distinguish "the workbench said structure-
    # mismatch" from "no verdict was available and the score was low".
    out.verdict = verdict
    if verbose:
        v = f" verdict={verdict}" if verdict else ""
        print(f"    sample -> {out.best_score:.2f}%{v}  route={route}", flush=True)

    # --- pass 2: route-specific treatment -------------------------------
    if route == "permute" and best_file is not None and permute_s > 0:
        rel = best_file.relative_to(repo)
        p_score, p_exact, p_code = run_permuter(repo, func, rel, permute_s, ws=ws)
        out.stages.append(("permute", p_score))
        if verbose:
            state = "EXACT" if p_exact else (f"{p_score:.2f}%" if p_score
                                             else "no improvement")
            print(f"    permute -> {state} (oracle-verified)", flush=True)
        # Only the oracle may declare a match.
        if p_score > out.best_score:
            out.best_score, out.best_code = p_score, p_code
        if p_exact:
            out.exact = True

    elif route in ("retype", "reshape", "relocation"):
        # `dx` was already produced by the diagnosis above; reuse it rather
        # than paying for a second workbench run.
        ctx_route = "retype" if route == "relocation" else route
        prompt2 = build_prompt(repo, conn, func, asm, draft, ctx_route,
                               use_siblings, historical_siblings) + dx
        workspace.assert_uncontaminated(prompt2, repo, func)
        for i in range(1, samples + 1):
            t_gen = time.time()
            text, meta = llm.generate(endpoint, model, prompt2, timeout=timeout,
                                      think=think, num_thread=num_thread,
                                      temperature=SAMPLE_TEMP,
                                      prefill=PREFILL)
            wall_ms = int((time.time() - t_gen) * 1000)
            code = llm.extract_c(text)
            if llm.is_refusal(code) or llm.is_refusal(text):
                out.refusals = getattr(out, "refusals", 0) + 1
                continue
            out.tokens += meta.get("eval_count", 0)
            out.generations += 1
            att = workspace.score(ws, repo, f"pipe_{route}_{i}", code)
            refine.log_attempt(conn, addr, func, samples + i, code, prompt2, att,
                               meta, f"pipeline-{route}", model, wall_ms,
                               temperature=SAMPLE_TEMP, run_id=run_id, raw_response=text)
            if att.score > out.best_score:
                out.best_score, out.best_code = att.score, code
            if att.exact:
                out.exact = True
                break
        out.stages.append((route, out.best_score))
        if verbose:
            print(f"    {route} -> "
                  f"{'EXACT' if out.exact else f'{out.best_score:.2f}%'}", flush=True)

    out.wall_s = time.time() - t0
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--functions", required=True)
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("-n", "--samples", type=int, default=3)
    ap.add_argument("--permute-seconds", type=int, default=180)
    ap.add_argument("--siblings", action="store_true",
                    help="include matched sibling sources (report runs using "
                         "this separately; a close twin carries real signal)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--think", default="low")
    ap.add_argument("--num-thread", type=int, default=12)
    args = ap.parse_args()

    repo = args.repo.expanduser()
    conn = sqlite3.connect(args.db.expanduser())
    refine.ensure_schema(conn)
    endpoint = llm.host()

    results = []
    for func in [f.strip() for f in args.functions.split(",") if f.strip()]:
        print(f"=== {func} ===", flush=True)
        try:
            r = solve(repo, conn, func, args.model, endpoint, args.samples,
                      args.permute_seconds, args.siblings, args.timeout,
                      args.think, args.num_thread)
        except Exception as exc:
            print(f"    ERROR: {exc}", flush=True)
            continue
        results.append(r)
        print(f"  -> {'EXACT' if r.exact else f'best {r.best_score:.2f}%'} "
              f"[{r.route}] {r.wall_s:.0f}s\n", flush=True)

    exact = sum(1 for r in results if r.exact)
    print("=" * 64)
    print(f"exact: {exact}/{len(results)}")
    by_route = {}
    for r in results:
        by_route.setdefault(r.route, []).append(r)
    for route, rs in sorted(by_route.items()):
        e = sum(1 for r in rs if r.exact)
        print(f"  {route:9} {len(rs):3} functions, {e} matched")


if __name__ == "__main__":
    main()
