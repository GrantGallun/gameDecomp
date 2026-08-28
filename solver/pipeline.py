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
    exact: bool = False
    best_score: float = 0.0
    best_code: str = ""
    route: str = ""
    stages: list = field(default_factory=list)
    tokens: int = 0
    wall_s: float = 0.0


def triage(score: float) -> str:
    if score >= 100.0:
        return "matched"
    if score >= PERMUTE_FLOOR:
        return "permute"
    if score >= RETYPE_FLOOR:
        return "retype"
    return "reshape"


def build_prompt(repo: Path, conn, func: str, asm: str, draft: str,
                 route: str, use_siblings: bool) -> str:
    """Assemble context appropriate to the route.

    Everything here is derived from the binary or from other already-matched
    functions. The target's own source is never read.
    """
    kb = kb_context.for_function(conn, func)
    hints = hints_for_asm(asm)          # includes decoded array strides

    sib = ""
    if use_siblings and route in ("retype", "reshape"):
        sib = siblings.context_block(repo, func, top=2 if route == "reshape" else 1)

    return refine.FIRST_PROMPT.format(asm=asm, draft=draft, kb=kb,
                                      hints=hints + sib)


def run_permuter(repo: Path, func: str, source: Path, seconds: int) -> float | None:
    """Returns the permuter's best score, or None if it found nothing."""
    rc, out = workspace.sh(
        f". .venv/bin/activate && timeout {seconds}s ./tools/permuter "
        f"--source-file {source} {func}", cwd=repo, timeout=seconds + 120)

    best = None
    for d in sorted((repo / "nonmatchings").glob(f"{func}-*/output-*")):
        try:
            best = max(best or 0.0, float(d.name.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return best


def solve(repo: Path, conn, func: str, model: str, endpoint: str,
          samples: int, permute_s: int, use_siblings: bool,
          timeout: int, think: str, num_thread: int,
          verbose: bool = True) -> Outcome:
    t0 = time.time()
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
    for i in range(1, samples + 1):
        text, meta = llm.generate(endpoint, model, prompt, timeout=timeout,
                                  think=think, num_thread=num_thread,
                                  temperature=0.7)
        code = llm.extract_c(text)
        out.tokens += meta.get("eval_count", 0)
        att = workspace.score(ws, repo, f"pipe_{i}", code)
        refine.log_attempt(conn, addr, func, i, code, prompt, att, meta,
                           "pipeline-sample", model, 0)
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

    route = triage(out.best_score)
    out.route = route
    if verbose:
        print(f"    sample -> {out.best_score:.2f}%  route={route}", flush=True)

    # --- pass 2: route-specific treatment -------------------------------
    if route == "permute" and best_file is not None and permute_s > 0:
        rel = best_file.relative_to(repo)
        got = run_permuter(repo, func, rel, permute_s)
        out.stages.append(("permute", got or out.best_score))
        if verbose:
            print(f"    permute -> {got if got else 'no improvement'}", flush=True)
        if got and got >= 100.0:
            out.exact = True
            out.best_score = 100.0

    elif route in ("retype", "reshape"):
        # Classify the mismatch instead of guessing from the score. The
        # workbench names a verdict and its playbook of levers; score alone
        # cannot distinguish a relocation error from a register-allocation one.
        dx = ""
        if best_obj is not None and best_obj.exists():
            d = diagnose.run(repo, ws / "target.o", best_obj)
            if d is not None:
                dx = diagnose.prompt_block(repo, d, asm_len=len(asm))
                out.stages.append((f"verdict:{d.verdict}", out.best_score))
                if verbose:
                    print(f"    verdict -> {d.verdict} [{d.playbook}]", flush=True)

        prompt2 = build_prompt(repo, conn, func, asm, draft, route, use_siblings) + dx
        workspace.assert_uncontaminated(prompt2, repo, func)
        for i in range(1, samples + 1):
            text, meta = llm.generate(endpoint, model, prompt2, timeout=timeout,
                                      think=think, num_thread=num_thread,
                                      temperature=0.7)
            code = llm.extract_c(text)
            out.tokens += meta.get("eval_count", 0)
            att = workspace.score(ws, repo, f"pipe_{route}_{i}", code)
            refine.log_attempt(conn, addr, func, samples + i, code, prompt2, att,
                               meta, f"pipeline-{route}", model, 0)
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
