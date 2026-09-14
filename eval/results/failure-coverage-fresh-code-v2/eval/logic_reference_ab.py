"""Equal-budget logic reconstruction with binary, LOFO, and LOTO context.

Each arm receives the same frozen parent, target assembly, exact residual,
model, seed schedule, and output-token cap. The only manipulated variable is
the context regime. Non-exact candidates are selected with
``logic.quality_key``; verifier exactness remains the terminal authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from kb import attempts as attempt_receipts
from eval import agentrepair
from solver import llm, logic, reference_teacher, refine, residual, workspace
from tools import n64_corpus


SCHEMA_VERSION = 1
PROMPT_VERSION = 5
ARMS = ("binary_only", "leave_one_function_out", "leave_one_tu_out")
FORBIDDEN_SOURCE = re.compile(
    r"(?:\b(?:GLOBAL_ASM|INCLUDE_ASM|__asm__)\b|\basm\s*\(|\.incbin\b|"
    r"^\s*#\s*include\s+(?![\"<]common\.h[\">]))", re.I | re.M)


PROMPT = """\
Reconstruct the logic of one C function from its target MIPS assembly. The
current C is a compilable but incomplete/non-exact starting point. Return an
independently revised definition of the TARGET FUNCTION ONLY. The evaluator
will keep the known-compiling file scaffold and splice in only your function
body.

Primary goal for this experiment:
1. recover calls and their order;
2. recover non-stack reads, writes, widths, and offsets;
3. recover branches, loops, and control structure;
4. recover broad instruction-selection shape.

Byte exactness is welcome and remains the terminal verifier, but do not spend
the answer on register allocation polish while major logic is absent.

