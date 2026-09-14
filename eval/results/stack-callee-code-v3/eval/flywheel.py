"""Generate and measure a matching-decompilation flywheel.

A flywheel is not "the match count went up".  It exists only when newly
verified source becomes useful context for functions that remain unsolved.
This module makes that claim testable in three ways:

* snapshot the exact-source pool and a frozen hard-function panel;
* rank unsolved *bridge functions* whose assembly resembles several panel
  targets, weighted by the bridge's current tractability;
* compare pool snapshots and sibling-on/off result arms without treating a
  stochastic score movement as proof of a causal gain.

Similarity ranking uses assembly only.  Paths returned by the reference
project's similarity index are discarded; reference C is never read or
written to the manifest.

Generate a snapshot and active DEV set:

    python3 -m eval.flywheel snapshot \
        --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \
        --panel eval/sets/hard_v1.json \
        --triage eval/results/triage.json \
        --out eval/results/flywheel.json

Compare the pool after another batch of matches:

    python3 -m eval.flywheel compare \
        --before eval/results/flywheel-before.json \
        --after eval/results/flywheel-after.json

Measure whether sibling context helped at one snapshot:

    python3 -m eval.flywheel arms \
        --control eval/results/flywheel-...-control-r1.jsonl \
        --treatment eval/results/flywheel-...-siblings-r1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from eval import matched as matched_mod
from eval.sets import TIERS
from eval.sibling_coverage import similarity_band
from solver import shaped_flywheel, siblings


ROOT = Path(__file__).parent.parent
SCHEMA_VERSION = 1
SIMILARITY_RANKING_SCHEMA = 1
TIER_WEIGHT = {"tiny": 0.5, "small": 0.75, "medium": 1.0,
               "large": 1.5, "huge": 2.0, "unknown": 1.0}

SimilarityResult = tuple[str, float, Path]
Retriever = Callable[[Path, str, set[str]], list[SimilarityResult]]


def _sha256_json(value: object, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:length]


def _tier(insn_count: int | None, size: int | None) -> str:
    count = insn_count if insn_count is not None else (size or 0) // 4
    for name, lo, hi in TIERS:
        if lo <= count < hi:
            return name
    return "unknown"


def _function_metadata(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(functions)")}
    wanted = [name for name in ("size", "insn_count", "is_leaf", "tier")
              if name in columns]
    select = ", ".join(["name", *wanted])
    metadata: dict[str, dict[str, object]] = {}
    for row in conn.execute(f"SELECT {select} FROM functions"):
        values = dict(zip(["name", *wanted], row))
        name = str(values.pop("name"))
        tier = values.get("tier") or _tier(
            values.get("insn_count"), values.get("size"))  # type: ignore[arg-type]
        metadata[name] = {**values, "tier": tier}
    return metadata


def _load_panel(path: Path, split: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if split not in payload or not isinstance(payload[split], list):
        raise ValueError(f"{path} has no list split named {split!r}")
    entries = payload[split]
    names = [entry.get("function") for entry in entries]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError(f"{path} contains a panel entry without a function name")
    if len(names) != len(set(names)):
        raise ValueError(f"{path} contains duplicate functions in {split}")
    return payload, entries


def _load_triage(path: Path | None, conn: sqlite3.Connection,
                  done: set[str]) -> list[dict[str, object]]:
    if path is not None:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"{path} must contain a JSON list")
        return [dict(row) for row in rows
                if row.get("function") not in done]

    # A score-only fallback still produces bridge candidates, but labels their
    # tractability unknown instead of inventing structural fault counts.
    rows = conn.execute(
        "SELECT f.name, max(a.score) FROM functions f "
        "JOIN attempts a ON a.func_addr=f.addr WHERE a.compiled=1 "
        "GROUP BY f.addr ORDER BY max(a.score) DESC").fetchall()
    return [{"function": name, "score": score, "structural": None,
             "offset": None, "width": None, "reloc": None, "regalloc": None}
            for name, score in rows if name not in done]


def _repairable(row: dict[str, object]) -> int | None:
    # Register allocation has no implemented rewrite. Counting it as cheap
    # repair work made the bridge queue call a 39-register wall tractable.
    keys = ("offset", "width", "reloc")
    if any(row.get(key) is None for key in keys):
        return None
    return sum(int(row[key]) for key in keys) + int(row.get("immediate") or 0)


def _bridge_cost(row: dict[str, object]) -> float | None:
    structural = row.get("structural")
    repairable = _repairable(row)
    if structural is None or repairable is None:
        return None
    # Structural and register-allocation faults are current walls, so make each
    # four times as expensive as a fault owned by an existing repair pass.
    regalloc = row.get("regalloc")
    if regalloc is None:
        return None
    return (1.0 + 4.0 * int(structural) + 4.0 * int(regalloc)
            + float(repairable))


def _default_retriever(repo: Path, target: str,
                       allowed: set[str]) -> list[SimilarityResult]:
    if not allowed:
        return []
    return siblings.find(repo, target, top=len(allowed), min_score=0.0,
                         timeout=600, allowed=allowed)


def _git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "missing"


def _public_provenance_record(path: Path) -> dict[str, object]:
    """Summarize source-blind public-corpus receipts for a DEV snapshot."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get(
            "kind") != "n64_public_provenance_hypotheses":
        raise ValueError(f"{path} is not a supported provenance registry")
    policy = payload.get("policy")
    if not isinstance(policy, dict) or policy.get("source_bodies_stored") is not False \
            or policy.get("source_in_solver_prompt") is not False:
        raise ValueError("public provenance must forbid stored/prompt source bodies")
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list):
        raise ValueError("public provenance registry has invalid hypotheses")

    target_functions = []
    promoted = []
    repository_heads: dict[str, str] = {}
    semantic_scores = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            raise ValueError("public provenance registry has an invalid hypothesis")
        source = hypothesis.get("corpus_source")
        promotion = hypothesis.get("promotion")
        if not isinstance(source, dict) or source.get("source_body_stored") is not False:
            raise ValueError("public provenance hypothesis stores or omits source policy")
        if not isinstance(promotion, dict):
            raise ValueError("public provenance hypothesis has no promotion record")
        oracle_exact = promotion.get("target_oracle_exact") is True
        if oracle_exact and not (
                promotion.get("target_candidate_compiled") is True
                and promotion.get("target_oracle_tested") is True):
            raise ValueError("public provenance claims exact without compile/oracle gates")
        target = hypothesis.get("target_function")
        if not isinstance(target, str) or not target:
            raise ValueError("public provenance hypothesis has no target function")
        target_functions.append(target)
        if oracle_exact:
            promoted.append(target)
        repo_id = source.get("repo_id")
        head = source.get("repo_head")
        if isinstance(repo_id, str) and isinstance(head, str):
            repository_heads[repo_id] = head
        comparison = hypothesis.get("ghidra", {}).get("comparison", {}) \
            if isinstance(hypothesis.get("ghidra"), dict) else {}
        semantic = comparison.get("semantic", {}) \
            if isinstance(comparison, dict) else {}
        score = semantic.get("provenance_rank_score") \
            if isinstance(semantic, dict) else None
        if isinstance(score, (int, float)):
            semantic_scores.append(float(score))

    corpus_index = payload.get("corpus_index")
    corpus_summary: dict[str, object] = {}
    if isinstance(corpus_index, dict):
        stats = corpus_index.get("stats")
        corpus_summary = {
            "sha256": corpus_index.get("sha256"),
            "repositories": stats.get("repository_count") if isinstance(stats, dict) else None,
            "source_files": stats.get("source_files") if isinstance(stats, dict) else None,
            "functions": stats.get("functions") if isinstance(stats, dict) else None,
        }
    return {
        "registry": str(path),
        "digest": _file_digest(path),
        "role": "DEV provenance ranking only; not solver context or exactness evidence",
        "hypotheses": len(hypotheses),
        "target_functions": sorted(set(target_functions)),
        "oracle_promoted": sorted(set(promoted)),
        "semantic_rank_range": (
            {"min": min(semantic_scores), "max": max(semantic_scores)}
            if semantic_scores else None),
        "repository_heads": dict(sorted(repository_heads.items())),
        "corpus_index": corpus_summary,
        "source_bodies_in_snapshot": False,
        "source_bodies_in_prompt": False,
    }


