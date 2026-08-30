"""Schema helpers for attempt receipts.

`score` is a similarity metric.  Only the verifier's `exact` verdict proves a
byte match, so old rows are migrated to NULL (unknown), never guessed from
their score.
"""

from __future__ import annotations

import sqlite3


def ensure_exact_receipt(conn: sqlite3.Connection) -> None:
    """Add the exact-verdict receipt to databases created by older revisions."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    if "exact" not in cols:
        conn.execute("ALTER TABLE attempts ADD COLUMN exact INTEGER")
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
