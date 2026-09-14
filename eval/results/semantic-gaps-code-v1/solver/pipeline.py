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

- PERMUTE above 95% is this legacy controller's budget heuristic, not a tool
  capability boundary. Upstream also mutates types and source structure.
  In our measured case, unlockRelocatableHeapBlock sat at 99.167% because the model
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
from solver import (llm, modelrepair, refine, repair, shaped_flywheel, siblings,
                    workspace)

PERMUTE_FLOOR = 95.0   # legacy budget heuristic; not a capability boundary
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
# classes of mismatch. This controller reserves the permuter for allocation
# polish and routes other residuals to their specialized repair owners.
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


def triage(score: float, exact: bool = False) -> str:
    """Fallback routing when no verdict is available.

    Score is not an exact-match oracle. A non-exact 100 means the instruction
    view matched while object bytes (usually relocations) did not.
    """
    if exact:
        return "matched"
    if score >= 100.0:
        return "relocation"
    if score >= PERMUTE_FLOOR:
        return "permute"
    if score >= RETYPE_FLOOR:
        return "retype"
    return "reshape"


def route_for(verdict: str, score: float, exact: bool = False) -> str:
    """Verdict decides; score is only the fallback.

    A `words-identical` candidate scores ~100% and must NOT go to the permuter:
    its instructions already match and only the relocations are wrong.
    """
    if exact:
        return "matched"
    if verdict:
        # Mixed verdicts read like "mixed(structural:4, register:8)".
        for key, route in VERDICT_ROUTE.items():
            if verdict.startswith(key):
                return route
    return triage(score, exact=exact)


def build_prompt(repo: Path, conn, func: str, asm: str, draft: str,
                 route: str, use_siblings: bool,
                 historical_siblings: bool = False,
                 sibling_sources: dict[str, str] | None = None,
                 sibling_library: dict[str, object] | None = None) -> str:
    """Assemble context appropriate to the route.

    Everything here is derived from the binary or from other already-matched
    functions. The target's own source is never read.
    """
    kb = kb_context.for_function(conn, func)
    hints = hints_for_asm(asm)          # includes decoded array strides

    sib = ""
    if use_siblings and route in ("retype", "reshape"):
        if sibling_sources is not None and sibling_library is not None:
            raise ValueError("legacy and shaped sibling pools are mutually exclusive")
        if historical_siblings and sibling_sources is not None:
            raise ValueError("a frozen sibling pool cannot be combined with "
                             "reference-history siblings")
        if historical_siblings and sibling_library is not None:
            raise ValueError("a shaped sibling pool cannot be combined with "
                             "reference-history siblings")
        top = 2 if route == "reshape" else 1
        if sibling_library is not None:
            sib = shaped_flywheel.context_block(
                repo, conn, func, sibling_library, top=top)
        else:
            sources = (None if historical_siblings else sibling_sources
                       if sibling_sources is not None else siblings.verified_sources(conn))
            sib = siblings.context_block(repo, func, top=top,
                                         historical=historical_siblings,
                                         sources=sources)

    return refine.FIRST_PROMPT.format(asm=asm, draft=draft, kb=kb,
                                      hints=hints + sib)