class SimilarityCache:
    """Persistent assembly rankings keyed by repository and index implementation.

    The cache stores names and scores only.  The external index also reports a
    reference source path, but that path is neither needed nor retained.
    Pool snapshots can therefore be re-filtered in milliseconds as new exact
    functions arrive, without rerunning the seven-minute corpus index.
    """

    def __init__(self, path: Path, repo: Path):
        self.path = path
        self.repo = repo
        self.key = {
            "repo_head": _git_head(repo),
            "index_implementation": _file_digest(
                repo / "tools" / "find_similar_functions.py"),
            # Bump only when SIM_RE/CSRC_RE ranking semantics change. Hashing
            # the whole siblings module made unrelated pool-bundle edits
            # invalidate seven minutes of deterministic assembly rankings.
            "ranking_schema": SIMILARITY_RANKING_SCHEMA,
        }
        self.rows: dict[str, list[list[object]]] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                cached_key = payload.get("key", {})
                same_index = (
                    cached_key.get("repo_head") == self.key["repo_head"]
                    and cached_key.get("index_implementation") ==
                    self.key["index_implementation"])
                schema_compatible = (
                    cached_key.get("ranking_schema") == SIMILARITY_RANKING_SCHEMA
                    # One-time migration from the overly broad module hash.
                    or "parser_implementation" in cached_key)
                if same_index and schema_compatible and isinstance(
                        payload.get("rows"), dict):
                    self.rows = payload["rows"]
            except (OSError, json.JSONDecodeError):
                self.rows = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "key": self.key, "rows": self.rows,
                   "contents": "assembly similarity names and scores only"}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def __call__(self, repo: Path, target: str,
                 allowed: set[str]) -> list[SimilarityResult]:
        if target not in self.rows:
            # Rank the full corpus once. Filtering before caching would make a
            # newly matched source invisible until the expensive index reran.
            ranked = siblings.find(repo, target, top=2500, min_score=0.0,
                                    timeout=600)
            self.rows[target] = [[name, score] for name, score, _path in ranked]
            self._save()
        return [(str(name), float(score), Path())
                for name, score in self.rows[target]
                if str(name) in allowed and str(name) != target]


