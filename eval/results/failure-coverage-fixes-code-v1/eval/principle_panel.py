"""Freeze a DEV-only panel selected by true compiler/object residuals.

This command may inspect and compile existing attempted candidates, so its
output is an experimental DEV panel, never a held-out assignment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair, matched
from solver import principles, residual, workspace


def _heldout(set_path: Path) -> set[str]:
    paths = sorted(set_path.glob("*.json")) if set_path.is_dir() else [set_path]
    names: set[str] = set()
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        names.update(row["function"] for row in value.get("heldout", [])
                     if isinstance(row, dict)
                     and isinstance(row.get("function"), str))
    return names


def candidate_rows(conn: sqlite3.Connection, heldout: set[str],
                   scan_limit: int) -> list[tuple]:
    done = matched.already_matched(conn)
    rows = conn.execute(
        "SELECT f.name,a.id,a.source_code,a.score FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr "
        "WHERE a.compiled=1 AND COALESCE(a.exact,0)=0 "
        "AND a.source_code IS NOT NULL "
        "ORDER BY a.score DESC,a.id DESC").fetchall()
    selected = []
    seen: set[str] = set()
    for name, attempt_id, source, score in rows:
        if name in seen or name in heldout or name in done:
            continue
        seen.add(name)
        selected.append((name, int(attempt_id), str(source), float(score)))
        if len(selected) >= scan_limit:
            break
    return selected


def freeze(*, repo: Path, db: Path, sets: Path, out: Path,
           limit: int = 8, scan_limit: int = 60,
           max_byte_distance: int = 64,
           max_instruction_delta: int = 2,
           max_structural_faults: int = 2) -> dict:
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    blocked = _heldout(sets)
    candidates = candidate_rows(conn, blocked, scan_limit)
    eligible = []
    scanned = []
    for index, (name, attempt_id, source, stored_score) in enumerate(candidates):
        agentrepair._refuse_frozen_heldout(sets, name)
        ws = workspace.bootstrap(repo, name)
        tag = f"{name}_principle_panel_{time.time_ns()}_{index}"
        attempt = workspace.score(ws, repo, tag, source)
        object_path = ws / f"{tag}.o" if attempt.compiled else None
        target_asm = workspace.target_asm(ws, name)
        packet = residual.build(
            attempt, target_asm=target_asm,
            target_object=ws / "target.o", candidate_object=object_path)
        row = {
            "function": name,
            "attempt_id": attempt_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "stored_weighted_score": stored_score,
            "fresh_residual": packet.to_dict(),
        }
        reasons = []
        if not attempt.compiled:
            reasons.append("fresh compile failed")
        if attempt.exact:
            reasons.append("fresh root is already exact")
        if packet.text_length_delta != 0:
            reasons.append("text lengths differ")
        if packet.instruction_delta is None or \
                abs(packet.instruction_delta) > max_instruction_delta:
            reasons.append("instruction delta exceeds cap")
        if packet.positional_byte_distance is None or \
                packet.positional_byte_distance > max_byte_distance:
            reasons.append("byte distance exceeds cap")
        if packet.faults.get("structural", 0) > max_structural_faults:
            reasons.append("structural faults exceed cap")
        row["eligible"] = not reasons
        row["exclusion_reasons"] = reasons
        scanned.append(row)
        if not reasons:
            matches = principles.retrieve(
                target_asm, attempt, packet, include_hypotheses=True)
            row["principle_ids"] = [match.pattern_id for match in matches]
            eligible.append(row)

    eligible.sort(key=lambda row: (
        row["fresh_residual"]["faults"]["structural"],
        abs(row["fresh_residual"]["instruction_delta"]),
        row["fresh_residual"]["positional_byte_distance"],
        -row["fresh_residual"]["weighted_progress_score"],
        row["function"],
    ))
    panel = eligible[:limit]
    manifest = {
        "schema_version": 1,
        "kind": "true-residual-dev-principle-panel",
        "created_at": int(time.time()),
        "selection": {
            "heldout_excluded_across_all_manifests": True,
            "fresh_compile_required": True,
            "exact_roots_excluded": True,
            "equal_text_length_required": True,
            "max_byte_distance": max_byte_distance,
            "max_instruction_delta": max_instruction_delta,
            "max_structural_faults": max_structural_faults,
            "scan_limit": scan_limit,
            "requested_panel_size": limit,
        },
        "panel": panel,
        "eligible_count": len(eligible),
        "scanned_count": len(scanned),
        "shortfall": max(0, limit - len(panel)),
        "scanned": scanned,
    }
    digest_value = dict(manifest)
    manifest["manifest_digest"] = hashlib.sha256(json.dumps(
        digest_value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    agentrepair._atomic_json(out, manifest)
    conn.close()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--scan-limit", type=int, default=60)
    parser.add_argument("--max-byte-distance", type=int, default=64)
    parser.add_argument("--max-instruction-delta", type=int, default=2)
    parser.add_argument("--max-structural-faults", type=int, default=2)
    args = parser.parse_args()
    manifest = freeze(
        repo=args.repo.expanduser().resolve(),
        db=args.db.expanduser().resolve(),
        sets=args.sets.expanduser().resolve(),
        out=args.out.expanduser().resolve(), limit=args.limit,
        scan_limit=args.scan_limit,
        max_byte_distance=args.max_byte_distance,
        max_instruction_delta=args.max_instruction_delta,
        max_structural_faults=args.max_structural_faults)
    print(json.dumps({
        "panel_size": len(manifest["panel"]),
        "eligible_count": manifest["eligible_count"],
        "scanned_count": manifest["scanned_count"],
        "shortfall": manifest["shortfall"],
        "manifest_digest": manifest["manifest_digest"],
    }, indent=2))


if __name__ == "__main__":
    main()
