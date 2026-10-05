"""Audit trajectory records for training-pair integrity.

The model proposes, the database remembers why, the compiler decides -- and the
training set is built from what the database remembered.  That last step has a
failure mode nothing else catches: the row can be internally consistent and still
be a lie about what the model was shown.  `attempts.parent_attempt_id` records
which candidate an attempt was derived from, and if that candidate is not the one
rendered into `prompt_context`, every (prompt, completion) pair built from the row
teaches the model to map a prompt to an unrelated continuation.

This module answers eight questions about a trajectory database, in order of how
badly a wrong answer hurts training:

1. lineage integrity      -- do the edge endpoints exist, compile, and improve?
2. THE CRITICAL CHECK     -- is the recorded parent actually in the child's prompt?
3. independent vs repair  -- do roots and refinement edges match their roles?
4. exact model inputs     -- which rows still have the prompt and the response?
5. compiler identity      -- which rows record what compiled them?
6. candidate hashes       -- do `source_sha256` values match `sha256(source_code)`?
7. verifier results       -- how many rows are byte-exact, and how many compiled?
8. failure logging        -- do failures carry a reason, or vanish silently?

On question 2, a word about the two checks, because the difference decides
whether rows get thrown away.  The literal check asks whether the parent's
`source_code` appears verbatim in the prompt.  Some prompt renderers -- the
`differential-debugger-*` strategies in the research KB -- prefix every line with
a `  12 | ` gutter and tell the model "the `NN |` prefixes are annotations, not
source text".  For those rows the parent IS the candidate the model was shown,
but the literal check fails, and a naive auditor would exclude 213 correct edges
along with the 3 genuinely broken ones.  So this module reports the literal
verdict AND the gutter-stripped verdict, and only the genuine failures are
excluded from the training-usable count.  It also guards the "some other attempt
of this function is in the prompt instead" branch with a minimum source length,
because `#include "common.h"` (19 characters, attempt 2643 in the research KB)
appears in essentially every prompt and would otherwise be reported as a
recovered match 109 times.

A zero here is suspicious, not reassuring.  If `prompt_context` is empty for most
rows the check cannot run at all, which is a different thing from passing, so a
row count below the checkable threshold emits an explicit `check-cannot-run`
finding rather than a clean bill of health.

Read-only, always: every database is opened with `mode=ro` and never written.

    python3 -m eval.trajectory_audit \
        --kb ~/decomp/kb-sbk1.sqlite \
        --scratch ~/decomp/posttraining-m1-20260920/collect.sqlite \
        --json-out eval/results/local-posttraining-20260920/audit.json \
        --md-out eval/results/local-posttraining-20260920/AUDIT.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

# A source file shorter than this is not a candidate worth matching: refusal text,
# a bare `#include "common.h"`, or a stray asm line all collide with prompt boilerplate.
DEFAULT_MIN_CANDIDATE_LEN = 200

# `  12 | ` / `12 | ` / `12|` line-number gutter used by the span/patch renderers.
GUTTER_RE = re.compile(r"^\s*\d+\s*\|[ ]?")
FENCE_RE = re.compile(r"^\s*```")

# Relations the schema comment declares. Anything else is reported, not rejected:
# the KB in practice uses many more (dag-pipeline-frozen-root, agent-repair-root, ...).
CANONICAL_RELATIONS = frozenset({"refine", "deterministic-repair", "model-repair", "derive"})
REPAIR_RELATIONS = frozenset({"refine", "deterministic-repair", "model-repair"})

# A parent-without-a-prompt means the check cannot run for that row. When this
# fraction of parented rows is unverifiable the audit says so loudly, because a
# clean result on the remaining slice is not evidence about the whole database.
LOW_COVERAGE_RATIO = 0.5
LOW_COVERAGE_MIN_ROWS = 50


# --------------------------------------------------------------------------- io


def _ro_uri(path: str | os.PathLike[str]) -> str:
    """Build a read-only SQLite URI that works on both POSIX and Windows paths."""
    p = Path(path).absolute()
    try:
        uri = p.as_uri()
    except ValueError:  # pragma: no cover - relative/drive-less paths
        uri = "file:" + quote(str(p).replace("\\", "/"))
    sep = "&" if "?" in uri else "?"
    return f"{uri}{sep}mode=ro"


def connect_readonly(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open *path* read-only. Never opens a writable handle, never creates the file."""
    con = sqlite3.connect(_ro_uri(path), uri=True)
    con.row_factory = sqlite3.Row
    return con


# ------------------------------------------------------------------- primitives


def strip_gutter(text: str) -> tuple[str, int]:
    """Remove a leading `NN | ` line-number gutter. Returns (text, lines_stripped).

    A true no-op when no gutter is found: the input is returned unchanged rather
    than re-joined through `splitlines`, which would silently drop a trailing
    newline and make a verbatim comparison fail for the wrong reason.
    """
    out = []
    hits = 0
    for line in text.splitlines():
        m = GUTTER_RE.match(line)
        if m:
            hits += 1
            out.append(line[m.end():])
        else:
            out.append(line)
    if not hits:
        return text, 0
    return "\n".join(out), hits


