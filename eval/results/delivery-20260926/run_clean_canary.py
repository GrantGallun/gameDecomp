"""Run two clean binary-type canaries through normal intake in private WSL state.

This does not import experimental winning C. The source database is opened
read-only and backed up consistently to a private database before any scoring.
Run with the target repository's WSL virtualenv after binary_type_only intake
is available. Every compiler attempt is retained in the private DB.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval import completion_campaign  # noqa: E402
from eval.campaign_workers import isolate  # noqa: E402


CANARIES = ("getRaceCourseNextSurface", "packFixedTransformMatrix")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(source: Path, target: Path) -> dict:
    if target.exists():
        raise FileExistsError(f"private database already exists: {target}")
    # The research DB carries large historical source/attempt bodies. Intake
    # needs its schema, function/TU metadata and immutable binary evidence;
    # private attempts begin empty, so reruns stay small and cannot touch it.
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as upstream:
        upstream.execute("BEGIN")
        before = {row[0] for row in upstream.execute(
            "SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        with sqlite3.connect(target) as private:
            private.executescript((ROOT / "kb/schema.sql").read_text())
            private.execute("PRAGMA foreign_keys=OFF")
            counts = {}
            for table in ("extraction", "tus", "functions", "evidence"):
                columns = [row[1] for row in upstream.execute(f"PRAGMA table_info({table})")]
                marks = ",".join("?" for _ in columns)
                cursor = upstream.execute(f"SELECT * FROM {table}")
                count = 0
                while batch := cursor.fetchmany(1000):
                    private.executemany(f"INSERT INTO {table} VALUES ({marks})", batch)
                    count += len(batch)
                counts[table] = count
            private.commit()
            check = private.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise ValueError(f"private database quick_check: {check}")
    return {"source": str(source), "snapshot_sha256": _sha256(target),
            "private": str(target), "initial_exact_count": len(before),
            "copied_rows": counts, "initial_exact_addrs": before}


def _attempts(db: Path, function: str, after_id: int) -> list[dict]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT a.id, a.parent_attempt_id, a.strategy, a.source_sha256, "
            "a.compiled, a.score, a.exact, a.model, a.sampling, a.compiler_stderr "
            "FROM attempts a JOIN functions f ON f.addr=a.func_addr "
            "WHERE f.name=? AND a.id>? ORDER BY a.id", (function, after_id)).fetchall()
    result = []
    for row in rows:
        sampling = json.loads(row["sampling"] or "{}")
        result.append({"attempt_id": row["id"],
                       "parent_attempt_id": row["parent_attempt_id"],
                       "strategy": row["strategy"],
                       "source_sha256": row["source_sha256"],
                       "compiled": bool(row["compiled"]),
                       "score": row["score"], "exact": bool(row["exact"]),
                       "model": row["model"],
                       "frontend": sampling.get("frontend"),
                       "verification": sampling.get("verification"),
                       "provenance_hashes": _digests(sampling),
                       "compiler_stderr_tail": (row["compiler_stderr"] or "")[-1000:]})
    return result


def _digests(value):
    """Expose evidence/context hashes from intake reports without copying source."""
    if isinstance(value, dict):
        found = {key: item for key, item in value.items()
                 if isinstance(item, str) and ("sha256" in key or "digest" in key)}
        for key, item in value.items():
            child = _digests(item)
            if child:
                found[key] = child
        return found
    if isinstance(value, list):
        return [item for entry in value if (item := _digests(entry))]
    return {}


def run(repo: Path, source_db: Path, out: Path) -> dict:
    if "binary_type_only" not in inspect.signature(completion_campaign._intake).parameters:
        raise RuntimeError("normal intake has no binary_type_only route yet")
    if out.exists():
        raise FileExistsError(f"refusing to mix or overwrite canary results: {out}")
    out.mkdir(parents=True)
    snapshot = _snapshot(source_db, out / "private.sqlite")
    before = snapshot.pop("initial_exact_addrs")
    receipt = {"kind": "clean-binary-type-normal-intake-canary",
               "repo": str(repo), "db": snapshot, "functions": [],
               "source_independent_only": True}
    path = out / "receipt.json"
    try:
        for function in CANARIES:
            iso = isolate(repo, out / "isolation" / function, function)
            with sqlite3.connect(out / "private.sqlite") as conn:
                prior = conn.execute("SELECT COALESCE(MAX(id), 0) FROM attempts").fetchone()[0]
            row = {"function": function, "isolation": str(iso), "status": "running"}
            receipt["functions"].append(row)
            path.write_text(json.dumps(receipt, indent=2))
            try:
                result = completion_campaign._intake(
                    repo=iso, db=out / "private.sqlite", function=function,
                    node={}, out=out / f"{function}.intake.json",
                    binary_type_only=True)
                attempts = _attempts(out / "private.sqlite", function, prior)
                unexpected = [a["strategy"] for a in attempts
                              if not (a["strategy"] or "").startswith("campaign-intake:binary-types:")]
                if unexpected or any(a["model"] for a in attempts):
                    raise RuntimeError("non-binary or model attempt in clean intake: " + repr(unexpected))
                row.update(status=result.get("status"), exact=result.get("exact"),
                           attempt_id=result.get("attempt_id"),
                           source_sha256=result.get("source_sha256"),
                           score=result.get("score"),
                           verification=result.get("verification"),
                           context_hashes=_digests(result.get("context") or result.get("blocker")),
                           attempts=attempts, failure=result.get("blocker"))
                if not attempts and row["status"] == "evaluated":
                    raise RuntimeError("intake returned evaluated without logged attempts")
            except Exception as exc:
                row.update(status="error", error=f"{type(exc).__name__}: {exc}",
                           traceback=traceback.format_exc()[-3000:],
                           attempts=_attempts(out / "private.sqlite", function, prior))
                receipt["status"] = "error"
                break
            finally:
                path.write_text(json.dumps(receipt, indent=2))
        with sqlite3.connect(out / "private.sqlite") as conn:
            after = {r[0] for r in conn.execute(
                "SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as upstream:
            source_after = {r[0] for r in upstream.execute(
                "SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
        receipt["db"]["new_private_exact_count"] = len(after)
        receipt["db"]["final_exact_count_with_source"] = len(source_after | after)
        receipt["db"]["exact_lost"] = sorted(before - source_after)
        if any(not row.get("attempts") for row in receipt["functions"]):
            receipt["status"] = "incomplete"
        if receipt["db"]["exact_lost"]:
            receipt["status"] = "error"
        else:
            receipt.setdefault("status", "completed")
    finally:
        path.write_text(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.repo.expanduser().resolve(), args.db.expanduser().resolve(),
                  args.out.expanduser().resolve())
    print(json.dumps({"status": receipt["status"], "receipt": str(args.out / "receipt.json"),
                      "functions": [{"function": row["function"], "status": row["status"],
                                     "exact": row.get("exact"), "attempt_id": row.get("attempt_id")}
                                    for row in receipt["functions"]]}, indent=2))
    return 0 if receipt["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