def run_permuter(repo: Path, func: str, source: Path, seconds: int,
                 ws: Path | None = None,
                 conn=None) -> tuple[float, bool, str]:
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
    # Permuter writes winners to nonmatchings/<func>-N/output-<score>-<n>/source.c.
    # A function can have several historical runs.  Lexical order selects the
    # oldest run first and can spend the entire re-verification budget on stale
    # candidates, silently ignoring what this invocation just produced.  New
    # output files have the newest mtimes, so consume those first.
    candidates = list((repo / "nonmatchings").glob(
        f"{func}-*/output-*/source.c"))
    candidates.sort(
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )
    for i, cand in enumerate(candidates[:8]):
        try:
            code = cand.read_text(errors="replace")
        except OSError:
            continue
        att = workspace.score(
            ws, repo, f"permcheck_{i}", code,
            conn=conn, func=func, strategy="permuter-reverify",
            extra={"candidate_path": str(cand), "budget_seconds": seconds},
        )
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
          verbose: bool = True, historical_siblings: bool = False,
          sibling_sources: dict[str, str] | None = None,
          sibling_library: dict[str, object] | None = None,
          model_repair_draws: int = 0, model_repair_depth: int = 2,
          model_repair_beam: int = 4) -> Outcome:
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
    best_attempt = None
    best_receipt_id = None
    # `model` may name several proposers, comma-separated. With a perfect
    # verifier, running more than one is FREE: the oracle cannot be fooled, so
    # a weaker model that succeeds on DIFFERENT functions is strictly additive.
    # Measured 2026-08-29 over 8 functions: gpt-oss:20b 23.81, qwen2.5-coder:14b
    # 18.00, UNION 32.86 -- the union beats the better model alone by 38%, and
    # qwen scored 68.27 on a function where gpt-oss scored 0.
    models = [m.strip() for m in model.split(",") if m.strip()] or [model]

    for i in range(1, samples + 1):
        t_gen = time.time()
        # GROUPED by model, not interleaved. gpt-oss:20b is 13.8GB and
        # qwen2.5-coder:14b is 9GB against 16GB of VRAM, so they cannot both be
        # resident: alternating per sample forces ollama to evict and reload on
        # EVERY sample. Round-robin made the union thrash, and calls failed
        # during the swap. Grouping costs one swap per function instead of one
        # per sample.
        per = max(1, samples // len(models))
        this_model = models[min((i - 1) // per, len(models) - 1)]
        # A failing SAMPLE must not abandon the FUNCTION. A union run died on
        # all 38 functions with draws=0 because qwen2.5-coder rejects `think`
        # with a 400 on one endpoint, and the unhandled exception discarded the
        # gpt-oss sample that had already succeeded. An infrastructure failure
        # on one proposer is not a result for the whole function.
        try:
            text, meta = llm.generate(endpoint, this_model, prompt,
                                      timeout=timeout, think=think,
                                      num_thread=num_thread,
                                      temperature=SAMPLE_TEMP, prefill=PREFILL)
        except Exception as exc:
            out.errors = getattr(out, "errors", 0) + 1
            if verbose:
                print(f"    sample {i} ({this_model}) failed: "
                      f"{type(exc).__name__}: {str(exc)[:80]}", flush=True)
            continue
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
        receipt_id = refine.log_attempt(
            conn, addr, func, i, code, prompt, att, meta,
            "pipeline-sample", this_model, wall_ms,
            temperature=SAMPLE_TEMP, run_id=run_id, raw_response=text,
            run_kind="pipeline")
        if (best_attempt is None
                or (att.compiled and not best_attempt.compiled)
                or att.score > out.best_score):
            out.best_score, out.best_code = att.score, code
            best_attempt = att
            best_receipt_id = receipt_id
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
    # router hands it to allocation search without resolving symbol identity.
    # The workbench names that case outright
    # (`words-identical` -> relocation-only), so the verdict decides the route
    # and the score is only a fallback.
    dx, verdict = "", ""
    if best_obj is not None and best_obj.exists():
        d = diagnose.run(repo, ws / "target.o", best_obj)
        if d is not None:
            verdict = d.verdict
            dx = diagnose.prompt_block(repo, d, asm_len=len(asm))
            out.stages.append((f"verdict:{verdict}", out.best_score))

    # --- deterministic repair, BEFORE any second model call -------------
    # The compiler-directed path is the default. Every match this project has
    # gained since the corpus was built came from a deterministic repair driven
    # by the oracle's own residual, and none of that machinery was reachable
    # from here: pipeline imported only context, diagnose, llm, refine,
    # siblings and workspace, so the passes that produced the matches lived in
    # evaluation scripts and never ran in production.
    #
    # The model is for supplying a new SHAPE. It should be asked only once the
    # deterministic rewrites have reached a fixed point, because a second
    # sample costs seconds of GPU and a composition costs a few compiles.
    if out.best_code and not out.exact:
        try:
            r_att, r_src, r_log = repair.search(
                repo, func, out.best_code, ws, conn=conn, verbose=False,
                parent_attempt_id=best_receipt_id, run_id=run_id)
            if r_att.compiled and r_att.score > out.best_score:
                out.best_score, out.best_code = r_att.score, r_src
                best_attempt = r_att
                best_receipt_id = r_att.receipt_id or best_receipt_id
                out.stages.append(("repair", r_att.score))
                if verbose:
                    print(f"    repair -> {r_att.score:.2f}%"
                          f"{' EXACT' if r_att.exact else ''}", flush=True)
            if r_att.exact:
                out.exact = True
                out.route = "repair"
                out.wall_s = time.time() - t0
                return out
        except Exception as exc:               # never let repair kill a run
            if verbose:
                print(f"    repair -> skipped ({type(exc).__name__})",
                      flush=True)

    # --- bounded local-model repair, after deterministic fixed point -----
    # This is opt-in until an A/B run shows positive exact-match lift. The
    # model proposes one small structured edit; the compiler/oracle controls
    # admission, parent retention and success. It is therefore a branching
    # repair search, not the regression-prone "rewrite your last answer" loop.
    if (model_repair_draws > 0 and out.best_code and not out.exact
            and best_attempt is not None):
        try:
            mr = modelrepair.search(
                repo, func, out.best_code, ws, model=models[0],
                endpoint=endpoint, conn=conn, base_attempt=best_attempt,
                parent_attempt_id=best_receipt_id, diagnosis=dx,
                draws=model_repair_draws, max_depth=model_repair_depth,
                beam_width=model_repair_beam, timeout=timeout, think=think,
                num_thread=num_thread, run_id=f"{run_id}-modelrepair",
                verbose=verbose)
            out.tokens += mr.tokens
            out.generations += mr.generations
            out.stages.append(("model-repair", mr.best_attempt.score))
            if (mr.best_attempt.compiled
                    and (not best_attempt.compiled
                         or mr.best_attempt.score > out.best_score)):
                out.best_score, out.best_code = (mr.best_attempt.score,
                                                 mr.best_source)
                best_attempt = mr.best_attempt
                best_receipt_id = mr.best_attempt.receipt_id or best_receipt_id
            if mr.exact:
                out.exact = True
                out.route = "model-repair"
                out.wall_s = time.time() - t0
                return out
        except Exception as exc:       # one optional stage cannot kill a run
            out.errors = getattr(out, "errors", 0) + 1
            if verbose:
                print(f"    model-repair -> skipped ({type(exc).__name__})",
                      flush=True)

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
        p_score, p_exact, p_code = run_permuter(
            repo, func, rel, permute_s, ws=ws, conn=conn)
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
                               use_siblings, historical_siblings,
                               sibling_sources, sibling_library) + dx
        workspace.assert_uncontaminated(prompt2, repo, func)
        for i in range(1, samples + 1):
            t_gen = time.time()
            # `model` may be a comma-separated LIST; passing it whole sent
            # ollama the literal string "gpt-oss:20b,qwen2.5-coder:14b" as a
            # model name, which is a 400 on every reshape sample. The guard
            # above turned that into a silent errors+=1 rather than a crash,
            # which is why it survived a smoke test that otherwise looked fine.
            stage_model = models[min((i - 1) // per, len(models) - 1)]
            try:
                text, meta = llm.generate(endpoint, stage_model, prompt2,
                                          timeout=timeout, think=think,
                                          num_thread=num_thread,
                                          temperature=SAMPLE_TEMP,
                                          prefill=PREFILL)
            except Exception:
                out.errors = getattr(out, "errors", 0) + 1
                continue
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
    ap.add_argument("--model-repair-draws", type=int, default=0,
                    help="structured edit proposals per parent (0 disables)")
    ap.add_argument("--model-repair-depth", type=int, default=2)
    ap.add_argument("--model-repair-beam", type=int, default=4)
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
                      args.think, args.num_thread,
                      model_repair_draws=args.model_repair_draws,
                      model_repair_depth=args.model_repair_depth,
                      model_repair_beam=args.model_repair_beam)
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
