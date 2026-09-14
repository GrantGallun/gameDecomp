"""Schema and lineage helpers for attempt receipts.

`score` is a similarity metric.  Only the verifier's `exact` verdict proves a
byte match, so old rows are migrated to NULL (unknown), never guessed from
their score. Old databases are also upgraded with explicit run and parent-edge
lineage: row order is not a refinement trajectory once sampling branches or a
later iteration anchors on the best earlier attempt.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import time


LINEAGE_COLUMNS = {
    "run_id": "TEXT",
    "parent_attempt_id": "INTEGER",
    "source_sha256": "TEXT",
    "prompt_sha256": "TEXT",
    "raw_response": "TEXT",
    "extract_status": "TEXT",
    "done_reason": "TEXT",
}


def ensure_exact_receipt(conn: sqlite3.Connection) -> None:
    """Add the exact-verdict receipt to databases created by older revisions."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    if "exact" not in cols:
        conn.execute("ALTER TABLE attempts ADD COLUMN exact INTEGER")
        conn.commit()


def ensure_lineage_schema(conn: sqlite3.Connection) -> None:
    """Idempotently migrate an existing attempts table for trajectory use."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    if "exact" not in cols:
        conn.execute("ALTER TABLE attempts ADD COLUMN exact INTEGER")
        cols.add("exact")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('attempt_runs','attempt_edges','model_proposals')")}
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
        "('att_run','att_parent','ae_child','mp_parent')")}
    if (LINEAGE_COLUMNS.keys() <= cols
            and tables == {"attempt_runs", "attempt_edges", "model_proposals"}
            and indexes == {"att_run", "att_parent", "ae_child", "mp_parent"}):
        return
    for name, declaration in LINEAGE_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE attempts ADD COLUMN {name} {declaration}")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS attempt_runs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            config TEXT NOT NULL DEFAULT '{}',
            started_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempt_edges (
            parent_attempt_id INTEGER NOT NULL REFERENCES attempts(id),
            child_attempt_id INTEGER NOT NULL REFERENCES attempts(id)
                ON DELETE CASCADE,
            relation TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            PRIMARY KEY (parent_attempt_id, child_attempt_id)
        );
        CREATE TABLE IF NOT EXISTS model_proposals (
            id INTEGER PRIMARY KEY,
            run_id TEXT REFERENCES attempt_runs(id),
            parent_attempt_id INTEGER REFERENCES attempts(id),
            child_attempt_id INTEGER REFERENCES attempts(id),
            prompt_context TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            raw_response TEXT NOT NULL,
            status TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            hypothesis TEXT NOT NULL DEFAULT '',
            edits TEXT NOT NULL DEFAULT '[]',
            model TEXT NOT NULL DEFAULT '',
            sampling TEXT NOT NULL DEFAULT '{}',
            wall_ms INTEGER NOT NULL DEFAULT 0,
            token_cost INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS att_run
            ON attempts(run_id, iteration);
        CREATE INDEX IF NOT EXISTS att_parent
            ON attempts(parent_attempt_id);
        CREATE INDEX IF NOT EXISTS ae_child
            ON attempt_edges(child_attempt_id);
        CREATE INDEX IF NOT EXISTS mp_parent
            ON model_proposals(parent_attempt_id, created_at);
    """)
    conn.commit()


def start_run(conn: sqlite3.Connection, run_id: str, *, kind: str = "",
              model: str = "", config: dict | None = None,
              started_at: int | None = None, ensure_schema: bool = True) -> None:
    """Persist invocation metadata once; repeated attempt writes are harmless."""
    if not run_id:
        return
    if ensure_schema:
        ensure_lineage_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO attempt_runs "
        "(id, kind, model, config, started_at) VALUES (?,?,?,?,?)",
        (run_id, kind, model, json.dumps(config or {}, sort_keys=True),
         int(time.time()) if started_at is None else int(started_at)),
    )
    conn.commit()


def record_model_proposal(conn: sqlite3.Connection, *, run_id: str,
                          parent_attempt_id: int | None, prompt: str,
                          raw_response: str, status: str, model: str = "",
                          kind: str = "", hypothesis: str = "",
                          edits: list | None = None,
                          sampling: dict | None = None, wall_ms: int = 0,
                          token_cost: int = 0) -> int:
    """Persist one model call whether or not it produced a scorable child."""
    ensure_lineage_schema(conn)
    cur = conn.execute(
        "INSERT INTO model_proposals (run_id, parent_attempt_id, prompt_context,"
        " prompt_sha256, raw_response, status, kind, hypothesis, edits, model,"
        " sampling, wall_ms, token_cost, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id or None, parent_attempt_id, prompt,
         hashlib.sha256(prompt.encode("utf-8")).hexdigest(), raw_response,
         status, kind, hypothesis, json.dumps(edits or [], sort_keys=True),
         model, json.dumps(sampling or {}, sort_keys=True), int(wall_ms),
         max(0, int(token_cost)), int(time.time())))
    conn.commit()
    return int(cur.lastrowid)


def link_model_proposal(conn: sqlite3.Connection, proposal_id: int,
                        child_attempt_id: int) -> None:
    conn.execute("UPDATE model_proposals SET child_attempt_id=? WHERE id=?",
                 (child_attempt_id, proposal_id))
    conn.commit()


def exact_functions(conn: sqlite3.Connection) -> set[str]:
    """Functions with an explicitly persisted positive verifier verdict."""
    ensure_exact_receipt(conn)
    return {row[0] for row in conn.execute(
        "SELECT DISTINCT f.name FROM attempts a "
        "JOIN functions f ON f.addr = a.func_addr WHERE a.exact = 1")}


def unknown_exact_count(conn: sqlite3.Connection) -> int:
    """Historical compiled rows created before the verifier verdict was stored."""
    ensure_exact_receipt(conn)
    return conn.execute(
        "SELECT count(*) FROM attempts WHERE compiled = 1 AND exact IS NULL"
    ).fetchone()[0]
