"""Frozen-parent A/B for local-model repair.

Both arms receive the same reverified parent C, target assembly, compiler or
instruction residual, model, seed schedule, and maximum number of calls.

Control: independent complete-file revisions, always anchored on the parent.
Treatment: bounded structured edits, allowed to follow a child at depth two.

The smoke mode checks activation and receipts only. It is never interpreted as
efficacy. Full runs use DEV candidates exclusively; held-out is refused.

Freeze:
    python3 -m eval.modelrepair_ab freeze --db ~/decomp/kb-sbk1.sqlite \
        --set eval/sets/sbk1_v3.json --out eval/results/modelrepair-ab-v1.json

Three-function mechanical smoke:
    python3 -m eval.modelrepair_ab run --repo ~/decomp/sbk1 \
        --db ~/decomp/kb-sbk1.sqlite \
        --manifest eval/results/modelrepair-ab-v1.json \
        --out eval/results/modelrepair-ab-v1-smoke.json --smoke \
        --cache-dir ~/decomp/generation-cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from kb import attempts as attempt_receipts
from solver import diagnose, llm, modelrepair, refine, workspace


BUCKETS = ("compile-failure", "compiled-80-95", "compiled-95-plus")
SCHEMA_VERSION = 1
FULL_SOURCE_PREFILL = '```c\n#include "common.h"\n'
FORBIDDEN_FULL_SOURCE = re.compile(
    r"(?:\b(?:GLOBAL_ASM|INCLUDE_ASM|__asm__)\b|\basm\s*\(|\.incbin\b|"
    r"^\s*#\s*include\s+(?![\"<]common\.h[\">]))", re.I | re.M)

FULL_SOURCE_PROMPT = """\
You are correcting one C candidate so IDO 5.3 -O2 emits the target MIPS
instructions byte-for-byte. Return an independently revised COMPLETE C file.

