"""ABI-sensitive leaf enrichment and deterministic caller-transfer pilot.

The pilot first ranks never-attempted small DEV leaves using target-binary
signals that can make an exact prototype compiler-active: narrow/floating
returns, narrow parameters, and caller fanout.  It then spends at most one
seeded, cached model draw per selected leaf.  Newly exact signatures are
shadow-applied to stored callers and recompiled; parent generation is never
performed by this experiment.

The target source is never read.  Held-out names are subtracted independently
of the input set's labels because the historical ``leaves_v1`` set predates
that safeguard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import callgraph
from eval import repair as finish_repair
from patterns.catalog import detect_narrow_params
from solver import (c89, llm, pipeline, project_headers, protostore, refine,
                    repair, rewrites, signals, structgen, workspace)


RETURN_NAME = re.compile(r"^(?:get|is|has|can|find|calculate|count|check)", re.I)
RECOVERY = tuple(protostore.RECOVERY_STRATEGIES)
PARAM_MEMORY = re.compile(
    r"\b(lb|lbu|sb|lh|lhu|sh|lw|sw|lwc1|swc1|ld|sd|ldc1|sdc1)\s+"
    r"\$?\w+\s*,\s*(-?(?:0x)?[0-9a-fA-F]+)\(\$?(a[0-3])\)", re.I)
MEMORY_WIDTH = {
    "lb": 1, "lbu": 1, "sb": 1,
    "lh": 2, "lhu": 2, "sh": 2,
    "lw": 4, "sw": 4, "lwc1": 4, "swc1": 4,
    "ld": 8, "sd": 8, "ldc1": 8, "sdc1": 8,
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def heldout_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        names.update(str(row["function"])
                     for row in payload.get("heldout", []))
    return names


def abi_signals(asm: str) -> dict[str, object]:
    """Conservative ABI-leverage hints from target assembly only."""
    narrow_params = detect_narrow_params(asm)
    lines = [re.sub(r"^\s*/\*.*?\*/\s*", "", line).strip()
             for line in asm.splitlines()]
    lines = [line for line in lines if line and not line.endswith(":")
             and not line.startswith(("glabel ", "endlabel "))]
    windows = []
    for index, line in enumerate(lines):
        if re.match(r"jr\s+\$?ra\b", line, re.I):
            windows.extend(lines[max(0, index - 8):min(len(lines), index + 2)])
    tail = "\n".join(windows or lines[-12:])

    float_return = bool(re.search(
        r"\b(?:mov\.s|mov\.d|lwc1|ldc1|add\.[sd]|sub\.[sd]|mul\.[sd]|div\.[sd])"
        r"\s+\$?f0\b", tail, re.I))
    narrow_load = None
    load = re.search(r"\b(lb|lbu|lh|lhu)\s+\$?v0\b", tail, re.I)
    if load:
        narrow_load = {
            "opcode": load.group(1).lower(),
            "width": 1 if load.group(1).lower() in {"lb", "lbu"} else 2,
            "signed": load.group(1).lower() in {"lb", "lh"},
        }
    narrow_normalization = None
    mask = re.search(
        r"\bandi\s+\$?v0\s*,\s*\$?\w+\s*,\s*0x(ff|ffff)\b", tail, re.I)
    if mask:
        narrow_normalization = {
            "kind": "zero_extend",
            "width": 1 if mask.group(1).lower() == "ff" else 2,
        }
    for amount, width in ((r"(?:24|0x18)", 1), (r"(?:16|0x10)", 2)):
        # target.s may print shift amounts in hex or decimal.
        if re.search(rf"\bsll\s+\$?v0\b[^\n]*,\s*{amount}\b", tail, re.I) \
                and re.search(
                    rf"\bsra\s+\$?v0\b[^\n]*,\s*{amount}\b", tail, re.I):
            narrow_normalization = {"kind": "sign_extend", "width": width}
            break

    writes_v0 = bool(re.search(
        r"\b(?:lb|lbu|lh|lhu|lw|move|li|addiu|addu|or|andi|sll|sra|sltu?)"
        r"\s+\$?v0\b", tail, re.I))
    return {
        "narrow_parameters": narrow_params,
        "float_return_signal": float_return,
        "narrow_return_load": narrow_load,
        "narrow_return_normalization": narrow_normalization,
        "writes_v0_near_return": writes_v0,
    }


def direct_parameter_layout(asm: str) -> dict[int, int]:
    """Direct parameter-relative offsets stated by target instructions.

    This intentionally does not chase copied or computed bases. A missed fact
    merely disables a proposal; a guessed base can fabricate a struct layout.
    Conflicting widths at one offset are also excluded.
    """
    seen: dict[int, set[int]] = {}
    for opcode, immediate, _parameter in PARAM_MEMORY.findall(asm):
        try:
            offset = int(immediate, 0)
        except ValueError:
            continue
        if offset < 0:
            continue
        seen.setdefault(offset, set()).add(MEMORY_WIDTH[opcode.lower()])
    return {offset: next(iter(widths)) for offset, widths in seen.items()
            if len(widths) == 1}


def leverage_score(name: str, signals: dict[str, object], callers: int,
                   insns: int) -> tuple[float, list[str]]:
    """Score compiler leverage, not semantic importance or match likelihood."""
    score = 0.0
    reasons = []
    if signals["float_return_signal"]:
        score += 12.0
        reasons.append("floating return register")
    if signals["narrow_return_normalization"]:
        score += 11.0
        reasons.append("explicit narrow return normalization")
    elif signals["narrow_return_load"]:
        score += 7.0
        reasons.append("narrow load reaches v0 near return")
    narrow = signals["narrow_parameters"]
    if isinstance(narrow, dict) and narrow:
        score += 5.0 * len(narrow)
        reasons.append(f"{len(narrow)} narrow parameter(s)")
    if not reasons:
        return 0.0, []
    score += min(10, max(0, callers)) * 0.8
    if RETURN_NAME.match(name) and signals["writes_v0_near_return"]:
        score += 1.0
        reasons.append("return-like symbol name (ranking hint only)")
    score += max(0.0, (20 - insns) / 20.0)
    return round(score, 3), reasons


def rank_pool(repo: Path, conn: sqlite3.Connection, leaf_set: dict,
              heldout: set[str], *, max_leaves: int) -> tuple[list[dict], dict]:
    attempted = {str(name) for (name,) in conn.execute(
        "select distinct f.name from attempts a join functions f "
        "on f.addr=a.func_addr")}
    _callees, callers = callgraph.edges(conn)
    rows, errors = [], []
    heldout_excluded = []
    stale_attempted = []
    no_eligible_callers = []
    for item in leaf_set.get("dev", []):
        name = str(item["function"])
        if name in heldout:
            heldout_excluded.append(name)
            continue
        if name in attempted:
            stale_attempted.append(name)
            continue
        try:
            ws = workspace.bootstrap(repo, name)
            asm = workspace.target_asm(ws, name)
        except Exception as exc:
            errors.append({"function": name,
                           "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            continue
        signals = abi_signals(asm)
        insns = int(item.get("insns") or len(asm.splitlines()))
        caller_count = len([parent for parent in callers.get(name, set())
                            if parent not in heldout])
        score, reasons = leverage_score(
            name, signals, caller_count, insns)
        if not reasons:
            continue
        # This pilot measures upward transfer, so a leaf with no eligible parent
        # cannot answer its primary question even if its ABI is interesting.
        if caller_count == 0:
            no_eligible_callers.append(name)
            continue
        rows.append({
            "function": name,
            "tier": item.get("tier"),
            "insns": insns,
            "dev_callers": caller_count,
            "abi_leverage_score": score,
            "reasons": reasons,
            "signals": signals,
            "target_asm_sha256": _sha(asm),
        })
    rows.sort(key=lambda row: (-float(row["abi_leverage_score"]),
                               -int(row["dev_callers"]),
                               int(row["insns"]), str(row["function"])))
    audit = {
        "input_dev_rows": len(leaf_set.get("dev", [])),
        "heldout_excluded": sorted(heldout_excluded),
        "stale_already_attempted": sorted(stale_attempted),
        "abi_sensitive_without_eligible_callers": sorted(no_eligible_callers),
        "bootstrap_errors": errors,
        "abi_sensitive_candidates": len(rows),
    }
    return rows[:max_leaves], {"ranked": rows, **audit}


def _score(repo: Path, conn: sqlite3.Connection, ws: Path, function: str,
           artifact: str, source: str, *, strategy: str, model: str = "",
           prompt: str = "", temperature=None, wall_ms: int = 0,
           token_cost: int = 0, run_id: str = "", extra: dict | None = None,
           raw_response: str = "", extract_status: str = "",
           done_reason: str = "") -> tuple[workspace.Attempt, dict]:
    att = workspace.score(
        ws, repo, artifact, source, conn=conn, func=function,
        strategy=strategy, model=model, prompt=prompt,
        temperature=temperature, wall_ms=wall_ms, token_cost=token_cost,
        run_id=run_id, extra=extra, raw_response=raw_response,
        extract_status=extract_status, done_reason=done_reason)
    return att, {
        "artifact_stem": artifact,
        "source_sha256": _sha(source),
        "source_chars": len(source),
        "compiled": att.compiled,
        "score": att.score,
        "exact": att.exact,
        "compiler_error": att.compiler_stderr[:1600],
    }


def solve_leaf(repo: Path, conn: sqlite3.Connection, endpoint: str,
               selected: dict, *, model: str, timeout: int, think: str,
               num_thread: int, num_predict: int, temperature: float,
               seed: int, cache_dir: Path, run_id: str,
               repair_candidates: int, layout_candidates: int,
               cache_only: bool,
               cache_namespace: str = "abi-leaf-pilot-v1",
               strategy_prefix: str = "abi-leaf") -> dict:
    function = str(selected["function"])
    started = time.perf_counter()
    ws = workspace.bootstrap(repo, function)
    asm = workspace.target_asm(ws, function)
    draft = workspace.m2c_draft(ws)
    candidates: list[tuple[workspace.Attempt, str, str]] = []
    attempts = []
    fallback_source, fallback_stage = draft, "m2c"

    if draft.strip():
        att, row = _score(
            repo, conn, ws, function, f"{run_id}_{function}_m2c", draft,
            strategy=f"{strategy_prefix}-m2c", run_id=run_id,
            extra={"seed": None, "stage": "zero_token_m2c"})
        attempts.append({"stage": "m2c", **row})
        if att.compiled:
            candidates.append((att, draft, "m2c"))
        if att.exact:
            return {
                "function": function, "seed": seed, "model_calls": 0,
                "charged_generation_tokens": 0, "attempts": attempts,
                "exact": True, "best_score": att.score,
                "exact_source": draft, "exact_stage": "m2c",
                "wall_seconds": round(time.perf_counter() - started, 3),
            }

    for index, (label, source) in enumerate(
            project_headers.preflight_variants(repo, function, asm, draft)):
        fallback_source, fallback_stage = source, label
        att, row = _score(
            repo, conn, ws, function,
            f"{run_id}_{function}_project_header_{index}", source,
            strategy=f"{strategy_prefix}-project-header", run_id=run_id,
            extra={"seed": None, "stage": "zero_token_project_header",
                   "preflight": label})
        attempts.append({"stage": label, **row})
        if att.compiled:
            candidates.append((att, source, label))
        if att.exact:
            return {
                "function": function, "seed": seed, "model_calls": 0,
                "charged_generation_tokens": 0, "attempts": attempts,
                "exact": True, "best_score": att.score,
                "exact_source": source, "exact_stage": label,
                "wall_seconds": round(time.perf_counter() - started, 3),
            }

    prompt_draft = max(candidates, key=lambda row: row[0].score)[1] \
        if candidates else draft
    prompt = pipeline.build_prompt(
        repo, conn, function, asm, prompt_draft, "reshape", False)
    workspace.assert_uncontaminated(prompt, repo, function)
    generated_at = time.perf_counter()
    try:
        text, meta = llm.generate(
            endpoint, model, prompt, timeout=timeout, think=think,
            num_thread=num_thread, num_predict=num_predict,
            temperature=temperature, prefill=pipeline.PREFILL, seed=seed,
            cache_dir=cache_dir, cache_namespace=cache_namespace,
            cache_only=cache_only)
    except FileNotFoundError as exc:
        if not cache_only:
            raise
        if candidates:
            best_att, best_source, best_stage = max(
                candidates, key=lambda row: (bool(row[0].exact), row[0].score))
        else:
            best_att = workspace.Attempt(False, 0.0, False, "", "", "")
            best_source, best_stage = fallback_source, fallback_stage
        return {
            "function": function, "seed": seed, "model_calls": 0,
            "cache_replays": 0, "charged_generation_tokens": 0,
            "attempts": attempts, "exact": bool(best_att.exact),
            "best_score": float(best_att.score),
            "best_stage": best_stage, "best_source": best_source,
            "exact_source": best_source if best_att.exact else "",
            "cache_only_miss": str(exc),
            "wall_seconds": round(time.perf_counter() - started, 3),
        }
    generation_seconds = time.perf_counter() - generated_at
    cache_hit = bool(meta.get("_cache_hit"))
    recorded_tokens = int(meta.get("eval_count", 0) or 0)
    charged_tokens = 0 if cache_hit else recorded_tokens
    code = llm.extract_c(text)
    extraction = llm.classify_extraction(text, code)
    variants = [("model_raw", code)]
    repaired = c89.to_c89(code)
    if repaired != code:
        variants.append(("model_c89", repaired))
    for index, (stage, source) in enumerate(variants):
        att, row = _score(
            repo, conn, ws, function, f"{run_id}_{function}_{stage}", source,
            strategy=f"{strategy_prefix}-{stage}", model=model, prompt=prompt,
            temperature=temperature, wall_ms=int(generation_seconds * 1000),
            token_cost=charged_tokens if index == 0 else 0, run_id=run_id,
            extra={"seed": seed, "cache_hit": cache_hit,
                   "cache_key": meta.get("_cache_key"), "stage": stage},
            raw_response=text if index == 0 else "",
            extract_status=extraction,
            done_reason=meta.get("done_reason", ""))
        attempts.append({"stage": stage, **row})
        if att.compiled:
            candidates.append((att, source, stage))

    if candidates:
        best_att, best_source, best_stage = max(
            candidates, key=lambda row: (bool(row[0].exact), row[0].score))
    else:
        best_att = workspace.Attempt(False, 0.0, False, "", "", "")
        best_source, best_stage = code, "model_raw"

    layout_log = []
    if best_att.compiled and not best_att.exact and layout_candidates > 0:
        best_faults = signals.analyse(
            best_att.diff, best_att.score, best_att.exact, best_att.compiled)
        proposals = list(finish_repair.passes(
            best_source, conn=conn, func=function, repo=repo, ws=ws))
        direct_layout = direct_parameter_layout(asm)
        if direct_layout:
            for skip in range(4):
                source, changed = structgen.align_positional(
                    best_source, direct_layout, skip=skip)
                if changed:
                    proposals.append((f"target-align(skip={skip})", source))
            for greedy in ("first", "last"):
                source, changed = structgen.align_subsequence(
                    best_source, direct_layout, greedy=greedy)
                if changed:
                    proposals.append((f"target-subseq({greedy})", source))
        seen_layout_sources = {best_source}
        for label, layout_source in proposals:
            if label == "baseline" or layout_source == best_source:
                continue
            if layout_source in seen_layout_sources:
                continue
            seen_layout_sources.add(layout_source)
            if len(layout_log) >= layout_candidates:
                break
            att, row = _score(
                repo, conn, ws, function,
                f"{run_id}_{function}_finish_{len(layout_log)}",
                layout_source, strategy=f"{strategy_prefix}-finish-{label}",
                run_id=run_id,
                extra={"seed": seed, "stage": "deterministic_finish",
                       "repair": label})
            attempts.append({"stage": f"deterministic_finish:{label}", **row})
            layout_log.append({
                "repair": label, "compiled": att.compiled,
                "score": att.score, "exact": att.exact,
                "layout_faults": signals.analyse(
                    att.diff, att.score, att.exact, att.compiled).layout,
            })
            att_faults = signals.analyse(
                att.diff, att.score, att.exact, att.compiled)
            closes_layout_phase = (
                att.compiled and att.score >= best_att.score - 0.0005
                and att_faults.layout < best_faults.layout
                and att_faults.structural <= best_faults.structural)
            if att.exact or (att.compiled and att.score > best_att.score) \
                    or closes_layout_phase:
                best_att, best_source, best_stage = (
                    att, layout_source, f"deterministic_finish:{label}")
                best_faults = att_faults
            if att.exact:
                break

    repair_log = []
    if best_att.compiled and not best_att.exact and repair_candidates > 0:
        try:
            r_att, r_source, repair_log = repair.search(
                repo, function, best_source, ws, conn=conn, verbose=False,
                max_pairs=repair_candidates, max_depth=3, beam_width=4)
        except Exception as exc:
            repair_log = [f"error: {type(exc).__name__}: {str(exc)[:200]}"]
        else:
            attempts.append({
                "stage": "deterministic_repair",
                "source_sha256": _sha(r_source),
                "source_chars": len(r_source),
                "compiled": r_att.compiled, "score": r_att.score,
                "exact": r_att.exact,
            })
            if r_att.exact or r_att.score > best_att.score:
                best_att, best_source, best_stage = \
                    r_att, r_source, "deterministic_repair"

    return {
        "function": function,
        "seed": seed,
        "model_calls": 0 if cache_hit else 1,
        "cache_replays": 1 if cache_hit else 0,
        "cache_hit": cache_hit,
        "cache_key": meta.get("_cache_key"),
        "prompt_chars": len(prompt),
        "prompt_sha256": _sha(prompt),
        "generation_seconds": round(generation_seconds, 3),
        "recorded_generation_tokens": recorded_tokens,
        "charged_generation_tokens": charged_tokens,
        "done_reason": meta.get("done_reason", ""),
        "extraction": extraction,
        "attempts": attempts,
        "layout_repair_log": layout_log,
        "repair_log": repair_log[-12:],
        "exact": best_att.exact,
        "best_score": best_att.score,
        "best_stage": best_stage,
        "best_source": best_source,
        "best_diff": best_att.diff,
        "best_diff_sha256": _sha(best_att.diff or ""),
        "exact_source": best_source if best_att.exact else "",
        "wall_seconds": round(time.perf_counter() - started, 3),
    }


def _best_parent_candidate(conn: sqlite3.Connection,
                           function: str) -> dict | None:
    rows = conn.execute(
        "select a.id,a.source_code,a.score,a.strategy,a.model from attempts a "
        "join functions f on f.addr=a.func_addr where f.name=? "
        "and a.compiled=1 and a.exact=0 and length(trim(a.source_code))>0 "
        "order by a.score desc,a.id desc limit 100", (function,)).fetchall()
    for attempt_id, source, score, strategy, model in rows:
        strategy = str(strategy or "")
        if any(marker in strategy for marker in RECOVERY):
            continue
        if strategy.startswith("abi-leaf-parent-"):
            continue
        return {"attempt_id": int(attempt_id), "source": str(source),
                "score": float(score or 0.0), "strategy": strategy,
                "model": str(model or "")}
    return None


def measure_parent_transfer(repo: Path, conn: sqlite3.Connection,
                            leaf_results: list[dict], heldout: set[str],
                            *, run_id: str, max_parents_per_leaf: int) -> list[dict]:
    _callees, callers = callgraph.edges(conn)
    rows = []
    for leaf in leaf_results:
        if not leaf.get("exact"):
            continue
        function = str(leaf["function"])
        source = str(leaf.get("exact_source") or "")
        signature = protostore.parse_definition(source, function)
        leaf_row = {
            "leaf": function,
            "signature": signature,
            "callers_total": len(callers.get(function, set())),
            "callers": [],
        }
        rows.append(leaf_row)
        if signature is None:
            leaf_row["status"] = "exact_source_signature_unparsed"
            continue
        protostore.clear()
        protostore.record(function, signature)
        for parent in sorted(callers.get(function, set())):
            if parent in heldout or len(leaf_row["callers"]) >= max_parents_per_leaf:
                continue
            stored = _best_parent_candidate(conn, parent)
            if stored is None:
                leaf_row["callers"].append({
                    "parent": parent, "status": "no_stored_compiling_candidate"})
                continue
            proposals = rewrites.prototype_rewrites(stored["source"], "")
            relevant = [proposal for proposal in proposals
                        if function in proposal.label]
            if not relevant:
                leaf_row["callers"].append({
                    "parent": parent,
                    "origin_attempt_id": stored["attempt_id"],
                    "status": "prototype_already_compatible_or_call_not_in_source",
                })
                continue
            ws = workspace.bootstrap(repo, parent)
            stem = f"{run_id}_{function}_{parent}"
            baseline = workspace.score(
                ws, repo, stem + "_baseline", stored["source"], conn=conn,
                func=parent, strategy="abi-leaf-parent-baseline", run_id=run_id,
                extra={"leaf": function,
                       "origin_attempt_id": stored["attempt_id"]})
            baseline_asm = ws / f"{stem}_baseline_object_dump_normalized.s"
            baseline_hash = (hashlib.sha256(baseline_asm.read_bytes()).hexdigest()
                             if baseline_asm.exists() else "")
            variants = []
            for index, proposal in enumerate(relevant[:2]):
                candidate = proposal(stored["source"])
                att = workspace.score(
                    ws, repo, f"{stem}_prototype_{index}", candidate, conn=conn,
                    func=parent, strategy="abi-leaf-parent-prototype",
                    run_id=run_id,
                    extra={"leaf": function, "prototype": signature["prototype"],
                           "origin_attempt_id": stored["attempt_id"]})
                candidate_asm = ws / (
                    f"{stem}_prototype_{index}_object_dump_normalized.s")
                candidate_hash = (hashlib.sha256(
                    candidate_asm.read_bytes()).hexdigest()
                    if candidate_asm.exists() else "")
                variants.append({
                    "proposal": proposal.label,
                    "compiled": att.compiled, "score": att.score,
                    "exact": att.exact,
                    "assembly_changed": bool(
                        baseline_hash and candidate_hash
                        and baseline_hash != candidate_hash),
                    "score_delta": round(att.score - baseline.score, 6),
                })
            leaf_row["callers"].append({
                "parent": parent,
                "origin_attempt_id": stored["attempt_id"],
                "origin_score": stored["score"],
                "replay_score": baseline.score,
                "status": "prototype_shadow_compiled",
                "variants": variants,
            })
        protostore.clear()
    return rows


def _apply_inferred_prototype(source: str, function: str,
                              return_type: str) -> tuple[str, str] | None:
    """Shadow-apply an old-style return contract, preserving parameter ABI."""
    masked = c89._mask(source)
    if not re.search(rf"(?<![\w.]){re.escape(function)}\s*\(", masked):
        return None
    prototype = f"{return_type} {function}();"
    declaration = re.compile(
        rf"^[ \t]*(?:extern[ \t]+)?[A-Za-z_][\w \t\*]*?(?<![\w])"
        rf"{re.escape(function)}[ \t]*\([^;{{}})]*\)[ \t]*;[ \t]*\n",
        re.M)
    match = declaration.search(masked)
    if match:
        existing = source[match.start():match.end()].strip()
        if existing.rstrip(";") == prototype.rstrip(";"):
            return None
        return (source[:match.start()] + prototype + "\n" + source[match.end():],
                "replace")
    anchor = source.find("\n", source.find("#include")) + 1
    if anchor <= 0:
        anchor = 0
    return source[:anchor] + prototype + "\n" + source[anchor:], "insert"


def return_contract_activity(asm: str, callee: str,
                             width: int) -> list[dict[str, object]]:
    """Classify the first visible consumer after each direct call.

    A mask no wider than the proposed return width subsumes zero extension,
    making that edge a poor return-contract experiment even though it consumes
    the value.
    """
    lines = [re.sub(r"^\s*/\*.*?\*/\s*", "", line).strip()
             for line in asm.splitlines()]
    calls = []
    for index, line in enumerate(lines):
        if not re.match(rf"(?:jal|bal)\s+{re.escape(callee)}\b", line, re.I):
            continue
        row: dict[str, object] = {
            "call_line": index + 1, "consumer": "not_observed",
            "compiler_active": False,
        }
        # index+1 is the MIPS delay slot and executes before the return exists.
        for consumer in lines[index + 2:index + 10]:
            if not consumer or consumer.endswith(":"):
                continue
            masked = re.match(
                r"andi\s+\$?\w+\s*,\s*\$?v0\s*,\s*(0x[0-9a-f]+|\d+)",
                consumer, re.I)
            if masked:
                mask = int(masked.group(1), 0)
                row.update({
                    "consumer": "explicit_mask", "mask": mask,
                    "compiler_active": mask > ((1 << (8 * width)) - 1),
                })
                break
            if re.search(r"\$?v0\b", consumer, re.I):
                row.update({"consumer": consumer,
                            "compiler_active": True})
                break
            if re.match(r"(?:jal|bal|jalr)\b", consumer, re.I):
                row["consumer"] = "clobbered_before_observed_use"
                break
        calls.append(row)
    return calls


def measure_inferred_contract_transfer(
        repo: Path, conn: sqlite3.Connection, selected: list[dict],
        heldout: set[str], *, run_id: str,
        max_parents_per_leaf: int) -> list[dict]:
    """Test ambiguous binary-derived return annotations on stored callers.

    These contracts never enter protostore: a narrow load into v0 proves the
    emitted extension behavior, not whether the original C spelled the return
    as u8/u16 or u32. Both compatible spellings are proposed and the caller's
    oracle result is recorded as evidence, never promoted as a verified fact.
    """
    _callees, callers = callgraph.edges(conn)
    rows = []
    for leaf in selected:
        signal = leaf.get("signals", {})
        load = signal.get("narrow_return_load") if isinstance(signal, dict) \
            else None
        if not isinstance(load, dict) or load.get("signed"):
            continue
        width = int(load.get("width") or 0)
        narrow_type = "u8" if width == 1 else "u16" if width == 2 else ""
        if not narrow_type:
            continue
        function = str(leaf["function"])
        leaf_row = {
            "leaf": function,
            "evidence": load,
            "hypotheses": [narrow_type, "u32"],
            "callers": [],
        }
        rows.append(leaf_row)
        for parent in sorted(callers.get(function, set())):
            if parent in heldout or len(leaf_row["callers"]) >= max_parents_per_leaf:
                continue
            stored = _best_parent_candidate(conn, parent)
            if stored is None:
                leaf_row["callers"].append(
                    {"parent": parent, "status": "no_stored_compiling_candidate"})
                continue
            ws = workspace.bootstrap(repo, parent)
            parent_target_asm = workspace.target_asm(ws, parent)
            activity = return_contract_activity(
                parent_target_asm, function, width)
            stem = f"{run_id}_{function}_{parent}_inferred"
            baseline = workspace.score(
                ws, repo, stem + "_baseline", stored["source"], conn=conn,
                func=parent, strategy="abi-inference-parent-baseline",
                run_id=run_id, extra={"leaf": function,
                                      "origin_attempt_id": stored["attempt_id"]})
            baseline_asm = ws / f"{stem}_baseline_object_dump_normalized.s"
            baseline_hash = (_sha(baseline_asm.read_text(errors="replace"))
                             if baseline_asm.exists() else "")
            variants = []
            for return_type in (narrow_type, "u32"):
                proposal = _apply_inferred_prototype(
                    stored["source"], function, return_type)
                if proposal is None:
                    variants.append({"return_type": return_type,
                                     "status": "already_declared_or_not_called"})
                    continue
                candidate, action = proposal
                att = workspace.score(
                    ws, repo, f"{stem}_{return_type}", candidate, conn=conn,
                    func=parent, strategy="abi-inference-parent-prototype",
                    run_id=run_id,
                    extra={"leaf": function, "return_type": return_type,
                           "action": action,
                           "origin_attempt_id": stored["attempt_id"]})
                asm_path = ws / f"{stem}_{return_type}_object_dump_normalized.s"
                asm_hash = (_sha(asm_path.read_text(errors="replace"))
                            if asm_path.exists() else "")
                variants.append({
                    "return_type": return_type, "action": action,
                    "compiled": att.compiled, "score": att.score,
                    "exact": att.exact,
                    "compiler_error": att.compiler_stderr[:1200],
                    "score_delta": round(att.score - baseline.score, 6),
                    "assembly_changed": bool(
                        baseline_hash and asm_hash and baseline_hash != asm_hash),
                })
            leaf_row["callers"].append({
                "parent": parent,
                "origin_attempt_id": stored["attempt_id"],
                "baseline_score": baseline.score,
                "return_contract_activity": activity,
                "variants": variants,
            })
    return rows


def assessment(selected: list[dict], leaf_results: list[dict],
               transfer: list[dict]) -> dict[str, object]:
    exact = [row for row in leaf_results if row.get("exact")]
    callers = [caller for leaf in transfer for caller in leaf.get("callers", [])]
    variants = [variant for caller in callers
                for variant in caller.get("variants", [])]
    changed = sum(bool(row.get("assembly_changed")) for row in variants)
    exact_parents = sum(bool(row.get("exact")) for row in variants)
    improved = sum(float(row.get("score_delta") or 0) > 0 for row in variants)
    if exact_parents:
        status = "confirmed_exact_parent_transfer"
    elif changed:
        status = "confirmed_parent_codegen_delta_no_exact_parent"
    elif exact:
        status = "confirmed_leaf_yield_transfer_not_observed"
    else:
        status = "inconclusive_no_leaf_promotion"
    return {
        "status": status,
        "selected_leaves": len(selected),
        "exact_leaves": len(exact),
        "exact_leaf_functions": [row["function"] for row in exact],
        "model_calls": sum(int(row.get("model_calls") or 0)
                           for row in leaf_results),
        "cached_generations_replayed": sum(
            int(row.get("cache_replays") or 0) for row in leaf_results),
        "charged_generation_tokens": sum(
            int(row.get("charged_generation_tokens") or 0)
            for row in leaf_results),
        "leaf_wall_seconds": round(sum(
            float(row.get("wall_seconds") or 0) for row in leaf_results), 3),
        "stored_callers_examined": len(callers),
        "prototype_variants_compiled": len(variants),
        "parent_assembly_deltas": changed,
        "parent_score_improvements": improved,
        "exact_parent_promotions": exact_parents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--leaf-set", required=True, type=Path)
    parser.add_argument("--heldout-set", action="append", type=Path, default=[])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--max-leaves", type=int, default=8)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--think", default="low")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--num-predict", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=2026090100)
    parser.add_argument("--repair-candidates", type=int, default=40)
    parser.add_argument("--layout-candidates", type=int, default=12)
    parser.add_argument("--max-parents-per-leaf", type=int, default=12)
    parser.add_argument(
        "--replay-receipt", type=Path,
        help="replay the exact selected cohort from a prior receipt")
    parser.add_argument(
        "--cache-only", action="store_true",
        help="fail a draw on a cache miss instead of running inference")
    parser.add_argument(
        "--only", action="append", default=[],
        help="limit a scan/replay to named DEV functions")
    parser.add_argument(
        "--test-inferred-contracts", action="store_true",
        help="shadow-test ambiguous narrow-return annotations on DEV callers")
    args = parser.parse_args()
    if not args.scan_only and args.cache_dir is None:
        parser.error("--cache-dir is required for a generation run")
    if args.num_predict > 1600:
        parser.error("pilot refuses --num-predict above 1600")

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    out = args.out.expanduser().resolve()
    leaf_set = json.loads(args.leaf_set.read_text(encoding="utf-8"))
    heldout = heldout_names(args.heldout_set)
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    if args.replay_receipt:
        prior = json.loads(args.replay_receipt.read_text(encoding="utf-8"))
        prior_selected = prior.get("selected", [])
        selected = [{**row, "cohort_index": index}
                    for index, row in enumerate(prior_selected)
                    if str(row.get("function")) not in heldout]
        if len(selected) != len(prior_selected):
            raise SystemExit("replay receipt contains a held-out function")
        selected = selected[:args.max_leaves]
        scan = {
            "replayed_from": str(args.replay_receipt),
            "original_run_id": prior.get("run_id"),
            "ranked": prior_selected,
            "abi_sensitive_candidates": len(prior_selected),
            "heldout_excluded": [],
        }
    else:
        selected, scan = rank_pool(
            repo, conn, leaf_set, heldout, max_leaves=args.max_leaves)
        selected = [{**row, "cohort_index": index}
                    for index, row in enumerate(selected)]
    if args.only:
        wanted = set(args.only)
        selected = [row for row in selected
                    if str(row.get("function")) in wanted]
        missing = wanted - {str(row.get("function")) for row in selected}
        if missing:
            raise SystemExit(
                "requested function absent from selected DEV cohort: "
                + ", ".join(sorted(missing)))
    run_id = f"abi-leaf-{int(time.time())}"
    receipt = {
        "schema_version": 1,
        "kind": "abi_sensitive_leaf_flywheel_pilot",
        "run_id": run_id,
        "pre_registration": {
            "primary_leaf_outcome": "new oracle-exact leaves",
            "primary_transfer_outcome": "stored-parent assembly changes after exact prototype propagation",
            "prediction": "at least two of eight selected leaves become exact and at least one exact signature changes a stored caller's assembly",
            "generation_budget": args.max_leaves * args.num_predict,
            "one_seeded_model_draw_per_unsolved_selected_leaf": True,
            "no_parent_generation": True,
            "heldout_excluded_before_bootstrap": True,
            "posthoc_cohort_replay": bool(args.replay_receipt),
        },
        "configuration": {
            "repo": str(repo), "db": str(db),
            "leaf_set": str(args.leaf_set),
            "heldout_sets": [str(path) for path in args.heldout_set],
            "model": args.model, "think": args.think,
            "temperature": args.temperature,
            "num_predict": args.num_predict,
            "max_leaves": args.max_leaves,
            "seed_base": args.seed_base,
            "repair_candidates": args.repair_candidates,
            "layout_candidates": args.layout_candidates,
            "replay_receipt": (str(args.replay_receipt)
                               if args.replay_receipt else None),
            "cache_only": args.cache_only,
        },
        "scan": scan,
        "selected": selected,
        "leaf_results": [],
        "parent_transfer": [],
        "inferred_contract_transfer": [],
        "started_at": int(time.time()),
    }
    _write(out, receipt)
    print(f"ABI-sensitive candidates: {scan['abi_sensitive_candidates']}", flush=True)
    for index, row in enumerate(selected, 1):
        print(f"  {index}. {row['function']} score={row['abi_leverage_score']} "
              f"insns={row['insns']} callers={row['dev_callers']} "
              f"[{'; '.join(row['reasons'])}]", flush=True)
    if args.scan_only:
        receipt["assessment"] = {"status": "scan_only", "selected": len(selected)}
        receipt["completed_at"] = int(time.time())
        _write(out, receipt)
        conn.close()
        print(f"receipt: {out}")
        return

    endpoint = llm.host()
    for index, row in enumerate(selected):
        print(f"\n=== leaf {index + 1}/{len(selected)}: {row['function']} ===",
              flush=True)
        try:
            result = solve_leaf(
                repo, conn, endpoint, row, model=args.model,
                timeout=args.timeout, think=args.think,
                num_thread=args.num_thread, num_predict=args.num_predict,
                temperature=args.temperature,
                seed=args.seed_base + int(row.get("cohort_index", index)),
                cache_dir=args.cache_dir, run_id=run_id,
                repair_candidates=args.repair_candidates,
                layout_candidates=args.layout_candidates,
                cache_only=args.cache_only)
        except Exception as exc:
            result = {"function": row["function"], "exact": False,
                      "best_score": 0.0, "model_calls": 0,
                      "charged_generation_tokens": 0,
                      "error": f"{type(exc).__name__}: {str(exc)[:600]}"}
        receipt["leaf_results"].append(result)
        _write(out, receipt)
        verdict = "EXACT" if result.get("exact") else \
            f"{float(result.get('best_score') or 0):.3f}%"
        print(f"  -> {verdict}; charged tokens="
              f"{result.get('charged_generation_tokens', 0)}; "
              f"wall={result.get('wall_seconds', 0)}s", flush=True)

    receipt["parent_transfer"] = measure_parent_transfer(
        repo, conn, receipt["leaf_results"], heldout, run_id=run_id,
        max_parents_per_leaf=args.max_parents_per_leaf)
    if args.test_inferred_contracts:
        receipt["inferred_contract_transfer"] = \
            measure_inferred_contract_transfer(
                repo, conn, selected, heldout, run_id=run_id,
                max_parents_per_leaf=args.max_parents_per_leaf)
    receipt["assessment"] = assessment(
        selected, receipt["leaf_results"], receipt["parent_transfer"])
    receipt["completed_at"] = int(time.time())
    _write(out, receipt)
    conn.close()
    print("\n" + json.dumps(receipt["assessment"], indent=2), flush=True)
    print(f"receipt: {out}")


if __name__ == "__main__":
    main()