def _pool_record(sources: dict[str, str], metadata: dict[str, dict[str, object]],
                 disk_matches: set[str]) -> dict[str, object]:
    tiers = Counter(str(metadata.get(name, {}).get("tier", "unknown"))
                    for name in sources)
    medium_plus = sum(tiers.get(tier, 0) for tier in ("medium", "large", "huge"))
    return {
        "digest": siblings.source_digest(sources),
        "exact_sources": len(sources),
        "source_functions": sorted(sources),
        "tier_counts": dict(sorted(tiers.items())),
        "medium_plus_sources": medium_plus,
        "disk_exact_without_source": sorted(disk_matches - set(sources)),
        "source_policy": "positive exact verifier receipts only",
    }


def _coverage_and_bridges(
        repo: Path, active: list[dict[str, object]], pool_names: set[str],
        triage: list[dict[str, object]], metadata: dict[str, dict[str, object]],
        usable_threshold: float, candidate_threshold: float, retriever: Retriever,
        progress: Callable[[str], None] | None = None,
        ) -> tuple[dict[str, object], list[dict[str, object]]]:
    triage_by_name = {str(row["function"]): row for row in triage
                      if row.get("function")}
    candidate_names = set(triage_by_name) - pool_names
    allowed = pool_names | candidate_names
    bands = Counter()
    coverage_rows: list[dict[str, object]] = []
    bridge_edges: dict[str, list[dict[str, object]]] = defaultdict(list)
    errors: list[dict[str, str]] = []

    for index, entry in enumerate(active, 1):
        target = str(entry["function"])
        if progress is not None:
            progress(f"[{index}/{len(active)}] similarity graph: {target}")
        try:
            ranked = retriever(repo, target, allowed)
        except Exception as exc:  # one broken target must not erase the snapshot
            errors.append({"function": target, "error": type(exc).__name__})
            coverage_rows.append({"function": target, "sibling": None,
                                  "error": type(exc).__name__})
            bands["none"] += 1
            continue

        pool_hits = [(name, score) for name, score, _path in ranked
                     if name in pool_names and name != target]
        if pool_hits:
            sibling_name, sibling_score = max(pool_hits, key=lambda item: item[1])
            band = similarity_band(sibling_score)
            bands[band] += 1
            coverage_rows.append({"function": target, "sibling": sibling_name,
                                  "similarity": sibling_score, "band": band})
        else:
            bands["none"] += 1
            coverage_rows.append({"function": target, "sibling": None})

        target_tier = str(entry.get(
            "tier", metadata.get(target, {}).get("tier", "unknown")))
        target_weight = TIER_WEIGHT.get(target_tier, 1.0)
        for candidate, score, _path in ranked:
            if candidate == target or candidate not in candidate_names:
                continue
            if score < candidate_threshold:
                continue
            usable = score >= usable_threshold
            bridge_edges[candidate].append({
                "function": target,
                "tier": target_tier,
                "similarity": score,
                "usable": usable,
                # Weak similarities generate hypotheses, not context. Keep
                # them visible but discount them fourfold in the queue.
                "weighted_value": round(
                    (score ** 2) * target_weight * (1.0 if usable else 0.25), 6),
            })

    hubs: list[dict[str, object]] = []
    for candidate, edges in bridge_edges.items():
        triage_row = triage_by_name[candidate]
        cost = _bridge_cost(triage_row)
        unlock_value = sum(float(edge["weighted_value"]) for edge in edges)
        priority = unlock_value / cost if cost is not None else None
        hubs.append({
            "function": candidate,
            "tier": metadata.get(candidate, {}).get("tier", "unknown"),
            "best_score": triage_row.get("score"),
            "structural_faults": triage_row.get("structural"),
            "repairable_faults": _repairable(triage_row),
            "estimated_cost": cost,
            "unlock_count": len(edges),
            "usable_unlock_count": sum(bool(edge["usable"]) for edge in edges),
            "exploratory_unlock_count": sum(not bool(edge["usable"]) for edge in edges),
            "unlock_value": round(unlock_value, 6),
            "priority": round(priority, 8) if priority is not None else None,
            "unlocks": sorted(edges, key=lambda edge: (-float(edge["similarity"]),
                                                        str(edge["function"]))),
        })
    hubs.sort(key=lambda row: (
        row["priority"] is None,
        -(float(row["priority"]) if row["priority"] is not None else 0.0),
        -int(row["unlock_count"]),
        str(row["function"]),
    ))

    usable = sum(
        row.get("similarity") is not None
        and float(row["similarity"]) >= usable_threshold
        for row in coverage_rows)
    coverage = {
        "measured": True,
        "usable_threshold": usable_threshold,
        "candidate_threshold": candidate_threshold,
        "usable": usable,
        "active_functions": len(active),
        "bands": {band: bands.get(band, 0)
                  for band in ("0.90+", "0.75-0.90", "0.45-0.75", "<0.45", "none")},
        "errors": errors,
        "rows": coverage_rows,
        "ranking_evidence": "assembly similarity only; returned source paths discarded",
    }
    return coverage, hubs