Rules:
- Output only the complete target function definition in one ```c block, with
  no prose, includes, helper definitions, typedefs, macros, or externs.
- Use C89; put declarations at the start of a function or block.
- `common.h` provides s8/u8/s16/u16/s32/u32, not stdint names such as
  int32_t or uint32_t.
- Use only types, macros, globals, and prototypes already present in CURRENT C.
  The evaluator mechanically preserves that known-compiling scaffold and its
  exact function signature; do not rely on guessed struct fields or new types.
- No inline assembly, GLOBAL_ASM, INCLUDE_ASM, or target/reference lookup.
- Preserve the function name and externally visible signature.
- Exact sibling sources below are analogies, not proof of target behavior.
- The diff uses `-` for TARGET and `+` for CURRENT C.

CONTEXT REGIME: {arm}

TARGET ASSEMBLY:
```
{assembly}
```

CURRENT C:
```c
{source}
```

EXACTNESS-FIRST RESIDUAL (reported, not the non-exact rank):
```json
{residual}
```

CURRENT COMPILER ERROR:
```
{compiler_error}
```

CURRENT INSTRUCTION DIFF:
```
{instruction_diff}
```

BINARY MODULE EVIDENCE:
```json
{logic_context}
```
{teacher_context}
Return the reconstructed target function definition now.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: dict, field: str) -> str:
    unsigned = dict(value)
    unsigned.pop(field, None)
    return _sha(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")))


def load_logic_packet(path: Path, function: str) -> dict:
    packet = json.loads(path.read_text(encoding="utf-8"))
    if packet.get("kind") != "logic-first-module-packet":
        raise ValueError(f"not a logic-first module packet: {path}")
    if packet.get("function") != function:
        raise ValueError(f"logic packet belongs to {packet.get('function')}")
    if packet.get("packet_digest") != _json_digest(packet, "packet_digest"):
        raise ValueError(f"logic packet digest is invalid: {path}")
    if packet.get("policies", {}).get("target_reference_c_available") is not False:
        raise ValueError("binary logic packet claims target reference C")
    return packet


def _teacher_prompt(packet: dict | None) -> str:
    if packet is None:
        return "\nREFERENCE TEACHER: disabled for the binary-only control.\n"
    context = {
        "regime": packet["regime"],
        "claim_policy": packet["policies"],
        "retrieval": packet["retrieval"],
        "exact_sibling_examples": packet["examples"],
        "unaligned_sibling_source_blocks": packet["matched_source_blocks"],
    }
    return ("\nFINISHED-DECOMP TEACHER CONTEXT (target excluded):\n"
            "```json\n" + json.dumps(context, indent=2) + "\n```\n")


def build_prompt(*, arm: str, assembly: str, source: str,
                 residual_packet: residual.ResidualPacket,
                 logic_packet: dict, teacher_packet: dict | None,
                 compiler_error: str = "",
                 instruction_diff: str = "") -> str:
    if arm not in ARMS:
        raise ValueError(f"unknown context arm: {arm}")
    if (arm == "binary_only") != (teacher_packet is None):
        raise ValueError("teacher packet presence does not match arm")
    if teacher_packet is not None and teacher_packet["regime"] != arm:
        raise ValueError("teacher packet regime does not match arm")
    compact_logic = dict(logic_packet)
    compact_logic.pop("packet_digest", None)
    return PROMPT.format(
        arm=arm, assembly=assembly, source=source,
        residual=residual_packet.render(),
        compiler_error=compiler_error[:6000],
        instruction_diff=instruction_diff[:6000],
        logic_context=json.dumps(compact_logic, indent=2),
        teacher_context=_teacher_prompt(teacher_packet))


def _assembly(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def _function_parts(source: str, function: str) -> tuple[str, str, int, int]:
    records = [row for row in n64_corpus.extract_functions(source)
               if row["name"] == function]
    if len(records) != 1:
        raise ValueError(
            f"expected one {function} definition, found {len(records)}")
    definition = str(records[0]["definition"])
    body = str(records[0]["body"])
    definition_start = source.find(definition)
    body_in_definition = definition.find(body)
    if definition_start < 0 or body_in_definition < 0:
        raise ValueError(f"could not locate {function} body span")
    start = definition_start + body_in_definition
    return definition, body, start, start + len(body)


def function_prefill(source: str, function: str) -> str:
    """Pin the model response to the root's exact function signature."""
    definition, body, _start, _end = _function_parts(source, function)
    header = definition[:definition.find(body)].rstrip()
    return f"```c\n{header}\n{{\n"


def splice_generated_body(source: str, function: str,
                          generated: str) -> str:
    """Keep the root scaffold and replace only the selected function body."""
    _definition, _body, start, end = _function_parts(source, function)
    records = [row for row in n64_corpus.extract_functions(generated)
               if row["name"] == function]
    if len(records) != 1:
        raise ValueError("response has no unique target function definition")
    generated_body = str(records[0]["body"])
    return source[:start] + generated_body + source[end:]


def _assessment(ws: Path, function: str, tag: str,
                attempt: workspace.Attempt) -> logic.Assessment:
    target = _assembly(ws / "target_object_dump_normalized.s")
    if not target:
        target = workspace.target_asm(ws, function)
    candidate = (_assembly(ws / f"{tag}_object_dump_normalized.s")
                 if attempt.compiled else "")
    return logic.compare(target, candidate, attempt=attempt)


def _attempt_summary(attempt: workspace.Attempt,
                     assessment: logic.Assessment,
                     packet: residual.ResidualPacket) -> dict:
    return {
        "attempt_id": attempt.receipt_id,
        "compiled": bool(attempt.compiled),
        "exact": bool(attempt.exact),
        "weighted_progress_score": float(attempt.score),
        "stage": assessment.stage,
        "semantic_status": assessment.semantic_status,
        "logic_quality_key": list(logic.quality_key(assessment)),
        "metrics": assessment.metrics,
        "residual": packet.to_dict(),
    }


def _metric_delta(best: logic.Assessment, root: logic.Assessment) -> dict:
    names = ("call_sequence", "memory_effects", "control_structure",
             "opcode_sequence")
    return {name: round(best.metrics.get(name, 0.0)
                        - root.metrics.get(name, 0.0), 6)
            for name in names}


def _is_better(candidate: logic.Assessment, current: logic.Assessment) -> bool:
    return logic.quality_key(candidate) > logic.quality_key(current)


def _function_seed(seed: int, function: str) -> int:
    offset = int(hashlib.sha256(function.encode()).hexdigest()[:8], 16)
    return (seed + offset) % 2_147_483_647


def _load_root(conn: sqlite3.Connection, manifest: dict,
               function: str) -> tuple[str, int]:
    frozen = next((row for row in manifest.get("cluster", [])
                   if row.get("function") == function), None)
    if frozen is None:
        raise ValueError(f"{function} is not in the frozen logic manifest")
    attempt_id = int(frozen["attempt_id"])
    source = agentrepair._source_for_attempt(conn, attempt_id, function)
    if _sha(source) != frozen["source_sha256"]:
        raise ValueError(f"frozen source hash changed for {function}")
    return source, attempt_id


def run_arm(*, repo: Path, conn: sqlite3.Connection, function: str,
            source: str, parent: workspace.Attempt, parent_tag: str,
            logic_packet: dict, teacher_packet: dict | None, arm: str,
            model: str, endpoint: str, seeds: list[int], timeout: int,
            think: str, num_thread: int, temperature: float,
            num_predict: int, cache_dir: Path, run_id: str,
            artifact_dir: Path) -> dict:
    ws = workspace.bootstrap(repo, function)
    assembly = workspace.target_asm(ws, function)
    parent_object = ws / f"{parent_tag}.o" if parent.compiled else None
    parent_residual = residual.build(
        parent, target_asm=assembly, target_object=ws / "target.o",
        candidate_object=parent_object)
    root_assessment = _assessment(ws, function, parent_tag, parent)
    best_attempt = parent
    best_assessment = root_assessment
    best_packet = parent_residual
    best_source = source
    seen = {_sha(source)}
    statuses: list[str] = []
    calls = responses = recorded_tokens = charged_tokens = 0
    compiling_children = 0
    candidates: list[dict] = []
    prompt_hashes: list[str] = []
    prompt_chars: list[int] = []
    active_source = source
    active_attempt = parent
    active_packet = parent_residual
    config = {
        "arm": arm, "seeds": seeds, "temperature": temperature,
        "num_predict": num_predict, "think": think,
        "logic_rank": "solver.logic.quality_key",
        "terminal_success": "verifier exact=true only",
    }
    for index, draw_seed in enumerate(seeds, 1):
        prompt = build_prompt(
            arm=arm, assembly=assembly, source=active_source,
            residual_packet=active_packet, logic_packet=logic_packet,
            teacher_packet=teacher_packet,
            # Round one already has the focused residual. Only a later round
            # receives its child's raw feedback, avoiding a duplicated full
            # root diff that previously exhausted the response budget.
            compiler_error=(active_attempt.compiler_stderr
                            if index > 1 else ""),
            instruction_diff=(active_attempt.diff if index > 1 else ""))
        workspace.assert_uncontaminated(prompt, repo, function)
        prompt_hashes.append(_sha(prompt))
        prompt_chars.append(len(prompt))
        calls += 1
        started = time.time()
        try:
            text, meta = llm.generate(
                endpoint, model, prompt, timeout=timeout, think=think,
                num_thread=num_thread, temperature=temperature,
                num_predict=num_predict,
                prefill=function_prefill(active_source, function),
                seed=draw_seed,
                cache_dir=cache_dir,
                cache_namespace=f"logic-reference-ab-v{PROMPT_VERSION}-{arm}")
        except Exception as exc:
            wall_ms = int((time.time() - started) * 1000)
            attempt_receipts.record_model_proposal(
                conn, run_id=run_id,
                parent_attempt_id=active_attempt.receipt_id,
                prompt=prompt, raw_response=str(exc),
                status="generation-error", model=model,
                kind=f"logic-full-source-{arm}",
                sampling={"seed": draw_seed, "round": index},
                wall_ms=wall_ms)
            statuses.append("generation-error")
            continue
        responses += 1
        wall_ms = int((time.time() - started) * 1000)
        token_count = int(meta.get("eval_count", 0) or 0)
        recorded_tokens += token_count
        if not meta.get("_cache_hit"):
            charged_tokens += token_count
        generated = llm.extract_c(text)
        code = ""
        if generated:
            try:
                code = splice_generated_body(
                    active_source, function, generated)
            except ValueError:
                pass
        if llm.is_refusal(text) or llm.is_refusal(generated):
            status = "refusal"
        elif not generated or not code or FORBIDDEN_SOURCE.search(generated):
            status = "invalid"
        elif _sha(code) in seen:
            status = "duplicate"
        else:
            status = "valid"
        proposal_id = attempt_receipts.record_model_proposal(
            conn, run_id=run_id,
            parent_attempt_id=active_attempt.receipt_id,
            prompt=prompt, raw_response=text, status=status, model=model,
            kind=f"logic-full-source-{arm}", sampling={
                "seed": draw_seed, "round": index,
                "temperature": temperature,
                "cache_hit": bool(meta.get("_cache_hit")),
                "cache_key": meta.get("_cache_key"),
                "done_reason": meta.get("done_reason")},
            wall_ms=wall_ms, token_cost=token_count)
        statuses.append(status)
        if status != "valid":
            continue
        seen.add(_sha(code))
        safe_arm = arm.replace("leave_one_", "")
        tag = f"{function}_logic_ref_{safe_arm}_{index}_{time.time_ns()}"
        attempt = workspace.score(
            ws, repo, tag, code, conn=conn, func=function, iteration=index,
            strategy=f"logic-reference-{arm}", model=model, prompt=prompt,
            temperature=temperature, wall_ms=wall_ms, run_id=run_id,
            token_cost=token_count,
            parent_attempt_id=active_attempt.receipt_id,
            relation=f"logic-context-{arm}",
            action="sequential-function-body-reconstruction",
            feedback=(active_attempt.compiler_stderr
                      if not active_attempt.compiled else active_attempt.diff),
            run_kind="logic-reference-ab",
            run_config=config, extra={
                "seed": draw_seed, "proposal_id": proposal_id,
                "cache_hit": bool(meta.get("_cache_hit"))},
            raw_response=text, extract_status="full-source",
            done_reason=meta.get("done_reason", ""))
        if attempt.receipt_id:
            attempt_receipts.link_model_proposal(
                conn, proposal_id, attempt.receipt_id)
        compiling_children += int(attempt.compiled)
        object_path = ws / f"{tag}.o" if attempt.compiled else None
        candidate_packet = residual.build(
            attempt, target_asm=assembly, target_object=ws / "target.o",
            candidate_object=object_path)
        candidate_assessment = _assessment(ws, function, tag, attempt)
        candidate_path = artifact_dir / f"{function}.{arm}.draw{index}.c"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(code, encoding="utf-8")
        candidate_summary = _attempt_summary(
            attempt, candidate_assessment, candidate_packet)
        candidate_summary.update({
            "draw": index, "seed": draw_seed,
            "source_sha256": _sha(code), "source_path": str(candidate_path),
            "compiler_stderr": (attempt.compiler_stderr or "")[:2000],
        })
        candidates.append(candidate_summary)
        if _is_better(candidate_assessment, best_assessment):
            best_attempt = attempt
            best_assessment = candidate_assessment
            best_packet = candidate_packet
            best_source = code
        # Follow a child even after regression: its exact compiler/diff
        # observation is the next experiment. The protected best cannot move
        # backwards because it is tracked separately.
        active_source = code
        active_attempt = attempt
        active_packet = candidate_packet
        if attempt.exact:
            break

    best_path = artifact_dir / f"{function}.{arm}.best.c"
    best_path.parent.mkdir(parents=True, exist_ok=True)
    best_path.write_text(best_source, encoding="utf-8")
    root_summary = _attempt_summary(parent, root_assessment, parent_residual)
    best_summary = _attempt_summary(
        best_attempt, best_assessment, best_packet)
    return {
        "arm": arm, "calls_attempted": calls, "responses": responses,
        "recorded_tokens": recorded_tokens, "charged_tokens": charged_tokens,
        "compiling_children": compiling_children, "statuses": statuses,
        "candidates": candidates,
        "prompt_sha256s": prompt_hashes, "prompt_chars": prompt_chars,
        "teacher_packet_digest": (teacher_packet.get("packet_digest")
                                  if teacher_packet else None),
        "root": root_summary, "best": best_summary,
        "stage_improved": (logic.quality_key(best_assessment)
                           > logic.quality_key(root_assessment)),
        "metric_delta": _metric_delta(best_assessment, root_assessment),
        "best_source_sha256": _sha(best_source),
        "best_source_path": str(best_path),
    }


def aggregate(results: list[dict]) -> dict:
    completed = [row for row in results if row.get("status") == "complete"]
    output = {}
    for arm in ARMS:
        rows = [row["arms"][arm] for row in completed if arm in row["arms"]]
        delta_names = ("call_sequence", "memory_effects", "control_structure",
                       "opcode_sequence")
        output[arm] = {
            "functions": len(rows),
            "calls_attempted": sum(row["calls_attempted"] for row in rows),
            "responses": sum(row["responses"] for row in rows),
            "charged_tokens": sum(row["charged_tokens"] for row in rows),
            "compiling_children": sum(row["compiling_children"] for row in rows),
            "exact": sum(row["best"]["exact"] for row in rows),
            "logic_improvements": sum(row["stage_improved"] for row in rows),
            "best_stages": dict(sorted(Counter(
                row["best"]["stage"] for row in rows).items())),
            "mean_metric_delta": {
                name: (round(sum(row["metric_delta"][name] for row in rows)
                             / len(rows), 6) if rows else 0.0)
                for name in delta_names},
            "byte_distance_improvements": sum(
                (row["best"]["residual"].get("positional_byte_distance")
                 is not None and
                 row["root"]["residual"].get("positional_byte_distance")
                 is not None and
                 row["best"]["residual"]["positional_byte_distance"]
                 < row["root"]["residual"]["positional_byte_distance"])
                for row in rows),
        }
    return {
        "completed_functions": len(completed), "arms": output,
        "interpretation": (
            "Pilot causal comparison. Logic rank excludes weighted score and "
            "byte distance; exact remains terminal. Same-game teacher arms "
            "are not cold-start or cross-game evidence."),
    }


def audit_run(conn: sqlite3.Connection, sets: Path,
              receipt: dict) -> dict:
    """Cross-check receipt membership, lineage, proposals, and held-out use."""
    run_id = str(receipt.get("run_id") or "")
    rows = conn.execute(
        "SELECT a.id,f.name,a.parent_attempt_id FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr WHERE a.run_id=? ORDER BY a.id",
        (run_id,)).fetchall()
    edge_count = int(conn.execute(
        "SELECT COUNT(*) FROM attempt_edges e JOIN attempts a "
        "ON a.id=e.child_attempt_id WHERE a.run_id=?", (run_id,)).fetchone()[0])
    proposals = conn.execute(
        "SELECT status,child_attempt_id FROM model_proposals "
        "WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    receipt_ids = set()
    receipt_names = set()
    for result in receipt.get("results", []):
        receipt_names.add(str(result["function"]))
        if result.get("parent_attempt_id") is not None:
            receipt_ids.add(int(result["parent_attempt_id"]))
        for arm in result.get("arms", {}).values():
            receipt_ids.update(int(row["attempt_id"])
                               for row in arm.get("candidates", [])
                               if row.get("attempt_id") is not None)
    heldout = set()
    paths = sorted(sets.glob("*.json")) if sets.is_dir() else [sets]
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        heldout.update(str(row["function"])
                       for row in value.get("heldout", [])
                       if isinstance(row, dict) and row.get("function"))
    database_ids = {int(row[0]) for row in rows}
    database_names = {str(row[1]) for row in rows}
    result = {
        "attempts": len(rows), "attempt_edges": edge_count,
        "model_proposals": len(proposals),
        "linked_model_proposals": sum(row[1] is not None for row in proposals),
        "proposal_statuses": dict(sorted(Counter(
            str(row[0]) for row in proposals).items())),
        "all_attempts_have_parents": all(row[2] is not None for row in rows),
        "receipt_attempt_ids_match_database": receipt_ids == database_ids,
        "receipt_names_match_database": receipt_names == database_names,
        "heldout_overlap": sorted(database_names & heldout),
    }
    result["clean"] = (
        result["all_attempts_have_parents"]
        and edge_count == len(rows)
        and result["receipt_attempt_ids_match_database"]
        and result["receipt_names_match_database"]
        and not result["heldout_overlap"])
    return result


def _find_logic_packet(packet_dir: Path, function: str) -> Path:
    matches = []
    for path in packet_dir.glob("logic-first-packet-*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if value.get("function") == function:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"no unique logic packet for {function}")
    return matches[0]


def run_experiment(*, repo: Path, db: Path, sets: Path, manifest_path: Path,
                   baseline_path: Path, packet_dir: Path, out: Path,
                   artifact_dir: Path, functions: list[str], model: str,
                   endpoint: str, draws: int, timeout: int, think: str,
                   num_thread: int, temperature: float, num_predict: int,
                   seed: int, cache_dir: Path) -> dict:
    if draws <= 0:
        raise ValueError("draws must be positive")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_digest") != _json_digest(
            manifest, "manifest_digest"):
        raise ValueError("logic manifest digest is invalid")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_names = {row["function"] for row in baseline.get("functions", [])}
    if not functions or any(name not in baseline_names for name in functions):
        raise ValueError("all selected functions must be in the frozen baseline")

    packet_fingerprints = {}
    for function in functions:
        logic_packet = load_logic_packet(
            _find_logic_packet(packet_dir, function), function)
        lofo = reference_teacher.load_packet(
            packet_dir / "reference_teacher" /
            f"logic-first-reference-{function}-lofo-v1.json")
        loto = reference_teacher.load_packet(
            packet_dir / "reference_teacher" /
            f"logic-first-reference-{function}-loto-v1.json")
        packet_fingerprints[function] = {
            "binary_only": logic_packet["packet_digest"],
            "leave_one_function_out": lofo["packet_digest"],
            "leave_one_tu_out": loto["packet_digest"],
        }

    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": "logic-reference-context-ab", "functions": functions,
        "arms": list(ARMS), "sequential_rounds_per_arm": draws,
        "calls_per_function": draws * len(ARMS), "model": model,
        "temperature": temperature, "num_predict": num_predict,
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "seed": seed, "manifest_digest": manifest["manifest_digest"],
        "prompt_version": PROMPT_VERSION,
        "prompt_template_sha256": _sha(PROMPT),
        "baseline_run_id": baseline.get("run_id"),
        "packet_fingerprints": packet_fingerprints,
        "endpoint": endpoint, "cache_dir": str(cache_dir),
        "logic_rank": "solver.logic.quality_key",
        "byte_metrics": "shadow-only; verifier exact is terminal",
        "arm_order": "rotating Latin order across functions",
    }
    config_digest = _sha(json.dumps(config, sort_keys=True))[:20]
    run_id = f"logic-reference-ab-{config_digest}"
    receipt = {
        "schema_version": SCHEMA_VERSION, "kind": config["kind"],
        "run_id": run_id, "created_at": int(time.time()), "config": config,
        "config_digest": config_digest, "status": "running", "results": [],
    }
    if out.exists():
        prior = json.loads(out.read_text(encoding="utf-8"))
        if prior.get("config_digest") != config_digest:
            raise ValueError("output belongs to a different configuration")
        receipt = prior

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    attempt_receipts.start_run(
        conn, run_id, kind=config["kind"], model=model, config=config)
    # A half-written function is restarted as a unit. Identical prompts and
    # seeds replay from the generation cache, while all three final arms share
    # one fresh parent instead of mixing parent objects across resumptions.
    receipt["results"] = [
        row for row in receipt.get("results", [])
        if row.get("status") == "complete"]
    done = {row["function"] for row in receipt["results"]}
    try:
        for function_index, function in enumerate(functions):
            if function in done:
                continue
            agentrepair._refuse_frozen_heldout(sets, function)
            source, original_attempt_id = _load_root(conn, manifest, function)
            ws = workspace.bootstrap(repo, function)
            parent_tag = f"{function}_logic_ref_parent_{time.time_ns()}"
            parent = workspace.score(
                ws, repo, parent_tag, source, conn=conn, func=function,
                iteration=0, strategy="logic-reference-parent", model=model,
                run_id=run_id, parent_attempt_id=original_attempt_id,
                relation="logic-reference-root-reverify",
                action="reverify identical root for three context arms",
                run_kind=config["kind"], run_config=config)
            logic_packet = load_logic_packet(
                _find_logic_packet(packet_dir, function), function)
            teacher_packets = {
                "binary_only": None,
                "leave_one_function_out": reference_teacher.load_packet(
                    packet_dir / "reference_teacher" /
                    f"logic-first-reference-{function}-lofo-v1.json"),
                "leave_one_tu_out": reference_teacher.load_packet(
                    packet_dir / "reference_teacher" /
                    f"logic-first-reference-{function}-loto-v1.json"),
            }
            arm_order = list(ARMS[function_index:] + ARMS[:function_index])
            draw_seed = _function_seed(seed, function)
            seeds = [draw_seed + index for index in range(draws)]
            row = {
                "function": function, "status": "running",
                "original_attempt_id": original_attempt_id,
                "parent_attempt_id": parent.receipt_id,
                "arm_order": arm_order, "seeds": seeds, "arms": {},
            }
            receipt["results"].append(row)
            for arm in arm_order:
                print(f"[{function}] {arm} ({draws} draw(s))", flush=True)
                row["arms"][arm] = run_arm(
                    repo=repo, conn=conn, function=function, source=source,
                    parent=parent, parent_tag=parent_tag,
                    logic_packet=logic_packet,
                    teacher_packet=teacher_packets[arm], arm=arm,
                    model=model, endpoint=endpoint, seeds=seeds,
                    timeout=timeout, think=think, num_thread=num_thread,
                    temperature=temperature, num_predict=num_predict,
                    cache_dir=cache_dir, run_id=run_id,
                    artifact_dir=artifact_dir)
                receipt["aggregate"] = aggregate(receipt["results"])
                agentrepair._atomic_json(out, receipt)
            row["status"] = "complete"
            receipt["aggregate"] = aggregate(receipt["results"])
            agentrepair._atomic_json(out, receipt)
        receipt["status"] = "complete"
        receipt["completed_at"] = int(time.time())
        receipt["aggregate"] = aggregate(receipt["results"])
        receipt["audit"] = audit_run(conn, sets, receipt)
        agentrepair._atomic_json(out, receipt)
        return receipt
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--packet-dir", type=Path, default=Path("eval/results"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--function", action="append", dest="functions",
                        required=True)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--draws", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--num-predict", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_experiment(
        repo=args.repo.expanduser().resolve(),
        db=args.db.expanduser().resolve(), sets=args.sets.expanduser().resolve(),
        manifest_path=args.manifest.expanduser().resolve(),
        baseline_path=args.baseline.expanduser().resolve(),
        packet_dir=args.packet_dir.expanduser().resolve(),
        out=args.out.expanduser().resolve(),
        artifact_dir=args.artifact_dir.expanduser().resolve(),
        functions=args.functions, model=args.model, endpoint=llm.host(),
        draws=args.draws, timeout=args.timeout, think=args.think,
        num_thread=args.num_thread, temperature=args.temperature,
        num_predict=args.num_predict, seed=args.seed,
        cache_dir=args.cache_dir.expanduser().resolve())
    print(json.dumps(result["aggregate"], indent=2))


if __name__ == "__main__":
    main()
