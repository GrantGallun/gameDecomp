"""Re-run the never-compiled admission population against the current pipeline.

Why this exists
---------------
`eval/results/admission-20260916/RESULT.md` measured the largest single failure population in this
project: functions that have never compiled a single attempt -- more than twice the live residual
set. Every one of their attempts falls between 2026-08-27 and 2026-09-06, and none has been
attempted since 2026-09-07, while the pipeline changed underneath them (`solver/compile_chain.py`,
`solver/llm.py`, the do-while lowering, and the PREFILL refusal fix).

That RESULT's own recommendation was: "Fix the factory's `prefill` and re-run the 199. They are ten
days stale... Cheapest action available, and it needs no new engineering."
`eval/trajectory_factory.py:504` calls `generate()` with no `prefill`; every other call site passes
it. This harness passes `pipeline.PREFILL` on every draw.

Two things this does that the throwaway probes under `.cache/admission/` did not:

1. **It logs.** `workspace.score` is called with `conn` and `func`, so every attempt -- including
   every failure -- lands in the knowledge base with its strategy, model, prompt, compiler stderr
   and raw response. `CLAUDE.md` requires it, and `solver/workspace.py:470` records why: an entire
   day of ~250 generations once left zero rows and could not be recovered. An unlogged hill-climb is
   exactly the data shape `TRAINING.md` calls unusable.
2. **It uses the measured model union.** `solver/pipeline.py:266` measured, over 8 functions:
   gpt-oss:20b 23.81, qwen2.5-coder:14b 18.00, UNION 32.86 -- a 38% gain at no extra oracle cost,
   because the verifier cannot be fooled and a weaker model that succeeds on DIFFERENT functions is
   strictly additive. Draws are grouped BY MODEL so Ollama does not evict and reload between
   requests (13.8GB + 9GB against 16GB of VRAM).

Invariants honoured
-------------------
- The target's own source is never read. `workspace.assert_uncontaminated` is called on every
  prompt and raises rather than sending a leaked body.
- `declarations` is OFF by default, so a resulting match is not silently promoted into the
  header-assisted tier that `CLAUDE.md` requires be reported separately from SOLVED.
- The population is "already attempted, never compiled", which excludes every never-touched
  held-out function by construction.
- Resumable and budgeted: state is written after every function, so it is safe to interrupt.

Usage
-----
    python3 -m eval.admission_rerun --db /home/grant/decomp/kb-sbk1.sqlite \
        --repo /home/grant/decomp/sbk1 --out eval/results/admission-rerun-<date> \
        [--max N] [--seconds S] [--draws D] [--models a,b] [--declarations]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import admission_triage                                    # noqa: E402
from solver import c89, compile_chain, llm, pipeline, workspace       # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = "/home/grant/decomp/sbk1"
DEFAULT_MODELS = "gpt-oss:20b,qwen2.5-coder:14b"
ROUTE = "reshape"          # the route `solver/pipeline.py:256` uses for pass 1 of a fresh run


def tokens_of(meta: dict) -> int:
    """Charged tokens, defensively: the metadata key has moved before."""
    total = 0
    for key in ("eval_count", "prompt_eval_count"):
        value = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(value, int):
            total += value
    return total


def load_state(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    """Atomic, and it must never raise: this runs after every function.

    A serialisation error here would lose the whole run, so an unserialisable value is replaced by
    its repr rather than allowed to propagate. That is not defensive habit -- `Attempt` objects and
    arbitrary fixer reports both flowed into this dict while the compile ladder was being wired in.
    """
    try:
        encoded = json.dumps(state, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        encoded = json.dumps(state, indent=2, sort_keys=True, default=repr)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(encoded, encoding="utf-8")
    tmp.replace(path)


def attempt_one(conn, repo: Path, endpoint: str, model: str, func: str, *,
                temperature: float, declarations: bool, draws: int,
                timeout: int = 900, trace: bool = True, chain: bool = True) -> dict:
    """One function: draft, prompt, generate, extract, compile, log. Never raises.

    `trace` prints a step marker before each stage. Without it a hang is unattributable: a stalled
    run produced no output at all, and the step that wedged could not be told from a slow model call,
    a slow m2c invocation, or a blocking workspace flock. `workspace.bootstrap` shells out with a
    hardcoded `timeout=900`, and `_workspace_lock` blocks on `flock` with NO timeout at all, so not
    every stage is bounded by `--timeout`.
    """
    def step(message: str) -> None:
        if trace:
            print(f"    . {message}", flush=True)

    row: dict = {"function": func, "model": model, "draws": [], "status": "ok"}
    t_start = time.time()
    try:
        step("bootstrap")
        ws = workspace.bootstrap(repo, func)
    except Exception as exc:                                        # noqa: BLE001
        row["status"] = "bootstrap-failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    try:
        step("target_asm")
        asm = workspace.target_asm(ws, func)
    except Exception as exc:                                        # noqa: BLE001
        row["status"] = "no-target-asm"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    step("m2c_draft")
    draft = workspace.m2c_draft(ws) or ""
    step(f"m2c_draft done ({len(draft)} chars)")
    try:
        step("build_prompt")
        prompt = pipeline.build_prompt(repo, conn, func, asm, draft, ROUTE,
                                       use_siblings=False, declarations=declarations)
        workspace.assert_uncontaminated(prompt, repo, func)
        step(f"build_prompt done ({len(prompt)} chars)")
    except Exception as exc:                                        # noqa: BLE001
        row["status"] = "prompt-failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    # Function-scope, because a refusal or an unextractable response `continue`s past the variant
    # block entirely -- so these must exist even when no candidate was ever compiled. A smoke test
    # caught exactly that as an UnboundLocalError on the first function that refused.
    last_attempt = None
    last_variant = "raw"

    for draw in range(draws):
        t_draw = time.time()
        try:
            # `transport_attempts=1` deliberately. `llm.generate` retries a timeout three times by
            # default, so a hung call costs 3x the budget -- `timeout=240` meant twelve minutes of
            # silence, not four, which is what made the first diagnosis so slow. Transient-error
            # retries are worth having in a campaign worker; in a resumable batch the right move is
            # to record the failure and come back to it, and this harness resumes from state anyway.
            step(f"generate draw {draw} (timeout={timeout}s)")
            text, meta = llm.generate(endpoint, model, prompt, temperature=temperature,
                                      timeout=timeout, prefill=pipeline.PREFILL,
                                      transport_attempts=1)
            step(f"generate done ({len(text or '')} chars)")
        except Exception as exc:                                    # noqa: BLE001
            row["draws"].append({"draw": draw, "outcome": "generate-failed",
                                 "error": f"{type(exc).__name__}: {exc}"})
            continue
        wall_ms = int((time.time() - t_draw) * 1000)
        charged = tokens_of(meta if isinstance(meta, dict) else {})
        if llm.is_refusal(text or ""):
            # A refusal is a recorded outcome, not a crash. It is also the exact failure the
            # PREFILL fix is supposed to prevent, so it must be visible in the receipt.
            row["draws"].append({"draw": draw, "outcome": "refusal",
                                 "wall_ms": wall_ms, "tokens": charged})
            continue
        code = llm.extract_c(text or "")
        if not code:
            row["draws"].append({"draw": draw, "outcome": "no-extractable-c",
                                 "wall_ms": wall_ms, "tokens": charged})
            continue
        # Paired: score what the model wrote, then score the C89/target-linkage rewrite of it. The
        # second compile is ~2s against ~17s of generation, and the pair is the measurement -- it
        # separates "the model cannot write C that builds" from "the pipeline handed the compiler
        # C99". Receipts 31124/31125 are the motivating residual: raw is a Syntax Error, the rewrite
        # compiles at 75.833 / 85.455.
        variants = [("raw", code)]
        normalized = c89.public_definition(c89.to_c89(code), func)
        if normalized != code:
            variants.append(("c89", normalized))
        last_attempt = None
        last_variant = "raw"
        for variant, source in variants:
            try:
                step(f"score {variant}")
                att = workspace.score(ws, repo, func, source, conn=conn, func=func,
                                      strategy=f"admission-rerun:{variant}", model=model,
                                      prompt=prompt, temperature=temperature, wall_ms=wall_ms,
                                      run_id=f"admission-rerun-{int(t_start)}",
                                      token_cost=charged if variant == "raw" else 0,
                                      iteration=draw, raw_response=text or "",
                                      extract_status="ok", done_reason="",
                                      run_kind="admission-rerun")
            except Exception as exc:                                # noqa: BLE001
                row["draws"].append({"draw": draw, "outcome": "score-failed", "variant": variant,
                                     "error": f"{type(exc).__name__}: {exc}",
                                     "wall_ms": wall_ms, "tokens": charged})
                continue
            # Kept as LOCALS, never inside `draws`: the Attempt dataclass is not JSON-serialisable and
            # `draws` is written to the state file after every function.
            last_attempt, last_variant = att, variant
            row["draws"].append({
                "draw": draw, "outcome": "scored", "variant": variant,
                "compiled": bool(att.compiled), "score": float(att.score),
                "exact": bool(att.exact), "receipt_id": getattr(att, "receipt_id", None),
                "wall_ms": wall_ms, "tokens": charged if variant == "raw" else 0,
                "stderr_head": " | ".join((att.compiler_stderr or "").splitlines()[:2])[:300],
            })
            if att.compiled:
                break
        if any(d.get("compiled") for d in row["draws"]):
            # One compiling draw is the whole question this run asks. Stop paying for more.
            break

    # The deterministic ladder, applied only when the model's own output cannot be made to build.
    # `solver/compile_chain` is what the campaign runs: do-while lowering, m2c placeholder
    # declarations, and -- the rung that matters for this population -- declaring the identifiers IDO
    # reports as undefined. The admission write-up measured 1,196 occurrences of 193 undefined symbols
    # across 44 functions, all of them a missing declaration rather than a model error, so running the
    # ladder is the deterministic half of item (3) and needs no extra model call.
    if chain and not any(d.get("compiled") for d in row["draws"]) and last_attempt is not None:
        step("compile-chain")
        base_source = normalized if last_variant == "c89" else code

        def score_child(label: str, child: str):
            scored_att = workspace.score(
                ws, repo, func, child, conn=conn, func=func,
                strategy=f"admission-rerun:chain:{label}", model=model, prompt=prompt,
                temperature=temperature, wall_ms=0,
                run_id=f"admission-rerun-{int(t_start)}", token_cost=0,
                iteration=draw, raw_response="", extract_status="ok",
                done_reason="", run_kind="admission-rerun")
            row["draws"].append({
                "draw": draw, "outcome": "scored", "variant": f"chain:{label}",
                "compiled": bool(scored_att.compiled), "score": float(scored_att.score),
                "exact": bool(scored_att.exact),
                "receipt_id": getattr(scored_att, "receipt_id", None),
                "wall_ms": 0, "tokens": 0,
                "stderr_head": " | ".join(
                    (scored_att.compiler_stderr or "").splitlines()[:2])[:300],
            })
            return scored_att

        try:
            scored, chain_log = compile_chain.chain(
                func, last_variant, base_source, last_attempt,
                score_child, headers="", rounds=6, budget=10)
            # Labels only: the raw log carries whatever the individual fixers return, and this dict
            # is JSON-dumped to the state file after every function. A save that raises loses the run.
            row["chain_rounds"] = len(scored)
            row["chain_applied"] = [label for label, _code, _att in scored]
        except Exception as exc:                                    # noqa: BLE001
            row["chain_error"] = f"{type(exc).__name__}: {exc}"

    row["seconds"] = round(time.time() - t_start, 2)
    row["compiled"] = any(d.get("compiled") for d in row["draws"])
    row["exact"] = any(d.get("exact") for d in row["draws"])
    row["best_score"] = max([d.get("score", 0.0) for d in row["draws"]] or [0.0])
    return row


def summarise(rows: list[dict]) -> dict:
    compiled = [r for r in rows if r.get("compiled")]
    exact = [r for r in rows if r.get("exact")]
    refusals = [r for r in rows if any(d.get("outcome") == "refusal" for d in r.get("draws", []))]

    def compiled_as(variant: str) -> list[str]:
        return sorted(r["function"] for r in rows
                      if any(d.get("variant") == variant and d.get("compiled")
                             for d in r.get("draws", [])))

    raw_only = compiled_as("raw")
    fixed_only = sorted(set(compiled_as("c89")) - set(raw_only))
    return {
        "population": len(rows),
        "now_compile": len(compiled),
        "now_exact": len(exact),
        "refusals": len(refusals),
        "still_not_compiling": len(rows) - len(compiled),
        # The paired measurement. `fixed_only` is the C89/target-linkage hole's yield: functions the
        # model DID write buildable C for, that the pipeline was feeding to the compiler as C99.
        "compiled_raw": len(raw_only),
        "compiled_only_after_c89": len(fixed_only),
        "c89_attributable_functions": fixed_only,
        "exact_functions": sorted(r["function"] for r in exact),
        "compiled_functions": sorted(r["function"] for r in compiled),
        "status_counts": {s: sum(1 for r in rows if r.get("status") == s)
                          for s in sorted({r.get("status") for r in rows})},
    }


def write_receipt(out: Path, rows: list[dict], summary: dict, args) -> None:
    lines = [
        "# Admission re-run: the never-compiled population against the current pipeline",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M')} against `{args.db}`.",
        "",
        "## Headline",
        "",
        "| | |",
        "|---|---|",
        f"| population (already attempted, never compiled, libultra excluded) | **{summary['population']}** |",
        f"| now compiles | **{summary['now_compile']}** |",
        f"| — of which compiled as written by the model | {summary['compiled_raw']} |",
        f"| — of which compiled only after the C89/target-linkage rewrite | **{summary['compiled_only_after_c89']}** |",
        f"| now byte-exact | **{summary['now_exact']}** |",
        f"| still does not compile | {summary['still_not_compiling']} |",
        f"| refusals | {summary['refusals']} |",
        "",
        f"Models: `{args.models}`. Draws per function: {args.draws}. "
        f"Route: `{ROUTE}`. Declarations: {'ON (header-assisted tier)' if args.declarations else 'OFF'}.",
        "",
        "`eval/trajectory_factory.py:504` calls `generate()` with no `prefill`; this run passes "
        "`pipeline.PREFILL` on every draw, which `solver/llm.py:112-125` measures as 9/9 -> 0/9 "
        "refusals on functions that refuse every draw.",
        "",
    ]
    if summary["exact_functions"]:
        lines += ["## Newly byte-exact", ""]
        lines += [f"- `{name}`" for name in summary["exact_functions"]]
        lines.append("")
    if summary["c89_attributable_functions"]:
        lines += ["## The C89 / target-linkage hole, measured",
                  "",
                  "These functions the model *did* write buildable C for. The pipeline was handing "
                  "the compiler C99 (`inline`) and a linkage IDO discards (`static` with no caller), "
                  "so the object came back with either `Syntax Error` at the opening brace or "
                  "`Compiled object has no text symbols` -- and the admission triage booked both as "
                  "separate failure kinds. They are one bug with two symptoms.", ""]
        lines += [f"- `{name}`" for name in summary["c89_attributable_functions"]]
        lines.append("")
    if summary["compiled_functions"]:
        lines += ["## Newly compiling (not yet exact)", "",
                  "These are the functions the admission bucket was hiding: they were never a "
                  "representation problem, only an un-retried one.", ""]
        lines += [f"- `{name}`" for name in summary["compiled_functions"]]
        lines.append("")
    lines += [
        "## Per-function",
        "",
        "| function | compiled | exact | best | seconds | outcomes |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        outcomes = ", ".join(
            f"{d.get('variant', '')}:{d.get('outcome', '?')}"
            + (f"={d['score']:.1f}" if d.get("compiled") else "")
            for d in r.get("draws", []))
        lines.append(
            f"| `{r['function']}` | {r.get('compiled')} | {r.get('exact')} | "
            f"{r.get('best_score', 0.0):.2f} | {r.get('seconds', 0)} | {outcomes} |")
    lines += ["", "## Status / errors", ""]
    for r in rows:
        if r.get("status") != "ok" or r.get("error"):
            lines.append(f"- `{r['function']}`: {r.get('status')} {r.get('error', '')}")
    (out / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=Path(DEFAULT_REPO))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--draws", type=int, default=2)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max", type=int, default=0, help="0 = whole population")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = no wall-clock cap")
    ap.add_argument("--timeout", type=int, default=900,
                    help="per-generation timeout. Lower it to make a stalled request surface as a "
                         "recorded failure instead of a silent 15-minute gap: a hung draw and a slow "
                         "one are indistinguishable from outside, which is the 'silent decline' shape "
                         "this project keeps catching")
    ap.add_argument("--declarations", action="store_true",
                    help="OFF by default: turning it on makes matches header-assisted, "
                         "which CLAUDE.md requires be reported separately from SOLVED")
    ap.add_argument("--redo", action="store_true", help="ignore saved state")
    ap.add_argument("--no-chain", action="store_true",
                    help="skip the deterministic compile ladder. It is ON by default because that "
                         "is what the campaign runs; leaving it off would measure a pipeline that "
                         "does not exist, and the ladder is the deterministic answer to the "
                         "undefined-identifier class (1,196 occurrences of 193 symbols)")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "state.json"
    state = {} if args.redo else load_state(state_path)

    conn = sqlite3.connect(args.db, timeout=120)
    population = admission_triage.never_compiled(conn)
    population.sort(key=lambda item: item["name"])
    if args.max:
        population = population[: args.max]

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    endpoint = llm.host()
    print(f"endpoint={endpoint} models={models} population={len(population)} "
          f"already_done={len(state)}", flush=True)

    started = time.time()
    done = 0
    # Grouped BY MODEL, matching solver/pipeline.py:274 -- two models cannot both be resident on a
    # 16GB card, so interleaving them forces an evict/reload on every single draw.
    for model in models:
        for item in population:
            func = item["name"]
            key = f"{model}|{func}"
            if key in state and not args.redo:
                continue
            if args.seconds and (time.time() - started) > args.seconds:
                print(f"BUDGET: wall-clock cap {args.seconds}s reached after {done} attempts",
                      flush=True)
                break
            row = attempt_one(conn, args.repo, endpoint, model, func,
                              temperature=args.temperature,
                              declarations=args.declarations, draws=args.draws,
                              timeout=args.timeout, chain=not args.no_chain)
            state[key] = row
            save_state(state_path, state)
            done += 1
            print(f"[{done}] {func:<44} {model:<22} compiled={row.get('compiled')} "
                  f"exact={row.get('exact')} best={row.get('best_score', 0.0):.2f} "
                  f"{row.get('status')}", flush=True)
        if args.seconds and (time.time() - started) > args.seconds:
            break

    rows = list(state.values())
    summary = summarise(rows)
    (args.out / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_receipt(args.out, rows, summary, args)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