def build_snapshot(
        conn: sqlite3.Connection, repo: Path, panel_path: Path,
        *, split: str = "dev", triage_path: Path | None = None,
        matched_root: Path = ROOT / "matched_recovered",
        usable_threshold: float = 0.75, candidate_threshold: float = 0.45,
        measure_similarity: bool = True, retriever: Retriever = _default_retriever,
        provenance_registry: Path | None = None,
        progress: Callable[[str], None] | None = None,
        ) -> tuple[dict[str, object], dict[str, object]]:
    """Return ``(snapshot, active_eval_set)`` without writing either."""
    if not 0.0 <= candidate_threshold <= usable_threshold <= 1.0:
        raise ValueError(
            "similarity thresholds must satisfy 0 <= candidate <= usable <= 1")
    if provenance_registry is not None and split != "dev":
        raise ValueError("public provenance is DEV-only and forbidden for heldout snapshots")
    panel_payload, entries = _load_panel(panel_path, split)
    metadata = _function_metadata(conn)
    sources = siblings.verified_sources(conn)
    disk_matches = matched_mod.matched_on_disk(str(matched_root))
    done = set(sources) | disk_matches
    active = [dict(entry) for entry in entries
              if str(entry["function"]) not in done]
    retired = [str(entry["function"]) for entry in entries
               if str(entry["function"]) in done]
    triage = _load_triage(triage_path, conn, done)

    canonical_panel = [
        {"function": str(entry["function"]),
         "tier": entry.get("tier", metadata.get(str(entry["function"]), {}).get(
             "tier", "unknown")),
         "leaf": entry.get("leaf", metadata.get(str(entry["function"]), {}).get(
             "is_leaf"))}
        for entry in entries]
    panel_digest = _sha256_json(canonical_panel)
    pool = _pool_record(sources, metadata, disk_matches)

    if measure_similarity:
        coverage, hubs = _coverage_and_bridges(
            repo, active, set(sources), triage, metadata,
            usable_threshold, candidate_threshold,
            retriever, progress)
    else:
        coverage = {
            "measured": False,
            "usable_threshold": usable_threshold,
            "candidate_threshold": candidate_threshold,
            "usable": None,
            "active_functions": len(active),
            "bands": {}, "errors": [], "rows": [],
            "ranking_evidence": None,
        }
        hubs = []

    snapshot_core = {
        "panel_digest": panel_digest,
        "pool_digest": pool["digest"],
        "active": [entry["function"] for entry in active],
        "coverage": [{"function": row["function"],
                      "sibling": row.get("sibling"),
                      "similarity": row.get("similarity")}
                     for row in coverage["rows"]],
    }
    snapshot_id = _sha256_json(snapshot_core)
    snapshot: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "decomp_flywheel_snapshot",
        "snapshot_id": snapshot_id,
        "repository": {"path": str(repo), "head": _git_head(repo)},
        "database": str(conn.execute("PRAGMA database_list").fetchone()[2]),
        "hypothesis": (
            "As the verified exact-source pool grows, assembly-similar source "
            "becomes available to a fixed hard-function panel and raises its "
            "oracle-verified exact rate."),
        "panel": {
            "source": str(panel_path), "split": split, "digest": panel_digest,
            "total": len(entries), "active": len(active),
            "retired_exact": sorted(retired),
            "tier_counts": dict(sorted(Counter(
                str(entry.get("tier", "unknown")) for entry in active).items())),
        },
        "pool": pool,
        "coverage": coverage,
        "bridge_queue": hubs,
        "bridge_policy": {
            "usable_similarity": f">={usable_threshold}",
            "exploratory_similarity": f"{candidate_threshold}..< {usable_threshold}",
            "exploratory_weight": 0.25,
        },
        "guardrails": {
            "reference_source_in_manifest": False,
            "reference_source_in_prompt": False,
            "similarity_role": "ranking only",
            "unknown_tractability": "reported, never guessed",
            "primary_outcome": "paired oracle exact verdict",
            "score_outcome": "secondary and inconclusive without replication",
            "public_provenance_in_prompt": False,
            "public_provenance_promotion": "target compile plus byte-exact oracle only",
        },
    }
    if provenance_registry is not None:
        snapshot["public_provenance"] = _public_provenance_record(
            provenance_registry)

    active_set = dict(panel_payload)
    active_set[split] = active
    active_set["flywheel_snapshot"] = snapshot_id
    active_set["note"] = (
        "DEV flywheel panel. Identity is frozen by panel_digest; functions "
        "already exact at this snapshot are retired rather than re-solved. "
        "Never use this tuning instrument for a headline held-out result.")
    return snapshot, active_set


