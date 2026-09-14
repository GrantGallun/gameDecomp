"""What a knowledge base has been fed, stamped into the database itself.

The ceiling experiment deliberately imports SBK1's finished headers -- the same
headers `eval/ground_truth.py` uses as its oracle -- to measure an upper bound
on what perfect type knowledge is worth. That is teaching to the test, on
purpose, and the number it produces is meaningful only while everyone reading
it knows that.

A note in a commit message does not survive contact with 2am. So the taint
lives in the database: any KB that has been fed oracle data carries a row
saying so, `eval/experiment.py` folds it into the run fingerprint, and
`eval/run_set.py` refuses to touch heldout with a tainted KB. A tainted result
file therefore cannot silently accumulate into a clean one -- the fingerprints
differ, and the existing mismatch refusal does the rest.

A database with no `kb_provenance` table is clean. Every KB built before this
module existed stays valid and unchanged.
"""

from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_provenance (
    key        TEXT PRIMARY KEY,
    detail     TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""

# The one taint that exists today. Others get their own key, never a bool.
ORACLE_TYPES = "oracle_types"


def stamp(conn: sqlite3.Connection, key: str, detail: str) -> None:
    """Record that this KB has been fed `key`. Idempotent, never unstamped.

    There is deliberately no `unstamp`. A KB that has seen oracle data cannot
    be cleaned by deleting rows -- anything already derived from them stays
    derived from them -- so the honest move is to throw the file away and
    rebuild, not to clear a flag.
    """
    conn.execute(SCHEMA)
    conn.execute(
        "INSERT INTO kb_provenance(key, detail, created_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET detail=excluded.detail",
        (key, detail, int(time.time())))
    conn.commit()


def taints(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every taint on this KB as (key, detail), oldest first. [] means clean."""
    try:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT key, detail FROM kb_provenance ORDER BY created_at, key")]
    except sqlite3.OperationalError:
        return []            # no table: a KB predating this module, i.e. clean


def is_clean(conn: sqlite3.Connection) -> bool:
    return not taints(conn)


def digest(conn: sqlite3.Connection) -> str:
    """Short stable string for the run fingerprint. '' when clean."""
    return ",".join(k for k, _ in taints(conn))


def banner(conn: sqlite3.Connection) -> str:
    """Loud human-readable warning, or '' when clean."""
    marks = taints(conn)
    if not marks:
        return ""
    lines = ["!" * 72,
             "TAINTED KNOWLEDGE BASE -- results from this KB are an upper",
             "bound, not a capability measurement. Do not quote as a headline.",
             ""]
    lines += [f"  {k}: {d}" for k, d in marks]
    lines.append("!" * 72)
    return "\n".join(lines)
