"""One-function, one-draw-per-arm Ghidra context pilot.

This is intentionally not wired into the production solver.  It first asks
the existing local model with the existing prompt, then asks the same model at
temperature zero with one compact Ghidra shape hypothesis appended.  Both
candidates go through the normal byte-exact oracle and attempt log.

The fixed two-generation budget keeps a promising-looking decompile from
turning into an open-ended GPU experiment before it has demonstrated signal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

from solver import c89, ghidra_context, llm, pipeline, refine, workspace


PREFILL = '```c\n#include "common.h"\n'


def _score(repo: Path, conn, ws: Path, func: str, model: str, prompt: str,
           label: str, code: str, text: str, meta: dict,
           generation_seconds: float) -> dict:
    extract_status = llm.classify_extraction(text, code)
    run_id = f"ghidra-pilot-{int(time.time())}"
    raw = workspace.score(ws, repo, f"ghidra_pilot_{label}", code)
    refine.log_attempt(
        conn, refine.func_addr(conn, func), func, 1, code, prompt, raw, meta,
        f"ghidra-pilot-{label}", model, int(generation_seconds * 1000),
        temperature=0.0, run_id=run_id,
        raw_response=text)

    repaired_code = c89.to_c89(code)
    repaired = raw
    if repaired_code != code:
        repaired = workspace.score(
            ws, repo, f"ghidra_pilot_{label}_c89", repaired_code)
        refine.log_attempt(
            conn, refine.func_addr(conn, func), func, 1, repaired_code,
            prompt, repaired, meta, f"ghidra-pilot-{label}-c89", model,
            int(generation_seconds * 1000), temperature=0.0,
            run_id=run_id, raw_response=text)

    return {
        "arm": label,
        "prompt_chars": len(prompt),
        "response_chars": len(text),
        "extraction": extract_status,
        "raw": {
            "compiled": raw.compiled,
            "score": raw.score,
            "exact": raw.exact,
            "compiler_error": raw.compiler_stderr[:500],
        },
        "c89_repair_changed_source": repaired_code != code,
        "compiled": repaired.compiled,
        "score": repaired.score,
        "exact": repaired.exact,
        "compiler_error": repaired.compiler_stderr[:500],
        "generation_seconds": round(generation_seconds, 3),
        "generation_tokens": meta.get("eval_count", 0),
        "tokens_per_second": round(llm.tokens_per_second(meta), 2),
        "done_reason": meta.get("done_reason", ""),
    }


def _arm(repo: Path, conn, ws: Path, func: str, endpoint: str, model: str,
         prompt: str, label: str, timeout: int, think: str,
         num_thread: int) -> dict:
    started = time.perf_counter()
    text, meta = llm.generate(
        endpoint, model, prompt, timeout=timeout, think=think,
        num_thread=num_thread, num_predict=6000, temperature=0.0,
        prefill=PREFILL)
    generation_seconds = time.perf_counter() - started
    code = llm.extract_c(text)
    return _score(repo, conn, ws, func, model, prompt, label, code, text,
                  meta, generation_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--function", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--score-existing", action="store_true",
        help="skip inference and apply the normal C89 repair to the two "
             "ghidra_pilot_*.c candidates already in the workspace")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    evidence_path = args.evidence.expanduser().resolve()
    out_path = args.out or evidence_path.with_suffix(".pilot.json")
    conn = sqlite3.connect(args.db.expanduser())
    refine.ensure_schema(conn)

    ws = workspace.bootstrap(repo, args.function)
    asm = workspace.target_asm(ws, args.function)
    draft = workspace.m2c_draft(ws)
    baseline = pipeline.build_prompt(
        repo, conn, args.function, asm, draft, "reshape", False)
    enriched = baseline + ghidra_context.from_file(
        evidence_path, args.function)
    workspace.assert_uncontaminated(baseline, repo, args.function)
    workspace.assert_uncontaminated(enriched, repo, args.function)

    row = conn.execute(
        "SELECT max(a.score) FROM attempts a JOIN functions f "
        "ON f.addr=a.func_addr WHERE f.name=? AND a.compiled=1",
        (args.function,)).fetchone()
    prior_best = float(row[0] or 0.0)
    results = []
    prompts = (("baseline", baseline), ("context", enriched))
    if args.score_existing:
        for label, prompt in prompts:
            candidate = ws / f"ghidra_pilot_{label}.c"
            code = candidate.read_text(encoding="utf-8")
            result = _score(
                repo, conn, ws, args.function, args.model, prompt, label,
                code, code, {}, 0.0)
            results.append(result)
            verdict = ("EXACT" if result["exact"]
                       else f"{result['score']:.3f}%" if result["compiled"]
                       else "did not compile")
            print(f"{label}: repaired rescore {verdict}", flush=True)
    else:
        endpoint = llm.host()
        # Warm model loading out of the first arm's wall time.  The tiny request
        # is not a candidate and is therefore not scored as one.
        llm.generate(endpoint, args.model, "int main(void){return 0;}",
                     timeout=args.timeout, think=args.think,
                     num_thread=args.num_thread, num_predict=64,
                     temperature=0.0)
        for label, prompt in prompts:
            print(f"{label}: generating ({len(prompt)} prompt chars)", flush=True)
            result = _arm(repo, conn, ws, args.function, endpoint, args.model,
                          prompt, label, args.timeout, args.think, args.num_thread)
            results.append(result)
            verdict = ("EXACT" if result["exact"]
                       else f"{result['score']:.3f}%" if result["compiled"]
                       else "did not compile")
            print(f"{label}: {verdict} in {result['generation_seconds']:.1f}s",
                  flush=True)

    receipt = {
        "schema_version": 1,
        "design": ("paired single draw, temperature 0; directional pilot only"
                   if not args.score_existing else
                   "deterministic C89 rescore of prior paired pilot"),
        "function": args.function,
        "model": args.model,
        "evidence": str(evidence_path),
        "prior_logged_best_score": prior_best,
        "arms": results,
        "score_delta_context_minus_baseline": round(
            results[1]["score"] - results[0]["score"], 3),
    }
    if args.score_existing and args.out is None:
        out_path = evidence_path.with_suffix(".pilot-rescore.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2), flush=True)
    print(f"receipt: {out_path}", flush=True)


if __name__ == "__main__":
    main()
