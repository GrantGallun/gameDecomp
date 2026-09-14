"""Governed retrieval from a finished same-game decompilation.

The finished corpus is useful teacher data, but it is not clean target evidence.
Packets produced here are explicitly labelled leave-one-function-out (LOFO) or
leave-one-translation-unit-out (LOTO).  The selected target definition is read
only to fingerprint and exclude it; it is never emitted.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re

from solver import shaped_flywheel, siblings
from tools import n64_corpus


REGIMES = {"leave_one_function_out", "leave_one_tu_out"}


@dataclass(frozen=True)
class FunctionMeta:
    name: str
    tu_id: int | None
    tu_path: str
    instruction_count: int | None


_FILE_CACHE: dict[Path, dict[str, dict[str, object]]] = {}


def clear_cache() -> None:
    _FILE_CACHE.clear()


def _source_tu_path(path: str) -> str:
    """Map miner object-unit names back to their finished source path."""
    normalized = str(path or "").replace("\\", "/")
    if normalized.startswith("build/") and normalized.endswith(".o"):
        return normalized[len("build/"):-2] + ".c"
    return normalized


def metadata(conn: sqlite3.Connection) -> dict[str, FunctionMeta]:
    return {
        str(name): FunctionMeta(
            str(name), int(tu_id) if tu_id is not None else None,
            _source_tu_path(str(path or "")),
            int(insns) if insns is not None else None)
        for name, tu_id, path, insns in conn.execute(
            "SELECT f.name,f.tu_id,t.name,f.insn_count FROM functions f "
            "LEFT JOIN tus t ON t.id=f.tu_id")
    }


def _definitions(path: Path) -> dict[str, dict[str, object]]:
    resolved = path.resolve()
    cached = _FILE_CACHE.get(resolved)
    if cached is not None:
        return cached
    try:
        source = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        result: dict[str, dict[str, object]] = {}
    else:
        result = {str(row["name"]): row
                  for row in n64_corpus.extract_functions(source)}
    _FILE_CACHE[resolved] = result
    return result


def _definition(repo: Path, meta: FunctionMeta) -> str:
    if not meta.tu_path:
        return ""
    row = _definitions(repo / meta.tu_path).get(meta.name)
    return str(row.get("definition", "")) if row else ""


def _normalized_digest(definition: str) -> str:
    tokens, _constants, _calls = n64_corpus.normalized_tokens(definition)
    return hashlib.sha256("\x1f".join(tokens).encode()).hexdigest()


def _source_digest(definition: str) -> str:
    return hashlib.sha256(definition.encode()).hexdigest()


def _packet_digest(packet: dict) -> str:
    unsigned = dict(packet)
    unsigned.pop("packet_digest", None)
    return hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _size_similarity(left: int | None, right: int | None) -> float:
    if not left or not right:
        return 0.0
    return min(left, right) / max(left, right)


def _name_similarity(left: str, right: str) -> float:
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio()


def _resolve_path(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _similarity_rows(repo: Path, target: str,
                     top: int = 24) -> list[tuple[str, float, Path]]:
    return siblings.find(
        repo, target, top=top, min_score=0.0, timeout=600,
        historical=False)


def _source_blocks(definition: str) -> list[str]:
    """Nested C blocks from one exact definition, excluding the outer body."""
    masked = n64_corpus._mask_noncode(definition)
    outer = masked.find("{")
    if outer < 0:
        return []
    rows = []
    cursor = outer + 1
    while cursor < len(masked):
        opening = masked.find("{", cursor)
        if opening < 0:
            break
        closing = n64_corpus._matching_right(masked, opening, "{", "}")
        if closing is None:
            break
        fragment = definition[opening:closing + 1].strip()
        if 20 <= len(fragment) <= 3000:
            rows.append(fragment)
        cursor = opening + 1
    return rows


def _idioms(examples: list[dict], logic_packet: dict,
            limit: int = 6) -> list[dict]:
    target_row = next((row for row in logic_packet.get("functions", [])
                       if row.get("active_target")), {})
    profile = target_row.get("target_profile") or {}
    target_calls = set(profile.get("direct_call_sequence") or []) - {"<indirect>"}
    target_branches = len(profile.get("branch_shapes") or [])
    candidates = []
    seen = set()
    for example_index, example in enumerate(examples):
        for block_index, block in enumerate(_source_blocks(
                example["exact_reference_source"])):
            normalized = _normalized_digest(block)
            if normalized in seen:
                continue
            seen.add(normalized)
            _tokens, _constants, calls = n64_corpus.normalized_tokens(block)
            call_overlap = len(target_calls & set(calls))
            control_count = sum(len(re.findall(
                rf"\b{word}\b", n64_corpus._mask_noncode(block)))
                for word in ("if", "else", "for", "while", "switch"))
            # Earlier examples already carry stronger function-level evidence.
            # Calls shared with the target dominate; control-count proximity is
            # only a weak tiebreaker and never a semantic claim.
            rank = (call_overlap, -abs(target_branches - control_count),
                    -example_index, -len(block), -block_index)
            candidates.append((rank, {
                "kind": "matched_source_block",
                "authority": "exact sibling source; target relevance is a ranking hint",
                "function": example["function"],
                "tu_path": example["tu_path"],
                "parent_source_sha256": example["source_sha256"],
                "normalized_block_sha256": normalized,
                "shared_target_calls": sorted(target_calls & set(calls)),
                "control_keyword_count": control_count,
                "source": block,
            }))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [row for _rank, row in candidates[:max(0, limit)]]


def build_packet(*, repo: Path, conn: sqlite3.Connection,
                 logic_packet: dict, regime: str,
                 max_examples: int = 6,
                 max_source_chars: int = 16000,
                 similar: list[tuple[str, float, Path]] | None = None) -> dict:
    if regime not in REGIMES:
        raise ValueError(f"unknown teacher regime: {regime}")
    target = str(logic_packet.get("function") or "")
    metas = metadata(conn)
    target_meta = metas.get(target)
    if target_meta is None or not target_meta.tu_path:
        raise ValueError(f"no reference TU metadata for {target}")
    target_definition = _definition(repo, target_meta)
    if not target_definition:
        raise ValueError(f"finished reference definition not found for {target}")
    target_source_sha256 = _source_digest(target_definition)
    target_normalized_sha256 = _normalized_digest(target_definition)

    binary_neighbors = set()
    for edge in logic_packet.get("binary_call_edges", []):
        caller, callee = edge.get("caller"), edge.get("callee")
        if caller == target and isinstance(callee, str):
            binary_neighbors.add(callee)
        if callee == target and isinstance(caller, str):
            binary_neighbors.add(caller)
    module_names = {
        str(row.get("function")) for row in logic_packet.get("functions", [])
        if isinstance(row, dict) and isinstance(row.get("function"), str)
    }
    candidates: dict[str, dict[str, object]] = {}

    def add(name: str, relation: str, similarity: float | None = None,
            path_hint: Path | None = None) -> None:
        if name == target or name not in metas:
            return
        meta = metas[name]
        if regime == "leave_one_tu_out" and meta.tu_id == target_meta.tu_id:
            return
        row = candidates.setdefault(name, {
            "relations": set(), "assembly_similarity": similarity,
            "path_hint": path_hint,
        })
        row["relations"].add(relation)
        if similarity is not None:
            current = row.get("assembly_similarity")
            row["assembly_similarity"] = max(float(current or 0), similarity)
        if path_hint is not None:
            row["path_hint"] = path_hint

    for name, meta in metas.items():
        if name != target and meta.tu_id == target_meta.tu_id:
            add(name, "same_translation_unit")
    for name in binary_neighbors:
        add(name, "binary_call_neighbor")
    for name in module_names:
        if name != target:
            add(name, "logic_cluster_member")
    for name, score, path in similar or _similarity_rows(repo, target):
        add(name, "assembly_similar", float(score), path)

    ranked = []
    for name, retrieval in candidates.items():
        meta = metas[name]
        definition = _definition(repo, meta)
        if not definition and retrieval.get("path_hint") is not None:
            row = _definitions(_resolve_path(repo, retrieval["path_hint"])).get(name)
            definition = str(row.get("definition", "")) if row else ""
        if not definition:
            continue
        normalized = _normalized_digest(definition)
        if normalized == target_normalized_sha256:
            continue
        relations = sorted(retrieval["relations"])
        relation_weight = sum({
            "binary_call_neighbor": 4.0,
            "same_translation_unit": 3.0,
            "logic_cluster_member": 2.0,
            "assembly_similar": 1.0,
        }.get(item, 0.0) for item in relations)
        similarity = float(retrieval.get("assembly_similarity") or 0.0)
        rank = (
            relation_weight, similarity,
            _name_similarity(target, name),
            _size_similarity(target_meta.instruction_count,
                             meta.instruction_count),
            name,
        )
        ranked.append((rank, name, meta, definition, normalized, relations,
                       similarity))
    ranked.sort(reverse=True)

    examples = []
    omitted_oversize = []
    for _rank, name, meta, definition, normalized, relations, similarity in ranked:
        if len(examples) >= max(0, max_examples):
            break
        if len(definition) > max_source_chars:
            omitted_oversize.append(name)
            continue
        examples.append({
            "function": name,
            "tu_id": meta.tu_id,
            "tu_path": meta.tu_path,
            "relations": relations,
            "assembly_similarity": round(similarity, 6),
            "instruction_count": meta.instruction_count,
            "source_sha256": _source_digest(definition),
            "normalized_source_sha256": normalized,
            "source_profile": shaped_flywheel.source_profile(name, definition),
            "exact_reference_source": definition,
        })

    serialized_examples = json.dumps(examples, sort_keys=True)
    if target_definition in serialized_examples:
        raise RuntimeError("target reference definition leaked into teacher examples")
    if any(example["function"] == target for example in examples):
        raise RuntimeError("target function leaked into teacher examples")
    target_tu_present = any(
        example["tu_id"] == target_meta.tu_id for example in examples)
    if regime == "leave_one_tu_out" and target_tu_present:
        raise RuntimeError("target translation unit leaked into LOTO packet")

    packet = {
        "schema_version": 1,
        "kind": "finished-reference-teacher-packet",
        "function": target,
        "regime": regime,
        "policies": {
            "same_game_finished_reference": True,
            "target_definition_emitted": False,
            "target_definition_read_for_exclusion_only": True,
            "target_tu_excluded": regime == "leave_one_tu_out",
            "valid_claim": (
                "same-game leave-one-function-out teacher assistance"
                if regime == "leave_one_function_out" else
                "same-game leave-one-translation-unit-out teacher assistance"),
            "not_valid_as": "cold-start or cross-game autonomous decompilation",
        },
        "target_provenance": {
            "tu_id": target_meta.tu_id,
            "tu_path": target_meta.tu_path,
            "reference_source_sha256_exclusion_fingerprint": target_source_sha256,
            "normalized_source_sha256_exclusion_fingerprint":
                target_normalized_sha256,
        },
        "logic_context": logic_packet,
        "examples": examples,
        "matched_source_blocks": _idioms(examples, logic_packet),
        "retrieval": {
            "candidate_count": len(candidates),
            "eligible_ranked": len(ranked),
            "emitted": len(examples),
            "omitted_oversize": omitted_oversize,
            "max_examples": max_examples,
            "max_source_chars": max_source_chars,
            "block_idioms_are_unaligned_ranking_hints": True,
        },
        "contamination_audit": {
            "target_function_absent": True,
            "target_definition_absent": True,
            "normalized_target_duplicate_absent": all(
                example["normalized_source_sha256"] != target_normalized_sha256
                for example in examples),
            "target_tu_present": target_tu_present,
            "target_tu_policy_satisfied": (
                regime == "leave_one_function_out" or not target_tu_present),
        },
    }
    packet["packet_digest"] = _packet_digest(packet)
    return packet


def load_packet(path: Path) -> dict:
    packet = json.loads(path.read_text(encoding="utf-8"))
    if packet.get("schema_version") != 1 or packet.get(
            "kind") != "finished-reference-teacher-packet":
        raise ValueError(f"{path} is not a reference-teacher packet")
    if packet.get("regime") not in REGIMES:
        raise ValueError(f"{path} has an invalid teacher regime")
    if packet.get("packet_digest") != _packet_digest(packet):
        raise ValueError(f"{path} reference-teacher digest mismatch")
    target = packet.get("function")
    provenance = packet.get("target_provenance") or {}
    target_tu = provenance.get("tu_id")
    target_normalized = provenance.get(
        "normalized_source_sha256_exclusion_fingerprint")
    examples = packet.get("examples")
    if not isinstance(target, str) or not isinstance(examples, list):
        raise ValueError(f"{path} has invalid reference examples")
    example_names = set()
    for example in examples:
        if not isinstance(example, dict) or not isinstance(
                example.get("function"), str):
            raise ValueError(f"{path} has an invalid reference example")
        if example["function"] == target:
            raise ValueError(f"{path} contains its target function")
        if example.get("normalized_source_sha256") == target_normalized:
            raise ValueError(f"{path} contains a normalized target duplicate")
        if packet["regime"] == "leave_one_tu_out" \
                and example.get("tu_id") == target_tu:
            raise ValueError(f"{path} contains its target translation unit")
        example_names.add(example["function"])
    blocks = packet.get("matched_source_blocks", [])
    if not isinstance(blocks, list) or any(
            not isinstance(block, dict)
            or block.get("function") not in example_names for block in blocks):
        raise ValueError(f"{path} has an unowned matched-source block")
    audit = packet.get("contamination_audit") or {}
    required = (
        "target_function_absent", "target_definition_absent",
        "normalized_target_duplicate_absent", "target_tu_policy_satisfied",
    )
    if not all(audit.get(key) is True for key in required):
        raise ValueError(f"{path} failed its contamination audit")
    return packet
