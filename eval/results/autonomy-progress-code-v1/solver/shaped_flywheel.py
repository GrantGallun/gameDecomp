"""Typed, tamper-evident retrieval memory for verified decompilation work.

The legacy sibling pool is intentionally a flat ``name -> exact C`` mapping.
That was enough to test whether whole-function examples can help, but it makes
the model rediscover the reusable lesson on every prompt.  This module freezes
four distinct layers instead:

* trust receipts -- why a source body is allowed into the library;
* machine profiles -- mechanically derived function/evidence-table facts;
* source profiles -- mechanical syntax summaries of byte-exact C;
* outcome receipts -- what happened when a target/candidate relation was used.

Assembly similarity and every derived relation remain ranking hints.  Only the
target compiler and byte oracle may promote a new exact function.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Iterable

from eval.sibling_coverage import similarity_band
from solver import siblings


SCHEMA_VERSION = 1
KIND = "verified_exact_evidence_graph"
CONTROL_WORDS = ("if", "else", "for", "while", "do", "switch", "case",
                 "goto", "return")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
MEMBER_RE = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)")
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
CALL_EXCLUSIONS = frozenset({
    "if", "for", "while", "switch", "return", "sizeof", "defined",
})
GENERIC_NAME_TOKENS = frozenset({
    "get", "set", "init", "update", "draw", "main", "func", "function",
})


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest_payload(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(_canonical(body)).hexdigest()[:16]


def artifact_ref(path: Path) -> dict[str, object]:
    """Content-address an experiment artifact without embedding its rows."""
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(
            f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _name_tokens(name: str) -> list[str]:
    tokens = [token.lower() for token in CAMEL_RE.findall(name)]
    return [token for token in tokens
            if len(token) > 1 and token not in GENERIC_NAME_TOKENS]


def source_profile(name: str, source: str) -> dict[str, object]:
    """Return a conservative, mechanical syntax profile of verified C."""
    clean = STRING_RE.sub('""', COMMENT_RE.sub("", source or ""))
    calls = []
    for called in CALL_RE.findall(clean):
        if called == name or called in CALL_EXCLUSIONS or called in calls:
            continue
        calls.append(called)
    members = []
    for member in MEMBER_RE.findall(clean):
        if member not in members:
            members.append(member)
    controls = {
        word: len(re.findall(rf"\b{word}\b", clean))
        for word in CONTROL_WORDS
    }
    return {
        "role": "mechanically extracted from byte-exact compatible C",
        "line_count": len((source or "").splitlines()),
        "char_count": len(source or ""),
        "statement_count": clean.count(";"),
        "control": controls,
        "call_sequence": calls[:32],
        "member_names": members[:48],
        "name_tokens": _name_tokens(name),
    }


def _base_class(base: object) -> str | None:
    if not isinstance(base, str) or base == "unknown":
        return None
    if base.startswith("param"):
        return base
    if base.startswith("global:"):
        # Globals are genuine cross-function identities inside one target.
        # Collapsing every address to merely "global" manufactures overlap at
        # offset zero between unrelated symbols.  Cross-game retrieval needs a
        # separate address-independent view, not loss of same-game identity.
        return base
    if base == "stack":
        return "stack"
    return base


def machine_profile(conn: sqlite3.Connection, name: str) -> dict[str, object]:
    """Extract target-safe machine facts, tolerating partial historical DBs."""
    fcols = _columns(conn, "functions")
    if not {"name", "addr"} <= fcols:
        return {"role": "binary-derived evidence", "function": name}
    wanted = [column for column in
              ("addr", "size", "insn_count", "is_leaf", "tu_id")
              if column in fcols]
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM functions WHERE name=? LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        return {"role": "binary-derived evidence", "function": name}
    values = dict(zip(wanted, row))
    addr = values.get("addr")
    profile: dict[str, object] = {
        "role": "binary-derived evidence",
        "function": name,
        "address": (f"0x{int(addr) & 0xFFFFFFFF:08X}"
                    if addr is not None else None),
        "size": values.get("size"),
        "instruction_count": values.get("insn_count"),
        "is_leaf": (bool(values["is_leaf"])
                    if values.get("is_leaf") is not None else None),
        "tu_id": values.get("tu_id"),
        "direct_calls": [],
        "indirect_call_count": 0,
        "memory_shapes": [],
    }

    ecols = _columns(conn, "evidence")
    if addr is None or not {"kind", "func_addr"} <= ecols:
        return profile

    if "target_addr" in ecols:
        direct = []
        if {"addr", "name"} <= fcols:
            direct = [str(item[0]) for item in conn.execute(
                "SELECT DISTINCT tf.name FROM evidence e "
                "JOIN functions tf ON tf.addr=e.target_addr "
                "WHERE e.kind='call' AND e.func_addr=? "
                "ORDER BY tf.name", (addr,))]
        profile["direct_calls"] = direct
        profile["indirect_call_count"] = int(conn.execute(
            "SELECT count(*) FROM evidence WHERE kind='call' "
            "AND func_addr=? AND target_addr IS NULL", (addr,)
        ).fetchone()[0] or 0)

    required = {"base", "offset", "width", "signed", "class", "is_load"}
    if required <= ecols:
        shapes = set()
        for base, offset, width, signed, cls, is_load in conn.execute(
                "SELECT base, offset, width, signed, class, is_load "
                "FROM evidence WHERE kind='mem_access' AND func_addr=? "
                "AND offset IS NOT NULL AND width IS NOT NULL", (addr,)):
            base_kind = _base_class(base)
            if base_kind is None or base_kind == "stack":
                continue
            sign = "unknown" if signed is None else (
                "signed" if int(signed) else "unsigned")
            shapes.add(
                f"{base_kind}@{int(offset):+#x}:{int(width)}:"
                f"{cls or 'unknown'}:{sign}:"
                f"{'read' if is_load else 'write'}")
        profile["memory_shapes"] = sorted(shapes)[:128]
    return profile


def _latest_receipts(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    acols = _columns(conn, "attempts")
    if not {"id", "func_addr", "exact", "source_code"} <= acols:
        return {}
    strategy = "coalesce(a.strategy, '')" if "strategy" in acols else "''"
    rows = conn.execute(
        f"SELECT f.name, a.id, {strategy} FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr "
        "WHERE a.exact=1 AND a.source_code IS NOT NULL ORDER BY a.id DESC")
    receipts = {}
    for name, attempt_id, strategy_value in rows:
        receipts.setdefault(str(name), {
            "exact": True,
            "attempt_id": int(attempt_id),
            "strategy": str(strategy_value or "unknown-exact-strategy"),
            "authority": "target compiler plus byte-exact object oracle",
        })
    return receipts


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float | None:
    a, b = set(left), set(right)
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def _derived_edges(nodes: dict[str, dict[str, object]],
                   max_shape_edges: int = 4) -> list[dict[str, object]]:
    """Create bounded, explicitly non-authoritative relations."""
    edges: list[dict[str, object]] = []
    names = set(nodes)
    for source, node in sorted(nodes.items()):
        machine = node["machine"]
        for target in machine.get("direct_calls", []):
            if target in names:
                edges.append({
                    "source": source, "target": target,
                    "relation": "calls",
                    "tier": "evidence",
                    "basis": "binary call target",
                })

    for source in sorted(nodes):
        candidates = []
        left_machine = nodes[source]["machine"]
        left_source = nodes[source]["source_profile"]
        for target in sorted(nodes):
            if target <= source:
                continue
            right_machine = nodes[target]["machine"]
            right_source = nodes[target]["source_profile"]
            memory = _jaccard(left_machine.get("memory_shapes", []),
                               right_machine.get("memory_shapes", []))
            calls = _jaccard(left_machine.get("direct_calls", []),
                              right_machine.get("direct_calls", []))
            namescore = _jaccard(left_source.get("name_tokens", []),
                                 right_source.get("name_tokens", []))
            signals = [score for score in (memory, calls, namescore)
                       if score is not None]
            if not signals:
                continue
            score = sum(signals) / len(signals)
            if score >= 0.45:
                candidates.append((score, target, memory, calls, namescore))
        for score, target, memory, calls, namescore in sorted(
                candidates, reverse=True)[:max_shape_edges]:
            edges.append({
                "source": source, "target": target,
                "relation": "derived_shape_affinity",
                "tier": "ranking_hint",
                "score": round(score, 6),
                "components": {
                    "memory_jaccard": memory,
                    "call_jaccard": calls,
                    "name_token_jaccard": namescore,
                },
                "basis": "mechanical profiles; never exactness evidence",
            })
    return edges


def build_library(conn: sqlite3.Connection, sources: dict[str, str], *,
                  outcomes: list[dict[str, object]] | None = None,
                  compiler: str = "unknown") -> dict[str, object]:
    """Build an immutable graph from sources carrying exact receipts."""
    receipts = _latest_receipts(conn)
    missing = sorted(set(sources) - set(receipts))
    if missing:
        raise ValueError(
            "shaped library source lacks an exact receipt: " + ", ".join(missing[:5]))
    nodes = {}
    for name, source in sorted(sources.items()):
        nodes[name] = {
            "trust": receipts[name],
            "machine": machine_profile(conn, name),
            "source_profile": source_profile(name, source),
            "exact_source": source,
        }
    for outcome in outcomes or []:
        if not isinstance(outcome, dict) or outcome.get(
                "relation") != "retrieval_outcome":
            raise ValueError("shaped library has an invalid outcome receipt")
        if outcome.get("candidate") not in nodes:
            raise ValueError(
                f"retrieval outcome candidate is not exact: {outcome.get('candidate')}")
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "compiler": compiler,
        "policies": {
            "exact_source_membership": "positive byte-oracle receipt required",
            "machine_profile": "binary-derived evidence",
            "source_profile": "mechanical summary of compatible exact C",
            "relations": "ranking hints unless tier=evidence",
            "default_source_policy": "full source only at assembly similarity >=0.90",
            "promotion": "target compile plus byte-exact oracle only",
        },
        "nodes": nodes,
        "edges": _derived_edges(nodes),
        "outcomes": list(outcomes or []),
    }
    payload["digest"] = _digest_payload(payload)
    return payload


def load_library(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != KIND:
        raise ValueError(f"{path} is not a shaped verified-function library")
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict) or not all(
            isinstance(name, str) and isinstance(node, dict)
            and node.get("trust", {}).get("exact") is True
            and isinstance(node.get("exact_source"), str)
            for name, node in nodes.items()):
        raise ValueError(f"{path} has invalid shaped-library nodes")
    if payload.get("digest") != _digest_payload(payload):
        raise ValueError(f"{path} shaped-library digest mismatch")
    return payload


def outcome_receipts(control: dict[str, dict[str, object]],
                     treatment: dict[str, dict[str, object]], *,
                     arm_pair: str,
                     control_ref: dict[str, object] | None = None,
                     treatment_ref: dict[str, object] | None = None,
                     ) -> list[dict[str, object]]:
    """Turn paired arm rows into bounded experimental relation receipts."""
    receipts = []
    for name in sorted(set(control) & set(treatment)):
        c, t = control[name], treatment[name]
        if int(c.get("draws", 0)) == 0 or int(t.get("draws", 0)) == 0:
            continue
        candidate = t.get("sibling")
        if not isinstance(candidate, str) or not candidate:
            continue
        c_score = float(c.get("best_score", 0.0))
        t_score = float(t.get("best_score", 0.0))
        receipts.append({
            "target": name,
            "candidate": candidate,
            "relation": "retrieval_outcome",
            "tier": "experiment_receipt",
            "arm_pair": arm_pair,
            "control_artifact": control_ref,
            "treatment_artifact": treatment_ref,
            "similarity": t.get("similarity"),
            "control_exact": bool(c.get("exact")),
            "treatment_exact": bool(t.get("exact")),
            "exact_delta": int(bool(t.get("exact"))) - int(bool(c.get("exact"))),
            "control_score": c_score,
            "treatment_score": t_score,
            "score_delta": round(t_score - c_score, 6),
            "interpretation": "score is secondary; exact delta is decisive",
        })
    return receipts


def _size_compatibility(left: object, right: object) -> float | None:
    try:
        a, b = int(left), int(right)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    return min(a, b) / max(a, b)


def rank(repo: Path, conn: sqlite3.Connection, target: str,
         library: dict[str, object], *, top: int = 3,
         min_assembly: float = 0.45, historical: bool = False,
         retriever: Callable | None = None) -> list[dict[str, object]]:
    """Assembly-gate then transparently rerank by compatible evidence layers."""
    nodes = library["nodes"]
    find = retriever or siblings.find
    ranked = find(repo, target, top=max(top * 8, 24), min_score=min_assembly,
                  historical=historical, allowed=set(nodes))
    target_machine = machine_profile(conn, target)
    target_tokens = _name_tokens(target)
    outcomes = library.get("outcomes", [])
    result = []
    for name, assembly, _path in ranked:
        if name == target or name not in nodes:
            continue
        node = nodes[name]
        machine = node["machine"]
        components: dict[str, float] = {"assembly_similarity": float(assembly)}
        weighted = [(0.60, float(assembly))]
        size = _size_compatibility(target_machine.get("instruction_count"),
                                   machine.get("instruction_count"))
        if size is not None:
            components["instruction_count_compatibility"] = size
            weighted.append((0.15, size))
        left_leaf, right_leaf = target_machine.get("is_leaf"), machine.get("is_leaf")
        if left_leaf is not None and right_leaf is not None:
            leaf = float(left_leaf == right_leaf)
            components["leaf_compatibility"] = leaf
            weighted.append((0.10, leaf))
        calls = _jaccard(target_machine.get("direct_calls", []),
                         machine.get("direct_calls", []))
        if calls is not None:
            components["call_jaccard"] = calls
            weighted.append((0.10, calls))
        memory = _jaccard(target_machine.get("memory_shapes", []),
                          machine.get("memory_shapes", []))
        if memory is not None:
            components["memory_shape_jaccard"] = memory
            weighted.append((0.05, memory))
        name_score = _jaccard(target_tokens,
                              node["source_profile"].get("name_tokens", []))
        if name_score is not None:
            components["name_token_jaccard_hint"] = name_score
        total_weight = sum(weight for weight, _score in weighted)
        shaped_score = sum(weight * score for weight, score in weighted) / total_weight
        history = [receipt for receipt in outcomes
                   if receipt.get("target") == target
                   and receipt.get("candidate") == name]
        result.append({
            "candidate": name,
            "assembly_similarity": float(assembly),
            "assembly_band": similarity_band(float(assembly)),
            "shaped_score": round(shaped_score, 6),
            "components": components,
            "shared_calls": sorted(set(target_machine.get("direct_calls", []))
                                   & set(machine.get("direct_calls", []))),
            "shared_memory_shapes": sorted(
                set(target_machine.get("memory_shapes", []))
                & set(machine.get("memory_shapes", [])))[:16],
            "outcomes": history,
            "node": node,
        })
    result.sort(key=lambda row: (-float(row["shaped_score"]),
                                 -float(row["assembly_similarity"]),
                                 str(row["candidate"])))
    return result[:top]


def render_context(matches: list[dict[str, object]], *,
                   full_source_threshold: float = 0.90,
                   max_source_chars: int = 6000) -> str:
    if not matches:
        return ""
    lines = [
        "\nSHAPED VERIFIED-FUNCTION EVIDENCE:",
        "Each candidate has a byte-exact receipt. Relationships and scores are",
        "ranking hints, not proof that the target has the same source shape.",
        "Use shared machine facts as constraints; treat source-derived structure",
        "as a proposal. The target compiler and byte oracle remain authoritative.",
    ]
    for match in matches:
        node = match["node"]
        trust = node["trust"]
        machine = node["machine"]
        source = node["source_profile"]
        control_summary = ", ".join(
            f"{key}={value}" for key, value in source["control"].items()
            if value)
        leaf_value = machine.get("is_leaf")
        leaf_label = ("unknown" if leaf_value is None else
                      "leaf" if leaf_value else "non-leaf")
        lines.extend([
            f"\n- {match['candidate']}: assembly {match['assembly_similarity']:.3f} "
            f"({match['assembly_band']}), shaped rank {match['shaped_score']:.3f}",
            f"  trust: exact attempt {trust['attempt_id']} via {trust['strategy']}",
            "  rank components: " + ", ".join(
                f"{name}={value:.3f}" for name, value in
                sorted(match["components"].items())),
            ("  machine shape: "
             f"{machine.get('instruction_count', '?')} instructions, "
             f"{leaf_label}, "
             f"calls={len(machine.get('direct_calls', []))}, "
             f"memory-shapes={len(machine.get('memory_shapes', []))}"),
            "  source structure: " + (control_summary or "straight-line"),
        ])
        if source.get("call_sequence"):
            lines.append("  verified call sequence: " + ", ".join(
                source["call_sequence"][:12]))
        if match["shared_calls"]:
            lines.append("  shared binary calls: " + ", ".join(match["shared_calls"]))
        if match["shared_memory_shapes"]:
            lines.append("  shared memory constraints: " + "; ".join(
                match["shared_memory_shapes"][:8]))
        history = match.get("outcomes") or []
        if history:
            deltas = [float(item["score_delta"]) for item in history]
            exact = sum(int(item["exact_delta"]) for item in history)
            lines.append(
                f"  prior retrieval receipts: n={len(history)}, "
                f"mean score delta={sum(deltas)/len(deltas):+.3f}, "
                f"net exact delta={exact:+d}; score remains secondary")
        if float(match["assembly_similarity"]) >= full_source_threshold:
            body = str(node["exact_source"])
            if len(body) > max_source_chars:
                body = body[:max_source_chars].rstrip() + "\n/* exact source truncated */"
            lines.extend([
                "  strong-match exact body (copy only compatible structure):",
                "```c", body, "```",
            ])
        else:
            lines.append(
                "  full body withheld below the strong-match threshold to reduce anchoring")
    lines.append(
        "\nGenerate an independent target candidate. Do not invent compatibility "
        "where the machine profiles disagree.\n")
    return "\n".join(lines)


def context_block(repo: Path, conn: sqlite3.Connection, target: str,
                  library: dict[str, object], *, top: int = 2,
                  historical: bool = False) -> str:
    return render_context(rank(repo, conn, target, library, top=top,
                               historical=historical))


def _load_jsonl(path: Path) -> dict[str, dict[str, object]]:
    rows = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc.msg}") from exc
        rows[str(row["function"])] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compiler", default="unknown")
    parser.add_argument("--outcome-pair", nargs=2, action="append", default=[],
                        metavar=("CONTROL_JSONL", "TREATMENT_JSONL"))
    args = parser.parse_args()
    conn = sqlite3.connect(args.db.expanduser(), timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    try:
        sources = siblings.verified_sources(conn)
        outcomes = []
        for index, (control, treatment) in enumerate(args.outcome_pair, 1):
            control_path, treatment_path = Path(control), Path(treatment)
            outcomes.extend(outcome_receipts(
                _load_jsonl(control_path), _load_jsonl(treatment_path),
                arm_pair=f"pair-{index}",
                control_ref=artifact_ref(control_path),
                treatment_ref=artifact_ref(treatment_path)))
        library = build_library(conn, sources, outcomes=outcomes,
                                compiler=args.compiler)
    finally:
        conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(library, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"nodes: {len(library['nodes'])}; edges: {len(library['edges'])}; "
          f"outcomes: {len(library['outcomes'])}; digest: {library['digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
