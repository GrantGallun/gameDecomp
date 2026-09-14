"""Freeze or audit a never-touched evaluation split without reading source.

Selection uses only knowledge-base metadata.  A function is ineligible when it
has any attempt receipt, appears in an earlier evaluation set or result
artifact, or has a recovered-source artifact.  Result artifacts matter because
early harnesses sometimes failed to write SQLite receipts. The legacy
``functions.state`` field is deliberately ignored: imported databases can mark
the entire corpus ``matched`` even though the append-only attempt ledger shows
most functions were never touched. Unlike ``eval.sets``, this tool does not
bootstrap candidates or inspect target C for feasibility while assigning
held-out names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import time
from pathlib import Path


TIERS = (
    ("tiny", 8, 20),
    ("small", 20, 50),
    ("medium", 50, 120),
    ("large", 120, 300),
    ("huge", 300, 100000),
)
EXCLUDE_TU = ("%ultra%", "%libmus%", "%libc%", "%audio%")


def _json_digest(value: dict) -> str:
    material = dict(value)
    material.pop("manifest_digest", None)
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _set_names(sets_dir: Path, *, ignore: Path | None = None) -> set[str]:
    names: set[str] = set()
    if not sets_dir.is_dir():
        return names
    ignored = ignore.resolve() if ignore else None
    for path in sets_dir.glob("*.json"):
        if ignored is not None and path.resolve() == ignored:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for split in ("dev", "heldout"):
            for row in value.get(split, []):
                if isinstance(row, dict) and isinstance(row.get("function"), str):
                    names.add(row["function"])
    return names


def _recovered_names(project_root: Path) -> set[str]:
    recovered = project_root / "matched_recovered"
    return {path.stem for path in recovered.glob("*.c")} \
        if recovered.is_dir() else set()


def _names_in_value(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "function" and isinstance(child, str):
                names.add(child)
            else:
                names.update(_names_in_value(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_names_in_value(child))
    return names


def _result_names(results_dir: Path) -> set[str]:
    """Function identities preserved by historical JSON/JSONL receipts."""
    names: set[str] = set()
    if not results_dir.is_dir():
        return names
    for path in results_dir.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix == ".jsonl":
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        names.update(_names_in_value(json.loads(line)))
            else:
                names.update(_names_in_value(json.loads(
                    path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A partial run file is not proof about identities it does not
            # expose. Other durable receipts still participate in exclusion.
            continue
    return names


def _attempted_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT DISTINCT f.name FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr")}


def _pool(conn: sqlite3.Connection, low: int, high: int,
          leaf: int, excluded: set[str]) -> list[str]:
    tu_exclusion = " ".join(
        f"AND t.name NOT LIKE '{pattern}'" for pattern in EXCLUDE_TU)
    rows = conn.execute(
        "SELECT f.name FROM functions f JOIN tus t ON t.id=f.tu_id "
        "WHERE t.name LIKE '%src/%' "
        "AND f.insn_count>=? AND f.insn_count<? AND f.is_leaf=? "
        f"{tu_exclusion} ORDER BY f.name", (low, high, leaf)).fetchall()
    return [row[0] for row in rows if row[0] not in excluded]


def freeze(conn: sqlite3.Connection, *, project_root: Path, sets_dir: Path,
           out: Path, per_stratum: int = 3,
           seed: int = 20260901) -> dict:
    """Create balanced fresh DEV/heldout names from metadata only."""
    if per_stratum <= 0:
        raise ValueError("per_stratum must be positive")
    attempted = _attempted_names(conn)
    prior_sets = _set_names(sets_dir, ignore=out)
    recovered = _recovered_names(project_root)
    prior_results = _result_names(project_root / "eval" / "results")
    excluded = attempted | prior_sets | recovered | prior_results
    rng = random.Random(seed)
    dev: list[dict] = []
    heldout: list[dict] = []
    shortfalls: list[dict] = []

    for tier, low, high in TIERS:
        for leaf in (1, 0):
            pool = _pool(conn, low, high, leaf, excluded)
            rng.shuffle(pool)
            wanted = per_stratum * 2
            chosen = pool[:wanted]
            if len(chosen) < wanted:
                shortfalls.append({
                    "tier": tier, "leaf": bool(leaf),
                    "wanted": wanted, "available": len(chosen),
                })
            for index, name in enumerate(chosen):
                row = {"function": name, "tier": tier, "leaf": bool(leaf)}
                (dev if index < per_stratum else heldout).append(row)

    manifest = {
        "schema_version": 1,
        "kind": "never-touched-metadata-only-eval-set",
        "created_at": int(time.time()),
        "seed": seed,
        "per_stratum": per_stratum,
        "policy": {
            "selection_inputs": "function/TU metadata only",
            "target_source_inspected_during_assignment": False,
            "excluded": [
                "any attempt receipt", "any earlier eval-set membership",
                "any historical result-artifact membership",
                "recovered-source artifact",
                "published/runtime/library translation units",
            ],
            "heldout_rule": (
                "never bootstrap, generate, repair, or inspect per-function "
                "residuals before the single preregistered evaluation"),
        },
        "exclusion_counts": {
            "attempted": len(attempted),
            "prior_set_members": len(prior_sets),
            "prior_result_members": len(prior_results),
            "recovered_sources": len(recovered),
            "combined": len(excluded),
        },
        "dev": dev,
        "heldout": heldout,
        "shortfalls": shortfalls,
    }
    manifest["manifest_digest"] = _json_digest(manifest)
    return manifest


def audit(conn: sqlite3.Connection, manifest: dict, *,
          project_root: Path) -> dict:
    """Report heldout contamination without mutating the append-only ledger."""
    heldout = {row["function"] for row in manifest.get("heldout", [])}
    attempted = sorted(heldout & _attempted_names(conn))
    recovered = sorted(heldout & _recovered_names(project_root))
    result_artifacts = sorted(
        heldout & _result_names(project_root / "eval" / "results"))
    digest_valid = _json_digest(manifest) == manifest.get("manifest_digest")
    return {
        "clean": (digest_valid and not attempted and not recovered
                  and not result_artifacts),
        "manifest_digest_valid": digest_valid,
        "heldout_functions": len(heldout),
        "attempted_heldout": attempted,
        "recovered_heldout": recovered,
        "result_artifact_heldout": result_artifacts,
    }


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--db", type=Path, required=True)
    freeze_parser.add_argument("--project-root", type=Path, default=Path("."))
    freeze_parser.add_argument("--sets-dir", type=Path,
                               default=Path("eval/sets"))
    freeze_parser.add_argument("--out", type=Path, required=True)
    freeze_parser.add_argument("--per-stratum", type=int, default=3)
    freeze_parser.add_argument("--seed", type=int, default=20260901)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--db", type=Path, required=True)
    audit_parser.add_argument("--project-root", type=Path, default=Path("."))
    audit_parser.add_argument("--set", dest="set_path", type=Path,
                              required=True)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db.expanduser(), timeout=120)
    if args.command == "freeze":
        out = args.out.expanduser().resolve()
        manifest = freeze(
            conn, project_root=args.project_root.expanduser().resolve(),
            sets_dir=args.sets_dir.expanduser().resolve(), out=out,
            per_stratum=args.per_stratum, seed=args.seed)
        _atomic_json(out, manifest)
        print(json.dumps({
            "manifest_digest": manifest["manifest_digest"],
            "dev": len(manifest["dev"]),
            "heldout": len(manifest["heldout"]),
            "shortfalls": manifest["shortfalls"],
        }, indent=2))
        return

    manifest = json.loads(
        args.set_path.expanduser().read_text(encoding="utf-8"))
    result = audit(
        conn, manifest, project_root=args.project_root.expanduser().resolve())
    print(json.dumps(result, indent=2))
    if not result["clean"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