Rules:
- Output one complete C file in a single ```c block, with no prose.
- It may only #include "common.h".
- C89 only; declarations belong at the start of a function or block.
- No inline assembly, GLOBAL_ASM, INCLUDE_ASM, or reference source.
- If a compiler error is present, fix that blocker before optimizing assembly.
- The instruction diff uses `-` for TARGET and `+` for CURRENT C.
- Do not assume or continue any earlier model proposal: revise the frozen parent.

TARGET ASSEMBLY:
```
{asm}
```

FROZEN PARENT C ({score:.3f}% similarity):
```c
{code}
```

CURRENT COMPILER ERROR:
```
{compiler_error}
```

CURRENT INSTRUCTION DIFF:
```
{diff}
```
{diagnosis}
Return the complete corrected C file now.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: dict) -> str:
    payload = dict(value)
    payload.pop("manifest_digest", None)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _valid_source(source: str) -> bool:
    return (bool(source.strip()) and "(" in source and "{" in source
            and not llm.is_refusal(source))


def _candidate(row: sqlite3.Row, entry: dict, bucket: str) -> dict:
    source = row["source_code"]
    return {
        "function": row["name"],
        "bucket": bucket,
        "tier": entry.get("tier", ""),
        "leaf": bool(entry.get("leaf")),
        "attempt_id": int(row["id"]),
        "source": source,
        "source_sha256": _sha(source),
        "recorded_compiled": bool(row["compiled"]),
        "recorded_score": float(row["score"] or 0.0),
        "recorded_exact": (None if row["exact"] is None
                           else bool(row["exact"])),
        "strategy": row["strategy"] or "",
        "compiler_stderr": row["compiler_stderr"] or "",
        "diff": row["diff_summary"] or "",
    }


def freeze_manifest(conn: sqlite3.Connection, set_path: Path, *,
                    split: str = "dev", per_bucket: int = 6,
                    seed: int = 20260901) -> dict:
    """Freeze deterministic non-exact parents from DEV attempt receipts."""
    if split != "dev":
        raise ValueError("model-repair A/B freezes DEV only; held-out is refused")
    if per_bucket <= 0:
        raise ValueError("per_bucket must be positive")

    refine.ensure_schema(conn)
    set_data = json.loads(set_path.read_text(encoding="utf-8"))
    entries = {entry["function"]: entry for entry in set_data[split]}
    if not entries:
        raise ValueError("evaluation split is empty")
    placeholders = ",".join("?" for _ in entries)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT a.id, f.name, a.source_code, a.compiled, a.score, a.exact, "
        f"a.compiler_stderr, a.diff_summary, a.strategy "
        f"FROM attempts a JOIN functions f ON f.addr=a.func_addr "
        f"WHERE f.name IN ({placeholders}) ORDER BY f.name, a.id",
        tuple(sorted(entries))).fetchall()

    by_function: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_function.setdefault(row["name"], []).append(row)

    pools = {bucket: [] for bucket in BUCKETS}
    excluded_exact = []
    for function in sorted(entries):
        attempts = by_function.get(function, [])
        if any(row["exact"] == 1 for row in attempts):
            excluded_exact.append(function)
            continue
        usable = [row for row in attempts
                  if _valid_source(row["source_code"] or "")]
        failed = [row for row in usable
                  if not row["compiled"] and (row["compiler_stderr"] or "")]
        middle = [row for row in usable
                  if row["compiled"] and 80 <= float(row["score"] or 0) < 95]
        near = [row for row in usable
                if row["compiled"] and 95 <= float(row["score"] or 0) < 100]
        if failed:
            row = max(failed, key=lambda item: item["id"])
            pools[BUCKETS[0]].append(
                _candidate(row, entries[function], BUCKETS[0]))
        if middle:
            row = max(middle, key=lambda item: (item["score"], item["id"]))
            pools[BUCKETS[1]].append(
                _candidate(row, entries[function], BUCKETS[1]))
        if near:
            row = max(near, key=lambda item: (item["score"], item["id"]))
            pools[BUCKETS[2]].append(
                _candidate(row, entries[function], BUCKETS[2]))

    rng = random.Random(seed)
    for pool in pools.values():
        pool.sort(key=lambda item: item["function"])
        rng.shuffle(pool)

    # Allocate scarce compile failures first and never use one function twice.
    chosen = {bucket: [] for bucket in BUCKETS}
    used: set[str] = set()
    for bucket in BUCKETS:
        for item in pools[bucket]:
            if item["function"] in used:
                continue
            chosen[bucket].append(item)
            used.add(item["function"])
            if len(chosen[bucket]) == per_bucket:
                break

    # Interleave buckets so --smoke selects one of each when all are present.
    candidates = []
    for index in range(per_bucket):
        for bucket in BUCKETS:
            if index < len(chosen[bucket]):
                candidates.append(chosen[bucket][index])
    counts = {bucket: len(chosen[bucket]) for bucket in BUCKETS}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "modelrepair-frozen-parent-manifest",
        "created_at": int(time.time()),
        "set_path": str(set_path),
        "set_sha256": hashlib.sha256(set_path.read_bytes()).hexdigest(),
        "split": split,
        "selection_seed": seed,
        "per_bucket_requested": per_bucket,
        "selection_rule": (
            "DEV functions without a positive exact receipt; latest usable "
            "compile failure, or highest-scoring receipt in the named band; "
            "seeded selection; function appears at most once"),
        "pool_counts": {bucket: len(pools[bucket]) for bucket in BUCKETS},
        "selected_counts": counts,
        "shortfalls": {bucket: per_bucket - counts[bucket]
                       for bucket in BUCKETS if counts[bucket] < per_bucket},
        "excluded_exact_functions": excluded_exact,
        "candidates": candidates,
    }
    manifest["manifest_digest"] = _json_digest(manifest)
    return manifest


def _source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "eval/modelrepair_ab.py", root / "solver/modelrepair.py",
        root / "solver/llm.py", root / "solver/workspace.py",
        root / "kb/attempts.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def _git_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=20).stdout.strip() or "no-git"
    except Exception:
        return "no-git"


def _base_seed(seed: int, function: str) -> int:
    offset = int(hashlib.sha256(function.encode()).hexdigest()[:8], 16)
    return (seed + offset) % (2**31 - 20001)


def _seed_schedule(seed: int, draws: int, depth: int) -> list[int]:
    return [seed + level * 10000 + draw
            for level in range(1, depth + 1) for draw in range(draws)]


def build_full_source_prompt(asm: str, source: str,
                             attempt: workspace.Attempt, diagnosis: str = "") -> str:
    diagnosis_block = (f"\nDETERMINISTIC DIAGNOSIS:\n{diagnosis}\n"
                       if diagnosis else "")
    return FULL_SOURCE_PROMPT.format(
        asm=asm, score=attempt.score, code=source,
        compiler_error=(attempt.compiler_stderr or "")[:6000],
        diff=(attempt.diff or "")[:12000], diagnosis=diagnosis_block)


def _better(candidate: workspace.Attempt,
            current: workspace.Attempt) -> bool:
    return (candidate.exact and not current.exact
            or candidate.compiled and not current.compiled
            or (candidate.compiled == current.compiled
                and candidate.score > current.score))


def _attempt_summary(attempt: workspace.Attempt) -> dict:
    return {
        "compiled": bool(attempt.compiled),
        "score": float(attempt.score),
        "exact": bool(attempt.exact),
        "receipt_id": attempt.receipt_id,
    }


def run_control(repo: Path, conn: sqlite3.Connection, function: str, ws: Path,
                asm: str, source: str, parent: workspace.Attempt, *,
                diagnosis: str, model: str, endpoint: str,
                seeds: list[int], timeout: int, think: str, num_thread: int,
                temperature: float, num_predict: int, cache_dir: Path,
                cache_namespace: str, run_id: str) -> dict:
    """Independent full-source revisions, all branching from one parent."""
    config = {
        "arm": "control-full-source", "seeds": seeds,
        "temperature": temperature, "num_predict": num_predict,
        "think": think, "cache_namespace": cache_namespace,
    }
    attempt_receipts.start_run(
        conn, run_id, kind="modelrepair-ab-control", model=model, config=config)
    prompt = build_full_source_prompt(asm, source, parent, diagnosis)
    workspace.assert_uncontaminated(prompt, repo, function)

    best, best_source = parent, source
    seen = {_sha(source)}
    calls = responses = tokens = charged_tokens = 0
    compiling_children = 0
    statuses: list[str] = []
    for index, draw_seed in enumerate(seeds, 1):
        calls += 1
        started = time.time()
        try:
            text, meta = llm.generate(
                endpoint, model, prompt, timeout=timeout, think=think,
                num_thread=num_thread, temperature=temperature,
                num_predict=num_predict, prefill=FULL_SOURCE_PREFILL,
                seed=draw_seed, cache_dir=cache_dir,
                cache_namespace=cache_namespace)
        except Exception as exc:
            wall_ms = int((time.time() - started) * 1000)
            attempt_receipts.record_model_proposal(
                conn, run_id=run_id, parent_attempt_id=parent.receipt_id,
                prompt=prompt, raw_response=str(exc), status="generation-error",
                model=model, kind="full-source", sampling={"seed": draw_seed},
                wall_ms=wall_ms)
            statuses.append("generation-error")
            continue
        responses += 1
        wall_ms = int((time.time() - started) * 1000)
        token_count = int(meta.get("eval_count", 0) or 0)
        tokens += token_count
        if not meta.get("_cache_hit"):
            charged_tokens += token_count
        code = llm.extract_c(text)
        if llm.is_refusal(text) or llm.is_refusal(code):
            status = "refusal"
        elif not code:
            status = "invalid"
        elif FORBIDDEN_FULL_SOURCE.search(code):
            status = "invalid"
        elif _sha(code) in seen:
            status = "duplicate"
        else:
            status = "valid"
        proposal_id = attempt_receipts.record_model_proposal(
            conn, run_id=run_id, parent_attempt_id=parent.receipt_id,
            prompt=prompt, raw_response=text, status=status, model=model,
            kind="full-source", sampling={
                "seed": draw_seed, "temperature": temperature,
                "done_reason": meta.get("done_reason"),
                "cache_hit": bool(meta.get("_cache_hit")),
                "cache_key": meta.get("_cache_key")},
            wall_ms=wall_ms, token_cost=token_count)
        statuses.append(status)
        if status != "valid":
            continue
        seen.add(_sha(code))
        attempt = workspace.score(
            ws, repo, f"{function}_ab_control_{index}", code,
            conn=conn, func=function, iteration=index,
            strategy="modelrepair-ab-control", model=model, prompt=prompt,
            temperature=temperature, wall_ms=wall_ms, run_id=run_id,
            token_cost=token_count,
            extra={"seed": draw_seed, "proposal_id": proposal_id,
                   "cache_hit": bool(meta.get("_cache_hit"))},
            raw_response=text, extract_status="full-source",
            done_reason=meta.get("done_reason", ""),
            parent_attempt_id=parent.receipt_id,
            relation="full-rewrite-control",
            action="independent-full-source", feedback=(
                parent.compiler_stderr if not parent.compiled else parent.diff),
            run_kind="modelrepair-ab-control", run_config=config)
        if attempt.receipt_id:
            attempt_receipts.link_model_proposal(
                conn, proposal_id, attempt.receipt_id)
        compiling_children += int(attempt.compiled)
        if _better(attempt, best):
            best, best_source = attempt, code
        if attempt.exact:
            break

    return {
        "arm": "control-full-source",
        "calls_attempted": calls,
        "responses": responses,
        "recorded_tokens": tokens,
        "charged_tokens": charged_tokens,
        "compiling_children": compiling_children,
        "produced_compiling_child": compiling_children > 0,
        "statuses": statuses,
        "best": _attempt_summary(best),
        "best_source_sha256": _sha(best_source),
        "score_delta": (best.score - parent.score
                        if best.compiled and parent.compiled else None),
        "compile_conversion": bool(best.compiled and not parent.compiled),
    }


def run_treatment(repo: Path, conn: sqlite3.Connection, function: str, ws: Path,
                  source: str, parent: workspace.Attempt, *, diagnosis: str,
                  model: str, endpoint: str, draws: int, depth: int,
                  timeout: int, think: str, num_thread: int,
                  temperature: float, num_predict: int, seed: int,
                  cache_dir: Path, cache_namespace: str, run_id: str) -> dict:
    result = modelrepair.search(
        repo, function, source, ws, model=model, endpoint=endpoint, conn=conn,
        base_attempt=parent, parent_attempt_id=parent.receipt_id,
        diagnosis=diagnosis, draws=draws, max_depth=depth, beam_width=1,
        timeout=timeout, think=think, num_thread=num_thread,
        temperature=temperature, num_predict=num_predict, seed=seed,
        run_id=run_id, cache_dir=cache_dir,
        cache_namespace=cache_namespace, exhaust_budget=True)
    return {
        "arm": "treatment-structured-edit",
        "calls_attempted": result.calls_attempted,
        "responses": result.generations,
        "recorded_tokens": result.tokens,
        "charged_tokens": result.charged_tokens,
        "produced_compiling_child": result.compiling_children > 0,
        "best": _attempt_summary(result.best_attempt),
        "best_source_sha256": _sha(result.best_source),
        "score_delta": (result.best_attempt.score - parent.score
                        if result.best_attempt.compiled and parent.compiled
                        else None),
        "compile_conversion": bool(
            result.best_attempt.compiled and not parent.compiled),
        "trace": result.log,
        "compiling_children_observed": result.compiling_children,
    }


def _diagnosis(repo: Path, ws: Path, object_path: Path, asm_len: int) -> str:
    if not object_path.exists():
        return ""
    result = diagnose.run(repo, ws / "target.o", object_path)
    return diagnose.prompt_block(repo, result, asm_len=asm_len) if result else ""


def _original_parent_id(conn: sqlite3.Connection, candidate: dict) -> int | None:
    row = conn.execute(
        "SELECT a.source_code FROM attempts a JOIN functions f "
        "ON f.addr=a.func_addr WHERE a.id=? AND f.name=?",
        (candidate["attempt_id"], candidate["function"])).fetchone()
    if row and _sha(row[0] or "") == candidate["source_sha256"]:
        return int(candidate["attempt_id"])
    return None


def run_candidate(repo: Path, conn: sqlite3.Connection, candidate: dict, *,
                  model: str, endpoint: str, draws: int, depth: int,
                  timeout: int, think: str, num_thread: int,
                  temperature: float, num_predict: int, seed: int,
                  cache_dir: Path, cache_namespace: str,
                  alternate: bool = False) -> dict:
    function = candidate["function"]
    source = candidate["source"]
    ws = workspace.bootstrap(repo, function)
    asm = workspace.target_asm(ws, function)
    parent_run = f"{cache_namespace}-{function}-parent"
    original_id = _original_parent_id(conn, candidate)
    parent = workspace.score(
        ws, repo, f"{function}_ab_parent", source,
        conn=conn, func=function, iteration=0,
        strategy="modelrepair-ab-parent", run_id=parent_run,
        parent_attempt_id=original_id,
        relation="ab-revalidation" if original_id is not None else "",
        action="reverify-frozen-parent", run_kind="modelrepair-ab")
    result = {
        "function": function,
        "bucket": candidate["bucket"],
        "tier": candidate.get("tier", ""),
        "leaf": bool(candidate.get("leaf")),
        "manifest_attempt_id": candidate["attempt_id"],
        "manifest_source_sha256": candidate["source_sha256"],
        "parent": _attempt_summary(parent),
        "recorded_parent": {
            "compiled": candidate["recorded_compiled"],
            "score": candidate["recorded_score"],
            "exact": candidate["recorded_exact"],
        },
    }
    if parent.exact:
        result.update({
            "status": "excluded-parent-now-exact",
            "reason": "frozen parent reverified exact before either arm",
        })
        return result
    diagnosis = (_diagnosis(
        repo, ws, ws / f"{function}_ab_parent.o", len(asm))
        if parent.compiled else "")
    base_seed = _base_seed(seed, function)
    seeds = _seed_schedule(base_seed, draws, depth)
    common = {
        "diagnosis": diagnosis, "model": model, "endpoint": endpoint,
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "temperature": temperature, "num_predict": num_predict,
        "cache_dir": cache_dir, "cache_namespace": cache_namespace,
    }

    def control():
        return run_control(
            repo, conn, function, ws, asm, source, parent, seeds=seeds,
            run_id=f"{cache_namespace}-{function}-control", **common)

    def treatment():
        return run_treatment(
            repo, conn, function, ws, source, parent, draws=draws,
            depth=depth, seed=base_seed,
            run_id=f"{cache_namespace}-{function}-treatment", **common)

    started = time.time()
    if alternate:
        treatment_result, control_result = treatment(), control()
    else:
        control_result, treatment_result = control(), treatment()
    result.update({
        "status": "complete",
        "diagnosis_present": bool(diagnosis),
        "seed_schedule": seeds,
        "control": control_result,
        "treatment": treatment_result,
        "wall_seconds": round(time.time() - started, 3),
    })
    return result


def aggregate(results: list[dict], *, smoke: bool = False,
              expected_functions: int | None = None) -> dict:
    complete = [result for result in results if result.get("status") == "complete"]

    def arm(name: str) -> dict:
        rows = [result[name] for result in complete]
        exact = sum(bool(row["best"]["exact"]) for row in rows)
        charged = sum(int(row["charged_tokens"]) for row in rows)
        deltas = [row["score_delta"] for row in rows
                  if row["score_delta"] is not None]
        return {
            "exact": exact,
            "calls_attempted": sum(row["calls_attempted"] for row in rows),
            "responses": sum(row["responses"] for row in rows),
            "recorded_tokens": sum(row["recorded_tokens"] for row in rows),
            "charged_tokens": charged,
            "compile_conversions": sum(row["compile_conversion"] for row in rows),
            "mean_score_delta": (sum(deltas) / len(deltas) if deltas else None),
            "exact_per_million_charged_tokens": (
                exact * 1_000_000 / charged if charged else None),
        }

    control = arm("control")
    treatment = arm("treatment")
    exact_delta = treatment["exact"] - control["exact"]
    expected = len(results) if expected_functions is None else expected_functions
    if smoke:
        verdict = "mechanical-only"
    elif len(complete) != expected:
        verdict = "incomplete"
    elif exact_delta >= 3:
        verdict = "treatment-passes-exact-floor"
    elif exact_delta <= -3:
        verdict = "control-wins-by-exact-floor"
    else:
        verdict = "inconclusive"
    return {
        "complete_functions": len(complete),
        "expected_functions": expected,
        "excluded_functions": len(results) - len(complete),
        "control": control,
        "treatment": treatment,
        "treatment_minus_control_exact": exact_delta,
        "verdict": verdict,
        "interpretation": (
            "smoke checks activation and receipts, not efficacy" if smoke else
            "exact deltas below three are inconclusive by project rule"),
    }


def run_experiment(repo: Path, conn: sqlite3.Connection, manifest: dict, *,
                   out: Path, model: str, endpoint: str, draws: int = 2,
                   depth: int = 2, timeout: int = 420, think: str = "low",
                   num_thread: int = 12, temperature: float = 0.4,
                   num_predict: int = 1800, seed: int = 20260901,
                   cache_dir: Path, smoke: bool = False,
                   max_wall_minutes: float = 45.0) -> dict:
    if manifest.get("split") != "dev":
        raise ValueError("model-repair A/B runs DEV only; held-out is refused")
    if _json_digest(manifest) != manifest.get("manifest_digest"):
        raise ValueError("manifest digest mismatch")
    if draws <= 0 or depth <= 0:
        raise ValueError("draws and depth must be positive")
    candidates = list(manifest.get("candidates", []))
    if smoke:
        candidates = candidates[:3]
    elif manifest.get("shortfalls"):
        raise ValueError(
            "full efficacy run requires six frozen parents in every bucket; "
            f"manifest shortfalls: {manifest['shortfalls']}")
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": "modelrepair-frozen-parent-ab",
        "manifest_digest": manifest["manifest_digest"],
        "selected_functions": [item["function"] for item in candidates],
        "smoke": smoke, "model": model, "draws_per_depth": draws,
        "depth": depth, "beam": 1, "calls_per_arm": draws * depth,
        "temperature": temperature, "num_predict": num_predict,
        "think": think, "num_thread": num_thread, "timeout": timeout,
        "max_wall_minutes": max_wall_minutes, "endpoint": endpoint,
        "cache_dir": str(cache_dir), "seed": seed,
        "source_fingerprint": _source_fingerprint(),
        "git_revision": _git_revision(),
    }
    config_digest = _sha(json.dumps(config, sort_keys=True))[:20]
    cache_namespace = f"modelrepair-ab-v1-{config_digest}"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "modelrepair-frozen-parent-ab",
        "created_at": int(time.time()),
        "config": config,
        "config_digest": config_digest,
        "cache_namespace": cache_namespace,
        "hypothesis": (
            "structured child-following repair produces at least three more "
            "exact matches than independent full-source revisions under the "
            "same parent, model, seed schedule, and call cap"),
        "results": [],
        "status": "running",
    }
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        if existing.get("config_digest") != config_digest:
            raise ValueError("output belongs to a different A/B configuration")
        receipt = existing
    done = {row["function"] for row in receipt.get("results", [])}
    started = time.time()
    for index, candidate in enumerate(candidates):
        if candidate["function"] in done:
            continue
        if receipt["results"] and (time.time() - started) / 60 >= max_wall_minutes:
            receipt["status"] = "stopped-wall-budget"
            break
        print(f"[{len(receipt['results']) + 1}/{len(candidates)}] "
              f"{candidate['function']} ({candidate['bucket']})", flush=True)
        result = run_candidate(
            repo, conn, candidate, model=model, endpoint=endpoint,
            draws=draws, depth=depth, timeout=timeout, think=think,
            num_thread=num_thread, temperature=temperature,
            num_predict=num_predict, seed=seed, cache_dir=cache_dir,
            cache_namespace=cache_namespace, alternate=bool(index % 2))
        receipt["results"].append(result)
        receipt["aggregate"] = aggregate(
            receipt["results"], smoke=smoke,
            expected_functions=len(candidates))
        _atomic_json(out, receipt)
        print(f"    control={result.get('control', {}).get('best', {})}"
              f" treatment={result.get('treatment', {}).get('best', {})}",
              flush=True)
    else:
        receipt["status"] = "complete"
    receipt["aggregate"] = aggregate(
        receipt["results"], smoke=smoke,
        expected_functions=len(candidates))
    receipt["finished_at"] = int(time.time())
    _atomic_json(out, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="freeze DEV parent attempts")
    freeze.add_argument("--db", required=True, type=Path)
    freeze.add_argument("--set", required=True, type=Path)
    freeze.add_argument("--out", required=True, type=Path)
    freeze.add_argument("--per-bucket", type=int, default=6)
    freeze.add_argument("--seed", type=int, default=20260901)

    run = sub.add_parser("run", help="run paired control and treatment arms")
    run.add_argument("--repo", required=True, type=Path)
    run.add_argument("--db", required=True, type=Path)
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--model", default="gpt-oss:20b")
    run.add_argument("--draws", type=int, default=2)
    run.add_argument("--depth", type=int, default=2)
    run.add_argument("--temperature", type=float, default=0.4)
    run.add_argument("--num-predict", type=int, default=1800)
    run.add_argument("--timeout", type=int, default=420)
    run.add_argument("--think", default="low")
    run.add_argument("--num-thread", type=int, default=12)
    run.add_argument("--seed", type=int, default=20260901)
    run.add_argument("--cache-dir", required=True, type=Path)
    run.add_argument("--smoke", action="store_true",
                     help="run the first balanced three; mechanical only")
    run.add_argument("--max-wall-minutes", type=float, default=45.0)

    args = parser.parse_args()
    if args.command == "freeze":
        conn = sqlite3.connect(args.db.expanduser(), timeout=120)
        conn.execute("PRAGMA busy_timeout = 120000")
        manifest = freeze_manifest(
            conn, args.set.expanduser(), per_bucket=args.per_bucket,
            seed=args.seed)
        _atomic_json(args.out.expanduser(), manifest)
        print(json.dumps({
            "manifest_digest": manifest["manifest_digest"],
            "pool_counts": manifest["pool_counts"],
            "selected_counts": manifest["selected_counts"],
            "shortfalls": manifest["shortfalls"],
        }, indent=2))
        return

    manifest = json.loads(args.manifest.expanduser().read_text(encoding="utf-8"))
    conn = sqlite3.connect(args.db.expanduser(), timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    receipt = run_experiment(
        args.repo.expanduser(), conn, manifest, out=args.out.expanduser(),
        model=args.model, endpoint=llm.host(), draws=args.draws,
        depth=args.depth, timeout=args.timeout, think=args.think,
        num_thread=args.num_thread, temperature=args.temperature,
        num_predict=args.num_predict, seed=args.seed,
        cache_dir=args.cache_dir.expanduser(), smoke=args.smoke,
        max_wall_minutes=args.max_wall_minutes)
    print(json.dumps(receipt["aggregate"], indent=2))


if __name__ == "__main__":
    main()