def fenced_blocks(text: str) -> list[str]:
    """Bodies of ```-fenced blocks, in order.

    Pairing is positional, so an UNBALANCED fence count shifts every later block
    and can push a real payload outside any detected block. Real prompts do hit
    that: `differential-debugger-*` prompts embed model JSON that itself contains
    ``` runs, which is why the parent-in-prompt check does not rely on this
    function -- see `gutter_rendering_match`.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if FENCE_RE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def gutter_rendering_match(prompt: str, target: str) -> bool:
    """Is `target` in `prompt` once a per-line `NN | ` gutter is removed?

    Fence-independent on purpose.  Stripping a numeric prefix can only remove
    text, never invent it, so a hit means the prompt really does carry the
    target's text with line-number annotations -- which is exactly what the
    `differential-debugger-*` renderers do, and what makes a literal verbatim
    check report a false failure.

    The matched region must itself carry gutters on at least half its lines
    (minimum two), so a chance collision in unrelated prose cannot qualify.
    """
    if not target or not prompt:
        return False
    lines = prompt.splitlines()
    stripped = "\n".join(GUTTER_RE.sub("", line) for line in lines)
    probe = target
    idx = stripped.find(probe)
    if idx < 0:
        probe = target.strip()
        if not probe:
            return False
        idx = stripped.find(probe)
        if idx < 0:
            return False
    span_first = stripped.count("\n", 0, idx)
    span_last = stripped.count("\n", 0, idx + len(probe))
    if span_last - span_first < 2:
        return False
    region = lines[span_first:span_last + 1]
    gutter_lines = sum(1 for line in region if GUTTER_RE.match(line))
    return gutter_lines >= max(2, len(region) // 2)


def parse_json_object(raw: str | None) -> dict | None:
    """Parse a JSON object defensively. Returns None for NULL, empty, invalid, or non-dict."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:  # pragma: no cover
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def dig(obj: object, dotted: str) -> object:
    """Walk a dotted path through nested dicts; None if any hop is missing."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def role_of(sampling: dict | None) -> str | None:
    """New-style generation role: `sampling.generation.sampling.role`."""
    if not isinstance(sampling, dict):
        return None
    role = dig(sampling, "generation.sampling.role")
    return role if isinstance(role, str) and role else None


def compiler_identity(sampling: dict | None) -> tuple[str | None, str | None]:
    """(identity, key_used). Recognises `compiler_recipe`, then `recipe`, then `compiler`."""
    if not isinstance(sampling, dict):
        return None, None
    for key in ("compiler_recipe", "recipe", "compiler"):
        if key not in sampling:
            continue
        val = sampling[key]
        if isinstance(val, dict):
            settings = val.get("settings")
            settings = settings if isinstance(settings, dict) else {}
            target = val.get("target")
            opt = settings.get("C_OPT")
            mips = settings.get("C_MIPS")
            bits = [str(b) for b in (target, opt, mips) if b]
            if bits:
                return f"{key}:" + "/".join(bits), key
            parts = sorted(val.keys())
            if parts:
                return f"{key}:" + ",".join(parts[:6]), key
            return f"{key}:<empty-object>", key
        return f"{key}:{val!r}"[:160], key
    return None, None


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bucket_delta(delta: float) -> str:
    """Score is asm-differ: 0 is byte-exact and lower is better."""
    if delta < 0:
        return "improved"
    if delta > 0:
        return "worse"
    return "unchanged"


# ---------------------------------------------------------------- the auditor


def audit_db(
    path: str | os.PathLike[str],
    *,
    examples: int = 10,
    min_candidate_len: int = DEFAULT_MIN_CANDIDATE_LEN,
    top_runs: int = 20,
) -> dict:
    """Audit one trajectory database. Read-only.

    `min_candidate_len` guards the "another attempt is in the prompt instead"
    branch against trivial string collisions. `examples` caps the per-check sample
    lists; `top_runs` caps the per-`run_id` table.
    """
    path = str(path)
    result: dict = {
        "db": {"path": path, "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0},
        "totals": {"attempts": 0, "edges": 0},
        "findings": [],
        "warnings": [],
    }
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    con = connect_readonly(path)
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for required in ("attempts", "attempt_edges"):
            if required not in tables:
                raise ValueError(f"{path}: missing table {required!r}")

        # ---- load attempts (sources loaded lazily per function to bound memory)
        attempts: dict[int, dict] = {}
        for row in cur.execute(
            """SELECT id, func_addr, iteration, source_code, prompt_context, compiled,
                      compiler_stderr, score, strategy, model, sampling, token_cost,
                      exact, extract_status, done_reason, run_id, parent_attempt_id,
                      source_sha256, prompt_sha256, raw_response
               FROM attempts"""
        ):
            attempts[row["id"]] = {
                "id": row["id"],
                "func_addr": row["func_addr"],
                "iteration": row["iteration"],
                "source_code": row["source_code"] or "",
                "raw_response": row["raw_response"] or "",
                "prompt_context": row["prompt_context"] or "",
                "compiled": _as_int(row["compiled"]),
                "compiler_stderr": row["compiler_stderr"] or "",
                "score": row["score"],
                "strategy": row["strategy"] or "",
                "model": row["model"] or "",
                "sampling": row["sampling"],
                "token_cost": _as_int(row["token_cost"]) or 0,
                "exact": _as_int(row["exact"]),
                "extract_status": row["extract_status"],
                "done_reason": row["done_reason"],
                "run_id": row["run_id"],
                "parent_attempt_id": row["parent_attempt_id"],
                "source_sha256": row["source_sha256"],
                "prompt_sha256": row["prompt_sha256"],
            }
        result["totals"]["attempts"] = len(attempts)

        func_names: dict[int, str] = {}
        if "functions" in tables:
            for r in cur.execute("SELECT addr, name FROM functions"):
                func_names[r["addr"]] = r["name"]

        # sampling is a JSON string; parse once, defensively.
        sampling_objs: dict[int, dict | None] = {}
        invalid_sampling = 0
        for aid, a in attempts.items():
            obj = parse_json_object(a["sampling"])
            if obj is None and not _is_blank(a["sampling"]):
                invalid_sampling += 1
            sampling_objs[aid] = obj

        # group attempts by function for the parent-in-prompt recovery search
        by_func: dict[int, list[int]] = {}
        for aid, a in attempts.items():
            by_func.setdefault(a["func_addr"], []).append(aid)

        # ------------------------------- Q3: independent roots vs refinement edges
        edge_rows = list(
            cur.execute(
                """SELECT parent_attempt_id AS pid, child_attempt_id AS cid,
                          relation, action, feedback, created_at
                   FROM attempt_edges"""
            )
        )
        result["totals"]["edges"] = len(edge_rows)
        edge_children = {r["cid"] for r in edge_rows}
        edge_parents = {r["pid"] for r in edge_rows}

        with_parent = {aid for aid, a in attempts.items() if a["parent_attempt_id"] is not None}
        independent_roots = {
            aid for aid, a in attempts.items()
            if a["parent_attempt_id"] is None and aid not in edge_children
        }
        no_edge_at_all = {
            aid for aid, a in attempts.items()
            if aid not in edge_children and aid not in edge_parents
        }

        role_counts: Counter = Counter()
        roles: dict[int, str | None] = {}
        for aid, a in attempts.items():
            r = role_of(sampling_objs[aid])
            roles[aid] = r
            if r is not None:
                role_counts[r] += 1

        role_violations = {
            "repair_without_parent": [],
            "independent_with_parent": [],
            "independent_in_edge": [],
            "repair_without_edge": [],
        }
        for aid, r in roles.items():
            if r is None:
                continue
            has_parent = attempts[aid]["parent_attempt_id"] is not None
            if r == "repair":
                if not has_parent:
                    role_violations["repair_without_parent"].append(aid)
                if aid not in edge_children:
                    role_violations["repair_without_edge"].append(aid)
            elif r == "independent":
                if has_parent:
                    role_violations["independent_with_parent"].append(aid)
                if aid in edge_children:
                    role_violations["independent_in_edge"].append(aid)

        result["q3_roots_vs_edges"] = {
            "rows_with_parent_field": len(with_parent),
            "rows_without_parent_field": len(attempts) - len(with_parent),
            "independent_roots_no_parent_no_edge_child": len(independent_roots),
            "rows_in_no_edge_at_all": len(no_edge_at_all),
            "rows_that_are_edge_children": len(edge_children),
            "rows_that_are_edge_parents": len(edge_parents),
            "role_counts": dict(role_counts.most_common()),
            "role_available": bool(role_counts),
            "role_violations": {k: {"count": len(v), "example_ids": v[:10]}
                                for k, v in role_violations.items()},
        }

        # ------------------------------- Q1: lineage integrity per edge
        rel_counter: Counter = Counter()
        action_counter: Counter = Counter()
        non_canonical_counter: Counter = Counter()
        compiled_pair_counter: Counter = Counter()
        delta_buckets: Counter = Counter()
        delta_values: list[float] = []
        parent_missing: list[int] = []
        child_missing: list[int] = []
        non_canonical_relations: list[dict] = []
        empty_action_edges = 0
        repair_edges_without_prompt = 0
        role_edge_inconsistent: list[dict] = []
        self_loops = 0
        for r in edge_rows:
            pid, cid = r["pid"], r["cid"]
            rel = r["relation"] or ""
            rel_counter[rel] += 1
            action_counter[r["action"] or ""] += 1
            if not (r["action"] or ""):
                empty_action_edges += 1
            if pid == cid:
                self_loops += 1
            p = attempts.get(pid)
            c = attempts.get(cid)
            if p is None:
                parent_missing.append(pid)
            if c is None:
                child_missing.append(cid)
            if p is not None and c is not None:
                compiled_pair_counter[(p["compiled"], c["compiled"])] += 1
                if p["score"] is not None and c["score"] is not None:
                    d = float(c["score"]) - float(p["score"])
                    delta_values.append(d)
                    delta_buckets[_bucket_delta(d)] += 1
                if rel not in CANONICAL_RELATIONS:
                    # count every row; the example list is capped independently, so
                    # deriving the count from it would silently under-report
                    non_canonical_counter[rel] += 1
                    if len(non_canonical_relations) < examples:
                        non_canonical_relations.append(
                            {"child_id": cid, "parent_id": pid, "relation": rel,
                             "action": r["action"]})
                if rel in REPAIR_RELATIONS and _is_blank(c["prompt_context"]):
                    repair_edges_without_prompt += 1
                child_role = roles.get(cid)
                if child_role is not None:
                    if child_role == "independent":
                        role_edge_inconsistent.append(
                            {"child_id": cid, "parent_id": pid, "role": child_role, "relation": rel})
                    elif child_role == "repair" and rel not in REPAIR_RELATIONS:
                        role_edge_inconsistent.append(
                            {"child_id": cid, "parent_id": pid, "role": child_role, "relation": rel})

        both_compiled = compiled_pair_counter.get((1, 1), 0)
        result["q1_lineage"] = {
            "edges": len(edge_rows),
            "parent_row_missing": len(parent_missing),
            "parent_row_missing_examples": sorted(set(parent_missing))[:examples],
            "child_row_missing": len(child_missing),
            "child_row_missing_examples": sorted(set(child_missing))[:examples],
            "self_loops": self_loops,
            "both_endpoints_compiled": both_compiled,
            "endpoints_not_both_compiled": len(edge_rows) - both_compiled,
            "endpoint_compiled_states": {
                f"parent={k[0]},child={k[1]}": v for k, v in sorted(
                    compiled_pair_counter.items(), key=lambda kv: str(kv[0]))},
            "score_delta": {
                "definition": "child.score - parent.score (asm-differ; lower is better)",
                "n": len(delta_values),
                "improved": delta_buckets.get("improved", 0),
                "unchanged": delta_buckets.get("unchanged", 0),
                "worse": delta_buckets.get("worse", 0),
                "not_comparable": len(edge_rows) - len(delta_values),
                "min": min(delta_values) if delta_values else None,
                "max": max(delta_values) if delta_values else None,
                "mean": (sum(delta_values) / len(delta_values)) if delta_values else None,
            },
            "relations": dict(rel_counter.most_common()),
            "non_canonical_relations": dict(non_canonical_counter.most_common()),
            "non_canonical_relations_total": sum(non_canonical_counter.values()),
            "non_canonical_relations_examples": non_canonical_relations,
            "actions_top": dict(action_counter.most_common(20)),
            "edges_with_empty_action": empty_action_edges,
            "repair_edges_whose_child_has_no_prompt": repair_edges_without_prompt,
            "role_relation_inconsistent": {
                "count": len(role_edge_inconsistent),
                "examples": role_edge_inconsistent[:examples],
            },
        }

        # ------------------------------- Q2: THE CRITICAL CHECK
        checkable = [
            aid for aid, a in attempts.items()
            if a["parent_attempt_id"] is not None and a["prompt_context"] != ""
        ]
        verbatim = 0
        rendering_variant = 0
        other_attempt = 0
        genuinely_absent = 0
        parent_row_missing_for_check = 0
        trivial_collisions = 0
        trivial_example_ids: set[int] = set()
        flagged_by_strategy: Counter = Counter()
        flagged_by_run: Counter = Counter()
        checked_by_strategy: Counter = Counter()
        overflow: list[dict] = []
        ex_absent: list[dict] = []
        ex_other: list[dict] = []
        ex_render: list[dict] = []

        for cid in checkable:
            child = attempts[cid]
            pid = child["parent_attempt_id"]
            prompt = child["prompt_context"]
            parent = attempts.get(pid)
            if parent is None:
                parent_row_missing_for_check += 1
                continue
            checked_by_strategy[child["strategy"]] += 1
            parent_src = parent["source_code"]

            if parent_src and parent_src in prompt:
                verbatim += 1
                continue

            flagged_by_strategy[child["strategy"]] += 1
            flagged_by_run[child["run_id"] or "<NULL>"] += 1

            # Does ANY other attempt of this function appear in the prompt instead?
            best_other: tuple[int, int] | None = None  # (attempt_id, src_len)
            for other_id in by_func.get(child["func_addr"], []):
                if other_id == cid:
                    continue
                other_src = attempts[other_id]["source_code"]
                if not other_src:
                    continue
                if other_src in prompt:
                    if len(other_src) >= min_candidate_len:
                        if best_other is None or len(other_src) > best_other[1]:
                            best_other = (other_id, len(other_src))
                    else:
                        trivial_collisions += 1
                        trivial_example_ids.add(other_id)

            # Rendering-aware retry. Order matters: if the recorded parent IS the
            # candidate shown (even gutter-annotated) the edge is correct, and only
            # a parent that is genuinely missing makes the "what was shown instead"
            # question worth asking.
            gutter_match = gutter_rendering_match(prompt, parent_src)
            if gutter_match:
                rendering_variant += 1
                klass = "parent-in-prompt-line-numbered"
                if len(ex_render) < examples:
                    ex_render.append({
                        "child_id": cid, "parent_id": pid,
                        "func_addr": child["func_addr"],
                        "func_name": func_names.get(child["func_addr"]),
                        "strategy": child["strategy"], "run_id": child["run_id"],
                        "prompt_len": len(prompt), "other_attempt_id": None,
                    })
            elif best_other is not None:
                other_attempt += 1
                klass = "other-attempt-in-prompt"
                entry = {
                    "child_id": cid,
                    "parent_id": pid,
                    "func_addr": child["func_addr"],
                    "func_name": func_names.get(child["func_addr"]),
                    "strategy": child["strategy"],
                    "run_id": child["run_id"],
                    "prompt_len": len(prompt),
                    "other_attempt_id": best_other[0],
                    "other_attempt_src_len": best_other[1],
                }
                if len(ex_other) < examples:
                    ex_other.append(entry)
            else:
                genuinely_absent += 1
                klass = "parent-not-in-prompt"
                entry = {
                    "child_id": cid,
                    "parent_id": pid,
                    "func_addr": child["func_addr"],
                    "func_name": func_names.get(child["func_addr"]),
                    "strategy": child["strategy"],
                    "run_id": child["run_id"],
                    "prompt_len": len(prompt),
                    "other_attempt_id": None,
                    "other_attempt_src_len": None,
                }
                if len(ex_absent) < examples:
                    ex_absent.append(entry)
            overflow.append({"child_id": cid, "parent_id": pid, "classification": klass})

        flagged = rendering_variant + other_attempt + genuinely_absent
        parented_rows = len(with_parent)
        coverage = (len(checkable) / parented_rows) if parented_rows else 1.0
        result["q2_parent_in_prompt"] = {
            "rule": "parent.source_code must appear verbatim in child.prompt_context",
            "checkable_rows": len(checkable),
            "rows_with_a_parent": parented_rows,
            "coverage_ratio": coverage,
            "check_runnable": len(checkable) > 0,
            "low_coverage": (parented_rows >= LOW_COVERAGE_MIN_ROWS
                             and coverage < LOW_COVERAGE_RATIO),
            "verbatim_match": verbatim,
            "flagged_parent_not_in_prompt_literal": flagged,
            "rendering_variant_line_numbered": rendering_variant,
            "other_attempt_in_prompt": other_attempt,
            "genuinely_absent": genuinely_absent,
            "parent_row_missing": parent_row_missing_for_check,
            "excluded_from_training_pairs": genuinely_absent,
            "trivial_substring_collisions": trivial_collisions,
            "trivial_collision_ids": sorted(trivial_example_ids)[:10],
            "min_candidate_len": min_candidate_len,
            "flagged_by_strategy": dict(flagged_by_strategy.most_common()),
            "checked_by_strategy": dict(checked_by_strategy.most_common()),
            "flagged_by_run_id": dict(flagged_by_run.most_common(15)),
            "examples_parent_not_in_prompt": ex_absent,
            "examples_other_attempt_in_prompt": ex_other,
            "examples_rendering_variant": ex_render,
            "all_flagged": overflow,
        }

        # ------------------------------- Q4: exact model inputs
        def input_table(key: str, limit: int | None) -> list[dict]:
            rows: list[dict] = []
            for aid, a in attempts.items():
                keyv = (a[key] if key != "run_id" else (a["run_id"] or "<NULL>"))
                rows.append((keyv or "", a))
            agg: dict[str, Counter] = {}
            for keyv, a in rows:
                c = agg.setdefault(keyv, Counter())
                c["attempts"] += 1
                if a["prompt_context"]:
                    c["prompt_context"] += 1
                if a["source_code"]:
                    c["source_code"] += 1
                if a["raw_response"]:
                    c["raw_response"] += 1
                if a["model"]:
                    c["model"] += 1
                if not _is_blank(a["sampling"]):
                    c["sampling"] += 1
                if a["token_cost"] > 0:
                    c["token_cost_positive"] += 1
            out = []
            for keyv, c in sorted(agg.items(), key=lambda kv: -kv[1]["attempts"]):
                out.append({
                    key: keyv,
                    "attempts": c["attempts"],
                    "has_prompt_context": c["prompt_context"],
                    "has_raw_response": c["raw_response"],
                    "has_source_code": c["source_code"],
                    "has_model": c["model"],
                    "has_sampling": c["sampling"],
                    "token_cost_positive": c["token_cost_positive"],
                })
            return out[:limit] if limit else out

        strategies = input_table("strategy", None)
        runs = input_table("run_id", top_runs)
        absent_but_modeled = [
            s for s in strategies if s["has_prompt_context"] == 0 and s["has_model"] > 0
        ]
        result["q4_model_inputs"] = {
            "rows_with_prompt_context": sum(1 for a in attempts.values() if a["prompt_context"]),
            "rows_with_raw_response": sum(1 for a in attempts.values() if a["raw_response"]),
            "rows_with_source_code": sum(1 for a in attempts.values() if a["source_code"]),
            "rows_with_model": sum(1 for a in attempts.values() if a["model"]),
            "rows_with_sampling": sum(1 for a in attempts.values() if not _is_blank(a["sampling"])),
            "by_strategy": strategies,
            "by_run_id_top": runs,
            "strategies_with_prompts_systematically_absent": {
                "definition": "strategy has model set on at least one row but prompt_context on none",
                "count": len(absent_but_modeled),
                "rows_total": sum(s["attempts"] for s in absent_but_modeled),
                "rows_modeled": sum(s["has_model"] for s in absent_but_modeled),
                "strategies": [s["strategy"] for s in absent_but_modeled],
            },
        }

        # ------------------------------- Q5: compiler identity
        identities: Counter = Counter()
        identity_key: Counter = Counter()
        no_identity: list[int] = []
        for aid, a in attempts.items():
            ident, key = compiler_identity(sampling_objs[aid])
            if ident is None:
                no_identity.append(aid)
            else:
                identities[ident] += 1
                identity_key[key] += 1
        result["q5_compiler_identity"] = {
            "rows_total": len(attempts),
            "rows_with_identity": len(attempts) - len(no_identity),
            "rows_without_identity": len(no_identity),
            "rows_without_identity_examples": sorted(no_identity)[:examples],
            "identity_key_used": dict(identity_key.most_common()),
            "distinct_identities": len(identities),
            "identities_top": dict(identities.most_common(25)),
            "unparseable_sampling_json": invalid_sampling,
        }

        # ------------------------------- Q6: candidate hashes
        hash_null = hash_match = hash_mismatch = 0
        hash_mismatch_examples: list[dict] = []
        for aid, a in attempts.items():
            stored = a["source_sha256"]
            if _is_blank(stored):
                hash_null += 1
                continue
            digest = hashlib.sha256(a["source_code"].encode("utf-8")).hexdigest()
            if digest == stored:
                hash_match += 1
            else:
                hash_mismatch += 1
                if len(hash_mismatch_examples) < examples:
                    hash_mismatch_examples.append(
                        {"id": aid, "stored": stored, "computed": digest})
        result["q6_candidate_hashes"] = {
            "rows_total": len(attempts),
            "null_or_empty": hash_null,
            "match": hash_match,
            "mismatch": hash_mismatch,
            "mismatch_examples": hash_mismatch_examples,
        }

        # ------------------------------- Q7: verifier results
        exact_counter: Counter = Counter()
        compiled_counter: Counter = Counter()
        for a in attempts.values():
            exact_counter["NULL" if a["exact"] is None else str(a["exact"])] += 1
            compiled_counter["NULL" if a["compiled"] is None else str(a["compiled"])] += 1
        result["q7_verifier"] = {
            "exact_distribution": dict(exact_counter.most_common()),
            "exact_null": exact_counter.get("NULL", 0),
            "exact_true": exact_counter.get("1", 0),
            "exact_false": exact_counter.get("0", 0),
            "compiled_distribution": dict(compiled_counter.most_common()),
            "compiled_true": compiled_counter.get("1", 0),
            "compiled_false": compiled_counter.get("0", 0),
        }

        # ------------------------------- Q8: failure logging
        c0 = [a for a in attempts.values() if a["compiled"] == 0]
        c0_stderr = [a for a in c0 if a["compiler_stderr"]]
        c1_stderr = [a for a in attempts.values() if a["compiled"] == 1 and a["compiler_stderr"]]
        es_counter: Counter = Counter()
        dr_counter: Counter = Counter()
        es_set = dr_set = 0
        consumed_no_response: list[int] = []
        for a in attempts.values():
            es = "NULL" if a["extract_status"] is None else (
                "<empty>" if a["extract_status"] == "" else a["extract_status"])
            dr = "NULL" if a["done_reason"] is None else (
                "<empty>" if a["done_reason"] == "" else a["done_reason"])
            es_counter[es] += 1
            dr_counter[dr] += 1
            if not _is_blank(a["extract_status"]):
                es_set += 1
            if not _is_blank(a["done_reason"]):
                dr_set += 1
            if not a["raw_response"] and (a["token_cost"] > 0 or a["model"]):
                consumed_no_response.append(a["id"])
        raw_empty = sum(1 for a in attempts.values() if not a["raw_response"])
        raw_empty_model = sum(
            1 for a in attempts.values() if not a["raw_response"] and a["model"])
        result["q8_failure_logging"] = {
            "compiled_false": len(c0),
            "compiled_false_with_stderr": len(c0_stderr),
            "compiled_false_without_stderr": len(c0) - len(c0_stderr),
            "compiled_true_with_stderr": len(c1_stderr),
            "extract_status_set": es_set,
            "extract_status_distribution": dict(es_counter.most_common(15)),
            "done_reason_set": dr_set,
            "done_reason_distribution": dict(dr_counter.most_common(15)),
            "raw_response_empty": raw_empty,
            "raw_response_empty_with_model": raw_empty_model,
            "consumed_call_without_response": len(consumed_no_response),
            "consumed_call_without_response_examples": sorted(consumed_no_response)[:examples],
        }

        # ------------------------------- training-usable accounting
        # Row-level cascade: a row can be a model-input example only if both halves
        # of the pair survive. Reasons are non-overlapping and applied in order.
        no_prompt = no_response = excluded_absent = usable_rows = 0
        usable_by_strategy: Counter = Counter()
        excluded_by_strategy: Counter = Counter()
        for aid, a in attempts.items():
            if not a["prompt_context"]:
                no_prompt += 1
                excluded_by_strategy[a["strategy"] or "<none>"] += 1
                continue
            if not a["raw_response"]:
                no_response += 1
                excluded_by_strategy[a["strategy"] or "<none>"] += 1
                continue
            flagged_absent = False
            if a["parent_attempt_id"] is not None:
                parent = attempts.get(a["parent_attempt_id"])
                if parent is not None and parent["source_code"] and \
                        parent["source_code"] not in a["prompt_context"]:
                    # Literal failure. Only a genuine absence excludes the row, so
                    # retry against a gutter-stripped rendering and then against any
                    # other substantial candidate of the same function.
                    if not gutter_rendering_match(a["prompt_context"], parent["source_code"]):
                        recovered = any(
                            attempts[o]["source_code"]
                            and len(attempts[o]["source_code"]) >= min_candidate_len
                            and attempts[o]["source_code"] in a["prompt_context"]
                            for o in by_func.get(a["func_addr"], []) if o != aid
                        )
                        if not recovered:
                            flagged_absent = True
            if flagged_absent:
                excluded_absent += 1
                excluded_by_strategy[a["strategy"] or "<none>"] += 1
                continue
            usable_rows += 1
            usable_by_strategy[a["strategy"] or "<none>"] += 1

        # Pair-level cascade over edges.
        pair_states: Counter = Counter()
        pair_by_strategy: Counter = Counter()
        for r in edge_rows:
            pid, cid = r["pid"], r["cid"]
            p = attempts.get(pid)
            c = attempts.get(cid)
            if p is None:
                pair_states["parent-missing"] += 1
                continue
            if c is None:
                pair_states["child-missing"] += 1
                continue
            pair_by_strategy[c["strategy"] or "<none>"] += 1
            if p["compiled"] != 1 or c["compiled"] != 1:
                pair_states["endpoints-not-both-compiled"] += 1
                continue
            if p["score"] is None or c["score"] is None:
                pair_states["score-missing"] += 1
                continue
            if float(c["score"]) >= float(p["score"]):
                pair_states["child-did-not-improve"] += 1
                continue
            if not c["prompt_context"]:
                pair_states["parent-verification-impossible-no-prompt"] += 1
                continue
            if not p["source_code"] or p["source_code"] in c["prompt_context"]:
                pair_states["usable"] += 1
                continue
            if gutter_rendering_match(c["prompt_context"], p["source_code"]):
                pair_states["usable"] += 1
                continue
            recovered = any(
                attempts[o]["source_code"]
                and len(attempts[o]["source_code"]) >= min_candidate_len
                and attempts[o]["source_code"] in c["prompt_context"]
                for o in by_func.get(c["func_addr"], []) if o != cid
            )
            pair_states["recoverable-other-attempt" if recovered
                        else "parent-not-in-prompt"] += 1

        result["training_usable"] = {
            "row_level": {
                "definition": "a row is usable as an (prompt, completion) example when it has "
                              "both halves and its recorded parent is verifiably in the prompt",
                "rows_total": len(attempts),
                "usable": usable_rows,
                "excluded_no_prompt_context": no_prompt,
                "excluded_no_raw_response": no_response,
                "excluded_parent_not_in_prompt": excluded_absent,
                "excluded_total": no_prompt + no_response + excluded_absent,
                "usable_by_strategy_top": dict(usable_by_strategy.most_common(15)),
                "excluded_by_strategy_top": dict(excluded_by_strategy.most_common(15)),
            },
            "pair_level": {
                "definition": "an edge is a usable repair pair when both endpoints exist and "
                              "compile, the child strictly improves the score, and the parent "
                              "is verifiably the candidate rendered into the child's prompt",
                "edges_total": len(edge_rows),
                "states": dict(pair_states.most_common()),
                "usable": pair_states.get("usable", 0),
                "excluded_total": len(edge_rows) - pair_states.get("usable", 0),
                "edges_by_child_strategy_top": dict(pair_by_strategy.most_common(15)),
            },
        }

        # ------------------------------- findings
        findings: list[dict] = []
        if not result["q2_parent_in_prompt"]["check_runnable"]:
            findings.append({
                "id": "check-cannot-run",
                "severity": "critical",
                "count": len(attempts) - len(checkable),
                "summary": (
                    f"the parent-in-prompt check examined 0 of {len(attempts)} rows: no row has "
                    "both a parent_attempt_id and a non-empty prompt_context. A clean result here "
                    "is NOT evidence of lineage integrity."),
            })
        elif result["q2_parent_in_prompt"]["low_coverage"]:
            findings.append({
                "id": "low-check-coverage",
                "severity": "warning",
                "count": len(with_parent) - len(checkable),
                "summary": (
                    f"the parent-in-prompt check could only run on {len(checkable)} of "
                    f"{len(with_parent)} parented rows ({coverage:.1%}); the other "
                    f"{len(with_parent) - len(checkable)} have no prompt_context, so lineage "
                    "integrity is unverified for them, not verified-good."),
            })
        if genuinely_absent:
            findings.append({
                "id": "parent-not-in-prompt",
                "severity": "critical",
                "count": genuinely_absent,
                "summary": (
                    f"{genuinely_absent} attempts record a parent whose source_code is absent from "
                    "the child's prompt and is not recovered by any other attempt of the same "
                    "function; these rows are excluded from training pairs."),
                "example_ids": [e["child_id"] for e in ex_absent],
            })
        if rendering_variant:
            findings.append({
                "id": "parent-in-prompt-line-numbered",
                "severity": "info",
                "count": rendering_variant,
                "summary": (
                    f"{rendering_variant} rows fail the literal verbatim check only because the "
                    "prompt renders the parent with a `NN | ` line-number gutter. The parent IS "
                    "the candidate shown, so these are NOT excluded."),
                "example_ids": [e["child_id"] for e in ex_render],
            })
        if trivial_collisions:
            findings.append({
                "id": "trivial-substring-collision",
                "severity": "warning",
                "count": trivial_collisions,
                "summary": (
                    f"{trivial_collisions} prompt/attempt substring collisions were ignored as "
                    f"non-candidates (source shorter than {min_candidate_len} characters, e.g. a bare "
                    "`#include \"common.h\"`). Without this guard the recovery search reports false "
                    "matches."),
                "example_ids": sorted(trivial_example_ids)[:10],
            })
        if other_attempt:
            findings.append({
                "id": "recoverable-other-attempt-in-prompt",
                "severity": "warning",
                "count": other_attempt,
                "summary": (
                    f"{other_attempt} rows show a different attempt of the same function in the "
                    "prompt, so the edge is mislabelled rather than unrecoverable."),
            })
        if parent_missing or child_missing:
            findings.append({
                "id": "dangling-edge",
                "severity": "critical",
                "count": len(parent_missing) + len(child_missing),
                "summary": f"{len(parent_missing)} edges name a missing parent and "
                           f"{len(child_missing)} a missing child row.",
            })
        if hash_mismatch:
            findings.append({
                "id": "source-hash-mismatch",
                "severity": "critical",
                "count": hash_mismatch,
                "summary": f"{hash_mismatch} rows have source_sha256 != sha256(source_code).",
            })
        if hash_null:
            findings.append({
                "id": "source-hash-missing",
                "severity": "warning",
                "count": hash_null,
                "summary": f"{hash_null} rows have no source_sha256, so candidate identity is "
                           "not verifiable for them.",
            })
        if len(c0) != len(c0_stderr):
            findings.append({
                "id": "failure-without-stderr",
                "severity": "critical",
                "count": len(c0) - len(c0_stderr),
                "summary": f"{len(c0) - len(c0_stderr)} rows failed to compile with no stderr "
                           "recorded: the failure reason is lost.",
            })
        if not role_counts:
            findings.append({
                "id": "no-generation-role",
                "severity": "warning",
                "count": len(attempts),
                "summary": (
                    "no row carries sampling.generation.sampling.role, so whether independent "
                    "draws were labelled as roots cannot be checked from the data."),
            })
        else:
            rv = result["q3_roots_vs_edges"]["role_violations"]
            bad = sum(v["count"] for v in rv.values())
            findings.append({
                "id": "role-lineage-consistency",
                "severity": "info" if bad == 0 else "critical",
                "count": bad,
                "summary": (
                    "repair-role attempts all have parents and independent-role attempts have "
                    "none" if bad == 0 else
                    f"{bad} role/parent disagreements between sampling role and lineage."),
            })
        if not result["q5_compiler_identity"]["rows_with_identity"]:
            findings.append({
                "id": "no-compiler-identity",
                "severity": "critical",
                "count": len(attempts),
                "summary": "no row records a compiler identity; compilation provenance is lost.",
            })
        else:
            miss = result["q5_compiler_identity"]["rows_without_identity"]
            if miss:
                findings.append({
                    "id": "compiler-identity-missing",
                    "severity": "warning",
                    "count": miss,
                    "summary": f"{miss} rows record no compiler identity "
                               "(no sampling.compiler_recipe/recipe/compiler key).",
                })
        if not result["q7_verifier"]["exact_true"]:
            findings.append({
                "id": "no-exact-rows",
                "severity": "info",
                "count": result["q7_verifier"]["exact_true"],
                "summary": "no byte-exact rows in this database.",
            })
        if result["q7_verifier"]["exact_null"]:
            findings.append({
                "id": "exact-null",
                "severity": "warning",
                "count": result["q7_verifier"]["exact_null"],
                "summary": f"{result['q7_verifier']['exact_null']} rows have exact IS NULL: the "
                           "verifier result was never recorded.",
            })
        if consumed_no_response:
            findings.append({
                "id": "consumed-call-without-response",
                "severity": "warning",
                "count": len(consumed_no_response),
                "summary": (
                    f"{len(consumed_no_response)} rows consumed a model call (token_cost>0 or model "
                    "set) but stored no raw_response, so the generation receipt is missing."),
                "example_ids": sorted(consumed_no_response)[:examples],
            })

        result["findings"] = findings
        if invalid_sampling:
            result["warnings"].append(
                f"{invalid_sampling} attempts.sampling values are missing or not valid JSON objects")
        if not result["q4_model_inputs"]["rows_with_prompt_context"]:
            result["warnings"].append(
                "no row has a prompt_context; questions 2 and 4 cannot run on this database")
    finally:
        con.close()
    return result


# ------------------------------------------------------------------------ CLI


def _fmt_row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))


def summarize(label: str, a: dict) -> str:
    """Human-readable digest of one audit."""
    t = a["totals"]
    q2 = a["q2_parent_in_prompt"]
    q7 = a["q7_verifier"]
    ru = a["training_usable"]
    lines = [
        "=" * 78,
        f"### {label}  ({a['db']['path']})",
        f"  attempts={t['attempts']}  edges={t['edges']}  "
        f"size={a['db']['size_bytes'] / 1e6:.1f} MB",
        "",
        "  Q1 lineage",
        f"    edges={a['q1_lineage']['edges']} "
        f"parent_missing={a['q1_lineage']['parent_row_missing']} "
        f"child_missing={a['q1_lineage']['child_row_missing']} "
        f"both_compiled={a['q1_lineage']['both_endpoints_compiled']}",
        f"    score delta improved={a['q1_lineage']['score_delta']['improved']} "
        f"unchanged={a['q1_lineage']['score_delta']['unchanged']} "
        f"worse={a['q1_lineage']['score_delta']['worse']} "
        f"not_comparable={a['q1_lineage']['score_delta']['not_comparable']}",
        f"    non-canonical relations={a['q1_lineage']['non_canonical_relations']}",
        f"    repair edges with no child prompt="
        f"{a['q1_lineage']['repair_edges_whose_child_has_no_prompt']}",
        "",
        "  Q2 parent-in-prompt (CRITICAL)",
        f"    checkable={q2['checkable_rows']}/{q2['rows_with_a_parent']} parented rows "
        f"(coverage {q2['coverage_ratio']:.1%}) runnable={q2['check_runnable']} "
        f"verbatim={q2['verbatim_match']}",
        f"    flagged(literal)={q2['flagged_parent_not_in_prompt_literal']}  "
        f"-> line-numbered rendering={q2['rendering_variant_line_numbered']}  "
        f"other-attempt={q2['other_attempt_in_prompt']}  "
        f"GENUINELY ABSENT={q2['genuinely_absent']}",
        f"    trivial collisions ignored={q2['trivial_substring_collisions']} "
        f"(min_candidate_len={q2['min_candidate_len']})",
        f"    flagged by strategy={q2['flagged_by_strategy']}",
        "",
        "  Q3 roots vs edges",
        f"    with_parent={a['q3_roots_vs_edges']['rows_with_parent_field']} "
        f"without_parent={a['q3_roots_vs_edges']['rows_without_parent_field']} "
        f"independent_roots={a['q3_roots_vs_edges']['independent_roots_no_parent_no_edge_child']}",
        f"    roles={a['q3_roots_vs_edges']['role_counts']}",
        f"    role violations={ {k: v['count'] for k, v in a['q3_roots_vs_edges']['role_violations'].items()} }",
        "",
        "  Q4 model inputs",
        f"    prompt={a['q4_model_inputs']['rows_with_prompt_context']} "
        f"raw_response={a['q4_model_inputs']['rows_with_raw_response']} "
        f"model={a['q4_model_inputs']['rows_with_model']} "
        f"sampling={a['q4_model_inputs']['rows_with_sampling']}",
        f"    strategies with model set but prompts always absent: "
        f"{a['q4_model_inputs']['strategies_with_prompts_systematically_absent']['count']} "
        f"({a['q4_model_inputs']['strategies_with_prompts_systematically_absent']['rows_modeled']} rows)",
        "",
        "  Q5 compiler identity",
        f"    with={a['q5_compiler_identity']['rows_with_identity']} "
        f"without={a['q5_compiler_identity']['rows_without_identity']} "
        f"distinct={a['q5_compiler_identity']['distinct_identities']}",
        "",
        "  Q6 hashes",
        f"    null={a['q6_candidate_hashes']['null_or_empty']} "
        f"match={a['q6_candidate_hashes']['match']} "
        f"mismatch={a['q6_candidate_hashes']['mismatch']}",
        "",
        "  Q7 verifier",
        f"    exact={q7['exact_distribution']} compiled={q7['compiled_distribution']}",
        "",
        "  Q8 failure logging",
        f"    compiled=0 {a['q8_failure_logging']['compiled_false']} "
        f"with_stderr={a['q8_failure_logging']['compiled_false_with_stderr']} "
        f"without={a['q8_failure_logging']['compiled_false_without_stderr']}",
        f"    extract_status set={a['q8_failure_logging']['extract_status_set']} "
        f"done_reason set={a['q8_failure_logging']['done_reason_set']}",
        f"    consumed call with no response={a['q8_failure_logging']['consumed_call_without_response']}",
        "",
        "  TRAINING USABLE",
        f"    rows usable={ru['row_level']['usable']} / {ru['row_level']['rows_total']}",
        f"      excluded no prompt  = {ru['row_level']['excluded_no_prompt_context']}",
        f"      excluded no response = {ru['row_level']['excluded_no_raw_response']}",
        f"      excluded bad parent = {ru['row_level']['excluded_parent_not_in_prompt']}",
        f"    pairs usable={ru['pair_level']['usable']} / {ru['pair_level']['edges_total']}",
        f"      {ru['pair_level']['states']}",
        "",
        "  FINDINGS",
    ]
    for f in a["findings"]:
        lines.append(f"    [{f['severity']:<8}] {f['id']}: {f['summary']}")
    for w in a["warnings"]:
        lines.append(f"    [warn    ] {w}")
    return "\n".join(lines)


def write_markdown(databases: dict[str, dict], path: str) -> None:
    """Emit the plain-language AUDIT.md from real audit counts."""
    out: list[str] = []
    out.append("# Trajectory integrity audit")
    out.append("")
    out.append(f"Generated by `eval/trajectory_audit.py` at {time.strftime('%Y-%m-%d %H:%M:%S')}.")
    out.append("Every number below is read from the database at audit time; none are estimates.")
    out.append("")
    out.append("Databases audited:")
    out.append("")
    for label, a in databases.items():
        out.append(f"- **{label}** - `{a['db']['path']}` "
                   f"({a['totals']['attempts']:,} attempts, {a['totals']['edges']:,} edges)")
    out.append("")
    out.append("## How to read a zero in section 2")
    out.append("")
    out.append("A pass that returns nothing looks exactly like a pass with nothing to do. "
               "So section 2 reports how many rows it could actually examine, and a zero "
               "there means *the check ran and the parent was present in every row it could "
               "see* - not that the corpus is clean. Rows with a parent but no `prompt_context` "
               "were never examined and carry no verdict at all.")
    out.append("")
    out.append("Three controls distinguish a real zero from a dead code path:")
    out.append("")
    out.append("1. `checkable_rows` is non-zero and `verbatim_match` is non-zero, so the "
               "comparison fires positively as well as negatively.")
    out.append("2. The literal-failure count is broken down into line-numbered renderings "
               "(the parent present, annotated), other-attempt recoveries, and genuine "
               "absences - the three are never collapsed into one number.")
    out.append("3. A database whose lineage cannot be checked at all reports "
               "`check-cannot-run` at critical severity rather than reporting a clean pass.")
    out.append("")
    out.append("## Cross-database summary")
    out.append("")
    out.append("| database | attempts | edges | checkable lineage | genuine parent-not-in-prompt "
               "| rows usable | pairs usable |")
    out.append("|---|---|---|---|---|---|---|")
    for label, a in databases.items():
        q2s, rus = a["q2_parent_in_prompt"], a["training_usable"]
        lineage = (f"{q2s['checkable_rows']:,}/{q2s['rows_with_a_parent']:,} "
                   f"({q2s['coverage_ratio']:.0%})" if q2s["rows_with_a_parent"]
                   else "n/a (no parented rows)")
        out.append(f"| {label} | {a['totals']['attempts']:,} | {a['totals']['edges']:,} | "
                   f"{lineage} | {q2s['genuinely_absent']:,} | "
                   f"{rus['row_level']['usable']:,} | {rus['pair_level']['usable']:,} |")
    out.append("")
    for label, a in databases.items():
        t, q1, q2, q3 = a["totals"], a["q1_lineage"], a["q2_parent_in_prompt"], a["q3_roots_vs_edges"]
        q4, q5 = a["q4_model_inputs"], a["q5_compiler_identity"]
        q6, q7, q8 = a["q6_candidate_hashes"], a["q7_verifier"], a["q8_failure_logging"]
        ru = a["training_usable"]
        out.append(f"## {label}")
        out.append("")
        out.append(f"`{a['db']['path']}` - {t['attempts']:,} attempts, {t['edges']:,} edges.")
        out.append("")
        out.append("### Training-usable pairs and exclusions")
        out.append("")
        rl, pl = ru["row_level"], ru["pair_level"]
        out.append(f"- **{rl['usable']:,} of {rl['rows_total']:,} rows are usable** as "
                   "(prompt, completion) examples. Excluded: "
                   f"{rl['excluded_no_prompt_context']:,} with no `prompt_context`, "
                   f"{rl['excluded_no_raw_response']:,} with no `raw_response`, "
                   f"{rl['excluded_parent_not_in_prompt']:,} whose recorded parent is not the "
                   "candidate in the prompt.")
        out.append(f"- **{pl['usable']:,} of {pl['edges_total']:,} edges are usable** as verified "
                   "repair pairs (both endpoints exist and compile, child strictly improves the "
                   "score, parent verifiably present in the prompt).")
        for state, count in pl["states"].items():
            if state != "usable":
                out.append(f"  - excluded `{state}`: {count:,}")
        out.append("")
        top_excl = list(rl["excluded_by_strategy_top"].items())[:5]
        if top_excl:
            out.append("Exclusions concentrate in: " + ", ".join(
                f"`{s}` ({c:,})" for s, c in top_excl) + ".")
            out.append("")
        out.append("### 1. Parent/child lineage integrity")
        out.append("")
        out.append(f"- {q1['edges']:,} edge rows; parent row missing: {q1['parent_row_missing']}, "
                   f"child row missing: {q1['child_row_missing']}, self-loops: {q1['self_loops']}.")
        out.append(f"- Both endpoints compiled: {q1['both_endpoints_compiled']:,}; "
                   f"not both compiled: {q1['endpoints_not_both_compiled']:,}.")
        sd = q1["score_delta"]
        out.append(f"- Score delta (child - parent; lower is better): improved {sd['improved']:,}, "
                   f"unchanged {sd['unchanged']:,}, worse {sd['worse']:,}, "
                   f"not comparable {sd['not_comparable']:,}.")
        canonical_edges = q1["edges"] - q1["non_canonical_relations_total"]
        out.append(f"- Declared relation vocabulary is "
                   f"`{' | '.join(sorted(CANONICAL_RELATIONS))}`; {canonical_edges:,} edges use one "
                   f"of those and **{q1['non_canonical_relations_total']:,} do not** "
                   f"({len(q1['non_canonical_relations'])} distinct undeclared names, e.g. "
                   f"{', '.join(list(q1['non_canonical_relations'])[:5])}). `relation` therefore "
                   "cannot be machine-validated against the schema for most of this database.")
        out.append(f"- Edges with empty `action`: {q1['edges_with_empty_action']:,}. "
                   f"Repair-relation edges whose child has no prompt (so the request cannot be "
                   f"confirmed): {q1['repair_edges_whose_child_has_no_prompt']:,}.")
        out.append(f"- Role/relation disagreements: {q1['role_relation_inconsistent']['count']:,}.")
        out.append("")
        out.append("### 2. The critical check - recorded parent vs the candidate in the prompt")
        out.append("")
        coverage_text = (f"{q2['coverage_ratio']:.1%} coverage"
                         if q2["rows_with_a_parent"] else "n/a - no row has a parent")
        out.append(f"- Checkable rows (parent set AND non-empty prompt): **{q2['checkable_rows']:,}** "
                   f"of {q2['rows_with_a_parent']:,} rows that have a parent "
                   f"({coverage_text}); check ran: {q2['check_runnable']}.")
        out.append(f"- Verbatim parent present: {q2['verbatim_match']:,}.")
        out.append(f"- Flagged `parent-not-in-prompt` by the literal rule: "
                   f"**{q2['flagged_parent_not_in_prompt_literal']:,}**, of which:")
        out.append(f"  - {q2['rendering_variant_line_numbered']:,} are the parent rendered with a `NN | ` "
                   "line-number gutter - the parent IS the candidate shown, so these are correct "
                   "edges, not defects.")
        out.append(f"  - {q2['other_attempt_in_prompt']:,} show a different attempt of the same "
                   "function instead (mislabelled and recoverable).")
        out.append(f"  - **{q2['genuinely_absent']:,} carry no candidate at all** and are the true "
                   "defects excluded from training.")
        out.append(f"- {q2['trivial_substring_collisions']:,} substring collisions were ignored as "
                   f"non-candidates (source shorter than {q2['min_candidate_len']} chars, e.g. a bare "
                   "`#include \"common.h\"`).")
        out.append(f"- Flagged rows by strategy: {q2['flagged_by_strategy']}")
        unexamined = q2["rows_with_a_parent"] - q2["checkable_rows"]
        if unexamined > 0 and q2["checkable_rows"] > 0:
            out.append(f"- **{unexamined:,} of {q2['rows_with_a_parent']:,} parented rows were "
                       f"never examined** (no `prompt_context`), so this section says nothing "
                       "about them. The largest real integrity gap in this database is that "
                       "gap, not a mislabelled parent.")
        if q2["checkable_rows"] and not q2["genuinely_absent"]:
            out.append(f"- Zero genuine absences is a real result here, not a silent no-op: the "
                       f"check examined {q2['checkable_rows']:,} rows, matched "
                       f"{q2['verbatim_match']:,} verbatim, and resolved the remaining "
                       f"{q2['flagged_parent_not_in_prompt_literal']:,} to the breakdown above. "
                       "The auditor's own test suite contains a fixture with a genuinely "
                       "mismatched parent that must be flagged, so the failure path is exercised.")
        if q2["examples_other_attempt_in_prompt"]:
            out.append("")
            out.append("Recoverable rows (a real stored attempt of the same function is in the "
                       "prompt instead of the recorded parent):")
            out.append("")
            out.append("| child id | recorded parent | attempt actually shown | function | strategy |")
            out.append("|---|---|---|---|---|")
            for e in q2["examples_other_attempt_in_prompt"]:
                out.append(f"| {e['child_id']} | {e['parent_id']} | {e['other_attempt_id']} | "
                           f"{e['func_name']} | `{e['strategy']}` |")
        if q2["examples_parent_not_in_prompt"]:
            out.append("")
            out.append("Genuine failures:")
            out.append("")
            out.append("| child id | parent id | function | strategy | prompt len | other attempt found |")
            out.append("|---|---|---|---|---|---|")
            for e in q2["examples_parent_not_in_prompt"]:
                out.append(f"| {e['child_id']} | {e['parent_id']} | {e['func_name']} | "
                           f"`{e['strategy']}` | {e['prompt_len']:,} | "
                           f"{e['other_attempt_id'] or 'none'} |")
        out.append("")
        out.append("### 3. Independent roots vs refinement edges")
        out.append("")
        out.append(f"- Rows with a parent: {q3['rows_with_parent_field']:,}; without: "
                   f"{q3['rows_without_parent_field']:,}.")
        out.append(f"- Independent roots (no parent and not an edge child): "
                   f"{q3['independent_roots_no_parent_no_edge_child']:,}. Rows in no edge at all: "
                   f"{q3['rows_in_no_edge_at_all']:,}.")
        out.append(f"- Roles: {q3['role_counts'] or 'none recorded'}")
        out.append(f"- Role violations: "
                   f"{ {k: v['count'] for k, v in q3['role_violations'].items()} }")
        out.append("")
        out.append("### 4. Exact model inputs")
        out.append("")
        out.append(f"- Rows with `prompt_context`: {q4['rows_with_prompt_context']:,}; "
                   f"`raw_response`: {q4['rows_with_raw_response']:,}; "
                   f"`source_code`: {q4['rows_with_source_code']:,}; `model`: "
                   f"{q4['rows_with_model']:,}; `sampling`: {q4['rows_with_sampling']:,}.")
        sa = q4["strategies_with_prompts_systematically_absent"]
        out.append(f"- Strategies where a model was used but prompts are always absent: "
                   f"**{sa['count']}** ({sa['rows_modeled']:,} modelled rows of "
                   f"{sa['rows_total']:,}).")
        if sa["strategies"]:
            out.append("  - " + ", ".join(f"`{s}`" for s in sa["strategies"][:12]) +
                       (" ..." if len(sa["strategies"]) > 12 else ""))
        out.append("")
        out.append("### 5. Compiler identity")
        out.append("")
        out.append(f"- Rows with a compiler identity: {q5['rows_with_identity']:,}; without: "
                   f"**{q5['rows_without_identity']:,}**.")
        out.append(f"- Key used: {q5['identity_key_used']}; distinct identities: "
                   f"{q5['distinct_identities']}.")
        if q5["unparseable_sampling_json"]:
            out.append(f"- Unparseable `sampling` JSON: {q5['unparseable_sampling_json']:,}.")
        out.append("")
        out.append("### 6. Candidate hashes")
        out.append("")
        out.append(f"- `source_sha256` NULL/empty: {q6['null_or_empty']:,}; "
                   f"matches `sha256(source_code)`: {q6['match']:,}; mismatches: "
                   f"**{q6['mismatch']:,}**.")
        out.append("")
        out.append("### 7. Verifier results")
        out.append("")
        out.append(f"- `exact`: {q7['exact_distribution']} (NULL {q7['exact_null']:,}, "
                   f"true {q7['exact_true']:,}).")
        out.append(f"- `compiled`: {q7['compiled_distribution']}.")
        out.append("")
        out.append("### 8. Failure logging")
        out.append("")
        out.append(f"- `compiled=0`: {q8['compiled_false']:,}, of which with stderr "
                   f"{q8['compiled_false_with_stderr']:,} and **without stderr "
                   f"{q8['compiled_false_without_stderr']:,}**.")
        out.append(f"- `compiled=1` with non-empty stderr: {q8['compiled_true_with_stderr']:,}.")
        out.append(f"- `extract_status` set on {q8['extract_status_set']:,} rows "
                   f"{q8['extract_status_distribution']}.")
        out.append(f"- `done_reason` set on {q8['done_reason_set']:,} rows "
                   f"{q8['done_reason_distribution']}.")
        out.append(f"- Empty `raw_response` (no stored completion): {q8['raw_response_empty']:,}; "
                   f"of those, consumed a call: **{q8['consumed_call_without_response']:,}**.")
        out.append("")
        out.append("### Findings")
        out.append("")
        for f in a["findings"]:
            out.append(f"- **[{f['severity']}] {f['id']}** ({f['count']:,}): {f['summary']}")
        for w in a["warnings"]:
            out.append(f"- [warning] {w}")
        out.append("")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Audit trajectory-record integrity for training-pair usability. Read-only.")
    ap.add_argument("--kb", help="research KB (kb-sbk1.sqlite); opened read-only")
    ap.add_argument("--scratch", nargs="+", default=[],
                    help="one or more fresh collection DBs; opened read-only")
    ap.add_argument("--json-out", help="write the full audit as JSON here")
    ap.add_argument("--md-out", help="write a plain-language summary as Markdown here")
    ap.add_argument("--examples", type=int, default=10,
                    help="max example row ids per check (default 10)")
    ap.add_argument("--min-candidate-len", type=int, default=DEFAULT_MIN_CANDIDATE_LEN,
                    help="shortest source_code counted as a real alternative candidate")
    args = ap.parse_args(argv)

    if not args.kb and not args.scratch:
        ap.error("at least one of --kb / --scratch is required")

    targets: list[tuple[str, str]] = []
    if args.kb:
        targets.append(("kb", args.kb))
    for p in args.scratch:
        targets.append((f"scratch:{Path(p).parent.name}", p))

    databases: dict[str, dict] = {}
    for label, path in targets:
        if not os.path.exists(path):
            print(f"!! missing database, skipped: {label} -> {path}", file=sys.stderr)
            continue
        audit = audit_db(path, examples=args.examples,
                         min_candidate_len=args.min_candidate_len)
        databases[label] = audit
        print(summarize(label, audit))
        print()

    if not databases:
        print("!! nothing audited", file=sys.stderr)
        return 2

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "auditor": "eval/trajectory_audit.py",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "databases": databases,
        }
        Path(args.json_out).write_text(
            json.dumps(payload, indent=2, sort_keys=False, default=str), encoding="utf-8")
        print(f"wrote {args.json_out}")

    if args.md_out:
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        write_markdown(databases, args.md_out)
        print(f"wrote {args.md_out}")

    critical = sum(
        1 for a in databases.values() for f in a["findings"] if f["severity"] == "critical")
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