def add_experiment_commands(
        snapshot: dict[str, object], active_set_path: Path, db: Path, repo: Path,
        results_dir: Path, pool_bundle_path: Path, *, model: str, samples: int,
        temperature: float,
        repeats: int, split: str, shaped_bundle_path: Path | None = None,
        ) -> None:
    snapshot_id = str(snapshot["snapshot_id"])
    common = [
        "python3", "-m", "eval.run_set", "--repo", str(repo),
        "--db", str(db), "--set", str(active_set_path), "--split", split,
        "--model", model, "-n", str(samples), "--temp", str(temperature),
        "--pipeline",
    ]
    commands = []
    for repeat in range(1, repeats + 1):
        control_out = results_dir / f"flywheel-{snapshot_id}-control-r{repeat}.jsonl"
        treatment_out = results_dir / f"flywheel-{snapshot_id}-siblings-r{repeat}.jsonl"
        command = {
            "repeat": repeat,
            "control_result": str(control_out),
            "treatment_result": str(treatment_out),
            "control": shlex.join([*common, "--out", str(control_out)]),
            "treatment": shlex.join(
                [*common, "--siblings", "--frozen-sibling-pool",
                 str(pool_bundle_path), "--out", str(treatment_out)]),
        }
        if shaped_bundle_path is not None:
            shaped_out = results_dir / (
                f"flywheel-{snapshot_id}-shaped-r{repeat}.jsonl")
            command.update({
                "shaped_result": str(shaped_out),
                "shaped_treatment": shlex.join(
                    [*common, "--siblings", "--shaped-sibling-pool",
                     str(shaped_bundle_path), "--out", str(shaped_out)]),
            })
        commands.append(command)
    snapshot["experiment"] = {
        "design": "paired sibling-off/on DEV runs at one exact-source snapshot",
        "frozen_sibling_pool": str(pool_bundle_path),
        "repeats": repeats,
        "commands": commands,
        "acceptance": {
            "decisive": "treatment produces more oracle-exact functions",
            "supporting": "score gain repeats and is not driven by zero-draw infrastructure failures",
            "inconclusive": "score movement without a replicated exact gain",
        },
    }


def compare_snapshots(before: dict[str, object],
                      after: dict[str, object]) -> dict[str, object]:
    if before.get("schema_version") != after.get("schema_version"):
        raise ValueError("snapshot schema versions differ")
    before_panel = before["panel"]  # type: ignore[assignment]
    after_panel = after["panel"]  # type: ignore[assignment]
    if before_panel["digest"] != after_panel["digest"]:  # type: ignore[index]
        raise ValueError("panel digests differ; that is panel drift, not a flywheel comparison")

    before_pool = before["pool"]  # type: ignore[assignment]
    after_pool = after["pool"]  # type: ignore[assignment]
    before_sources = set(before_pool["source_functions"])  # type: ignore[index]
    after_sources = set(after_pool["source_functions"])  # type: ignore[index]
    b_cov = before["coverage"]  # type: ignore[assignment]
    a_cov = after["coverage"]  # type: ignore[assignment]
    if not b_cov["measured"] or not a_cov["measured"]:  # type: ignore[index]
        coverage_delta = None
        status = "inconclusive_similarity_not_measured"
    else:
        coverage_delta = int(a_cov["usable"]) - int(b_cov["usable"])  # type: ignore[index]
        if coverage_delta > 0:
            status = "flywheel_coverage_engaged"
        elif after_sources - before_sources:
            status = "pool_grew_without_hard_panel_coverage"
        else:
            status = "no_verified_pool_growth"

    before_rows = {row["function"]: row for row in b_cov.get("rows", [])}  # type: ignore[union-attr]
    after_rows = {row["function"]: row for row in a_cov.get("rows", [])}  # type: ignore[union-attr]
    changed_targets = []
    for name in sorted(set(before_rows) & set(after_rows)):
        old = before_rows[name]
        new = after_rows[name]
        old_score = old.get("similarity")
        new_score = new.get("similarity")
        if old.get("sibling") == new.get("sibling") and old_score == new_score:
            continue
        changed_targets.append({
            "function": name,
            "before_sibling": old.get("sibling"),
            "after_sibling": new.get("sibling"),
            "before_similarity": old_score,
            "after_similarity": new_score,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "decomp_flywheel_snapshot_comparison",
        "before": before.get("snapshot_id"),
        "after": after.get("snapshot_id"),
        "panel_digest": before_panel["digest"],  # type: ignore[index]
        "status": status,
        "pool_growth": len(after_sources) - len(before_sources),
        "added_sources": sorted(after_sources - before_sources),
        "removed_sources": sorted(before_sources - after_sources),
        "usable_coverage_before": b_cov.get("usable"),  # type: ignore[union-attr]
        "usable_coverage_after": a_cov.get("usable"),  # type: ignore[union-attr]
        "usable_coverage_delta": coverage_delta,
        "changed_targets": changed_targets,
    }


def _load_jsonl(path: Path) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if "function" not in row:
            raise ValueError(f"{path}:{line_number}: missing function")
        rows[str(row["function"])] = row
    return rows


def compare_arms(control: dict[str, dict[str, object]],
                 treatment: dict[str, dict[str, object]]) -> dict[str, object]:
    common = sorted(set(control) & set(treatment))
    missing_control = sorted(set(treatment) - set(control))
    missing_treatment = sorted(set(control) - set(treatment))
    paired: list[dict[str, object]] = []
    excluded: list[dict[str, str]] = []
    for name in common:
        c = control[name]
        t = treatment[name]
        if int(c.get("draws", 0)) == 0 or int(t.get("draws", 0)) == 0:
            excluded.append({"function": name,
                             "reason": "one or both arms never reached a model call"})
            continue
        c_score = float(c.get("best_score", 0.0))
        t_score = float(t.get("best_score", 0.0))
        paired.append({
            "function": name,
            "control_exact": bool(c.get("exact")),
            "treatment_exact": bool(t.get("exact")),
            "control_score": c_score,
            "treatment_score": t_score,
            "score_delta": round(t_score - c_score, 6),
        })

    control_exact = sum(bool(row["control_exact"]) for row in paired)
    treatment_exact = sum(bool(row["treatment_exact"]) for row in paired)
    exact_delta = treatment_exact - control_exact
    if exact_delta > 0:
        status = "exact_gain_observed_needs_replication"
    elif exact_delta < 0:
        status = "exact_regression_observed_needs_replication"
    else:
        status = "inconclusive_no_exact_difference"
    mean_delta = (sum(float(row["score_delta"]) for row in paired) / len(paired)
                  if paired else None)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "decomp_flywheel_arm_comparison",
        "status": status,
        "paired_functions": len(paired),
        "excluded_infrastructure": excluded,
        "missing_control": missing_control,
        "missing_treatment": missing_treatment,
        "control_exact": control_exact,
        "treatment_exact": treatment_exact,
        "exact_delta": exact_delta,
        "mean_score_delta": round(mean_delta, 6) if mean_delta is not None else None,
        "improved_scores": sum(float(row["score_delta"]) > 0 for row in paired),
        "worse_scores": sum(float(row["score_delta"]) < 0 for row in paired),
        "unchanged_scores": sum(float(row["score_delta"]) == 0 for row in paired),
        "rows": paired,
        "interpretation": (
            "Only oracle exact verdicts are decisive. A single stochastic arm "
            "comparison cannot establish a score-only gain."),
    }


def _write_json(path: Path | None, payload: object) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path is None:
        print(rendered, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    print(f"wrote {path}")


def _snapshot_command(args: argparse.Namespace) -> int:
    if args.split != "dev" and args.outcome_pair:
        raise ValueError("retrieval outcome receipts are DEV-only")
    conn = sqlite3.connect(args.db.expanduser())
    retriever: Retriever = _default_retriever
    if not args.skip_similarity:
        cache_path = args.similarity_cache or args.out.with_suffix(
            ".similarity-cache.json")
        retriever = SimilarityCache(cache_path, args.repo.expanduser())
    try:
        snapshot, active_set = build_snapshot(
            conn, args.repo.expanduser(), args.panel,
            split=args.split, triage_path=args.triage,
            matched_root=args.matched_root,
            usable_threshold=args.usable_threshold,
            candidate_threshold=args.candidate_threshold,
            measure_similarity=not args.skip_similarity,
            retriever=retriever,
            provenance_registry=args.provenance_registry,
            progress=lambda message: print(message, flush=True),
        )
        exact_sources = siblings.verified_sources(conn)
        pool_bundle = siblings.source_bundle(exact_sources)
        outcomes = []
        for index, pair in enumerate(args.outcome_pair, 1):
            control_path, treatment_path = Path(pair[0]), Path(pair[1])
            outcomes.extend(shaped_flywheel.outcome_receipts(
                _load_jsonl(control_path), _load_jsonl(treatment_path),
                arm_pair=f"snapshot-input-{index}",
                control_ref=shaped_flywheel.artifact_ref(control_path),
                treatment_ref=shaped_flywheel.artifact_ref(treatment_path)))
        shaped_bundle = shaped_flywheel.build_library(
            conn, exact_sources, outcomes=outcomes,
            compiler=args.compiler_label)
    finally:
        conn.close()

    active_set_path = args.active_set_out or args.out.with_name(
        args.out.stem + "-active-set.json")
    pool_bundle_path = args.pool_out or args.out.with_name(
        args.out.stem + "-sibling-pool.json")
    shaped_bundle_path = args.shaped_pool_out or args.out.with_name(
        args.out.stem + "-shaped-pool.json")
    if pool_bundle["digest"] != snapshot["pool"]["digest"]:
        raise ValueError("database exact-source pool changed while snapshotting")
    snapshot["pool"]["bundle"] = str(pool_bundle_path)
    snapshot["pool"]["shaped_bundle"] = str(shaped_bundle_path)
    snapshot["pool"]["shaped_digest"] = shaped_bundle["digest"]
    snapshot["pool"]["shaped_edges"] = len(shaped_bundle["edges"])
    snapshot["pool"]["retrieval_outcomes"] = len(shaped_bundle["outcomes"])
    add_experiment_commands(
        snapshot, active_set_path, args.db, args.repo, args.results_dir,
        pool_bundle_path,
        model=args.model, samples=args.samples, temperature=args.temperature,
        repeats=args.repeats, split=args.split,
        shaped_bundle_path=shaped_bundle_path)
    _write_json(active_set_path, active_set)
    _write_json(pool_bundle_path, pool_bundle)
    _write_json(shaped_bundle_path, shaped_bundle)
    _write_json(args.out, snapshot)
    panel = snapshot["panel"]
    pool = snapshot["pool"]
    coverage = snapshot["coverage"]
    print(f"panel: {panel['active']} active / {panel['total']} frozen")
    print(f"verified source pool: {pool['exact_sources']} "
          f"({pool['medium_plus_sources']} medium+)")
    if coverage["measured"]:
        print(f"usable sibling coverage: {coverage['usable']}/{panel['active']}")
        print(f"bridge candidates: {len(snapshot['bridge_queue'])}")
        for row in snapshot["bridge_queue"][:5]:
            priority = (f"{row['priority']:.4f}" if row["priority"] is not None
                        else "unknown")
            print(f"  {row['function']}: unlocks {row['unlock_count']}, "
                  f"priority {priority}")
    return 0


def _compare_command(args: argparse.Namespace) -> int:
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    result = compare_snapshots(before, after)
    _write_json(args.out, result)
    if args.out:
        print(f"status: {result['status']}")
        print(f"pool growth: {result['pool_growth']:+d}")
        if result["usable_coverage_delta"] is not None:
            print(f"usable coverage: {result['usable_coverage_before']} -> "
                  f"{result['usable_coverage_after']}")
    return 0


def _arms_command(args: argparse.Namespace) -> int:
    result = compare_arms(_load_jsonl(args.control), _load_jsonl(args.treatment))
    _write_json(args.out, result)
    if args.out:
        print(f"status: {result['status']}")
        print(f"exact: {result['control_exact']} -> {result['treatment_exact']}")
        print(f"mean score delta: {result['mean_score_delta']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="generate a pool/panel snapshot and bridge queue")
    snap.add_argument("--repo", required=True, type=Path)
    snap.add_argument("--db", required=True, type=Path)
    snap.add_argument("--panel", required=True, type=Path)
    snap.add_argument("--triage", type=Path)
    snap.add_argument("--split", default="dev", choices=("dev", "heldout"))
    snap.add_argument("--matched-root", type=Path,
                      default=ROOT / "matched_recovered")
    snap.add_argument("--usable-threshold", type=float, default=0.75)
    snap.add_argument("--candidate-threshold", type=float, default=0.45)
    snap.add_argument("--similarity-cache", type=Path,
                      help="persistent name/score ranking cache (default: beside --out)")
    snap.add_argument("--skip-similarity", action="store_true",
                      help="snapshot the pool without the slower similarity graph")
    snap.add_argument(
        "--provenance-registry", type=Path,
        help="attach source-blind public N64 DEV hypotheses (never prompt context)")
    snap.add_argument("--out", required=True, type=Path)
    snap.add_argument("--active-set-out", type=Path)
    snap.add_argument("--pool-out", type=Path,
                      help="write the immutable verified-source bundle here")
    snap.add_argument("--shaped-pool-out", type=Path,
                      help="write the typed verified-function graph here")
    snap.add_argument("--compiler-label", default="IDO 5.3 -O2")
    snap.add_argument("--outcome-pair", nargs=2, action="append", default=[],
                      metavar=("CONTROL_JSONL", "TREATMENT_JSONL"),
                      help="attach one paired retrieval outcome receipt")
    snap.add_argument("--results-dir", type=Path, default=Path("eval/results"))
    snap.add_argument("--model", default="gpt-oss:20b")
    snap.add_argument("-n", "--samples", type=int, default=4)
    snap.add_argument("--temperature", type=float, default=0.7)
    snap.add_argument("--repeats", type=int, default=2)
    snap.set_defaults(run=_snapshot_command)

    compare = sub.add_parser("compare", help="compare two fixed-panel pool snapshots")
    compare.add_argument("--before", required=True, type=Path)
    compare.add_argument("--after", required=True, type=Path)
    compare.add_argument("--out", type=Path)
    compare.set_defaults(run=_compare_command)

    arms = sub.add_parser("arms", help="compare sibling-off/on JSONL results")
    arms.add_argument("--control", required=True, type=Path)
    arms.add_argument("--treatment", required=True, type=Path)
    arms.add_argument("--out", type=Path)
    arms.set_defaults(run=_arms_command)

    args = parser.parse_args()
    try:
        return args.run(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
