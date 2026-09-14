"""Truth maintenance: citation-enforced writes, retraction, and the ratchet.

`kb/schema.sql` has claimed twice, since the schema was written, that invariants
3, 4 and 5 are "enforced in kb/tms.py". That file did not exist. Until now the
invariants were prose in DESIGN.md and conventions in the code -- which is to
say, not enforced at all. An external review caught it.

What this module makes true rather than aspirational:

    Invariant 3  Evidence is mechanical and immutable; only the inference tier
                 is writable. `assert_inference` is the ONLY sanctioned way to
                 add a claim, and there is no evidence-writing path here at all.

    Invariant 4  Every inference cites evidence. A write with no supporting
                 evidence row is rejected at this boundary, not warned about.

    Invariant 5  Unknown is representable. A claim whose value is unknown is
                 simply not asserted; nothing here invents one.

    The ratchet  Global match count never decreases. Every mutation runs inside
                 a SAVEPOINT that rolls back if it does.

The design's whole safety argument rests on retraction being cheap and its
consequences measurable. That is only true if `func_deps` is populated
faithfully: it is the record of which claims a match relied on, and without it
the system cannot detect its own poisoning.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass


class CitationRequired(Exception):
    """Raised when an inference is asserted without supporting evidence."""


class EvidenceIsImmutable(Exception):
    """Raised on any attempt to modify the evidence tier."""


class RatchetViolation(Exception):
    """Raised when a mutation would reduce the global match count."""


@dataclass
class Retraction:
    root: int
    cascaded: list          # inference ids retracted transitively
    demoted: list           # func_addrs knocked out of 'matched'


# --------------------------------------------------------------- assertions


def assert_inference(conn: sqlite3.Connection, kind: str, subject: str,
                     value, evidence_ids: list[int], origin: str = "model",
                     confidence: float | None = None,
                     depends_on: list[int] | None = None) -> int:
    """Add one claim. Rejects it if nothing in the binary supports it.

    Invariant 4 is enforced here and nowhere else, so this must stay the only
    write path into the inference tier. `origin='human'` is the single
    exemption: claims imported from the reference repo's own headers and symbol
    table carry their provenance in the repo itself, not in an evidence row.
    """
    if origin != "human" and not evidence_ids:
        raise CitationRequired(
            f"inference {kind}:{subject} has no supporting evidence. "
            "Every inference must cite evidence -- see DESIGN.md invariant 4.")

    # Cited evidence must actually exist. A dangling citation is no citation.
    if evidence_ids:
        placeholders = ",".join("?" * len(evidence_ids))
        found = {r[0] for r in conn.execute(
            f"SELECT id FROM evidence WHERE id IN ({placeholders})", evidence_ids)}
        missing = set(evidence_ids) - found
        if missing:
            raise CitationRequired(
                f"inference {kind}:{subject} cites evidence rows that do not "
                f"exist: {sorted(missing)}")

    cur = conn.execute(
        "INSERT INTO inference (kind, subject, value, confidence, origin,"
        " status, created_at) VALUES (?,?,?,?,?,'active',?)",
        (kind, subject, json.dumps(value) if not isinstance(value, str) else value,
         confidence, origin, int(time.time())))
    inf_id = cur.lastrowid

    for ev in evidence_ids:
        conn.execute("INSERT OR IGNORE INTO inference_support VALUES (?,?)",
                     (inf_id, ev))
    for dep in (depends_on or []):
        conn.execute("INSERT OR IGNORE INTO inference_depends VALUES (?,?)",
                     (inf_id, dep))
    return inf_id


def record_dependency(conn: sqlite3.Connection, func_addr: int,
                      inference_ids: list[int]) -> None:
    """Record which claims a matched function relied on.

    THE critical table. Retraction walks it to find what must be rebuilt, so an
    unrecorded dependency is a fact that can be poisoned without detection.
    """
    for inf_id in inference_ids:
        conn.execute("INSERT OR IGNORE INTO func_deps VALUES (?,?)",
                     (func_addr, inf_id))


def guard_evidence_immutable(conn: sqlite3.Connection) -> None:
    """Install triggers so the evidence tier cannot be edited or deleted.

    Invariant 3 as a database property rather than a habit. Inserts are still
    allowed -- the miner has to populate it -- but nothing may rewrite an
    observation after the fact.
    """
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS evidence_no_update
        BEFORE UPDATE ON evidence BEGIN
            SELECT RAISE(ABORT, 'evidence is immutable (DESIGN.md invariant 3)');
        END;
        CREATE TRIGGER IF NOT EXISTS evidence_no_delete
        BEFORE DELETE ON evidence BEGIN
            SELECT RAISE(ABORT, 'evidence is immutable (DESIGN.md invariant 3)');
        END;
    """)


# --------------------------------------------------------------- retraction


def dependents(conn: sqlite3.Connection, inf_id: int) -> list[int]:
    """Transitive closure of inferences built on this one."""
    seen, frontier = set(), [inf_id]
    while frontier:
        current = frontier.pop()
        rows = conn.execute(
            "SELECT inference_id FROM inference_depends WHERE depends_on_id = ?",
            (current,)).fetchall()
        for (child,) in rows:
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return sorted(seen)


def affected_functions(conn: sqlite3.Connection, inf_ids: list[int]) -> list[int]:
    if not inf_ids:
        return []
    placeholders = ",".join("?" * len(inf_ids))
    return sorted({r[0] for r in conn.execute(
        f"SELECT func_addr FROM func_deps WHERE inference_id IN ({placeholders})",
        inf_ids)})


def retract(conn: sqlite3.Connection, inf_id: int, reason: str) -> Retraction:
    """Retract a claim, everything built on it, and every match that used it.

    Retracted rather than deleted: the bank of what was believed and why is the
    debugging record. A deleted claim can be re-derived tomorrow by the same
    faulty reasoning.
    """
    cascade = dependents(conn, inf_id)
    all_ids = [inf_id] + cascade
    funcs = affected_functions(conn, all_ids)

    now = int(time.time())
    placeholders = ",".join("?" * len(all_ids))
    conn.execute(
        f"UPDATE inference SET status='retracted', retracted_at=?,"
        f" retraction_reason=? WHERE id IN ({placeholders})",
        [now, reason] + all_ids)

    # A match that leaned on a retracted claim is no longer trustworthy.
    for addr in funcs:
        conn.execute(
            "UPDATE functions SET state='attempted' WHERE addr=? AND state='matched'",
            (addr,))

    return Retraction(root=inf_id, cascaded=cascade, demoted=funcs)


# ------------------------------------------------------------------ ratchet


def matched_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM functions WHERE state='matched'").fetchone()[0]


def ratchet(conn: sqlite3.Connection, mutate, rebuild=None,
            label: str = "") -> tuple[bool, int, int]:
    """Run a mutation inside a savepoint; roll it back if matches decrease.

    `mutate(conn)` applies the change. `rebuild(conn, func_addrs)` re-verifies
    functions the change touched and updates their state -- inject the oracle
    here; without it the count cannot fall and the ratchet is decorative.

    Returns (committed, before, after). This is what makes an unattended
    overnight run safe: the worst case is a rolled-back savepoint, never a
    silently degraded knowledge base.
    """
    before = matched_count(conn)
    conn.execute("SAVEPOINT ratchet")
    try:
        touched = mutate(conn) or []
        if rebuild is not None:
            rebuild(conn, touched)
        after = matched_count(conn)
        if after < before:
            conn.execute("ROLLBACK TO ratchet")
            conn.execute("RELEASE ratchet")
            return False, before, after
        conn.execute("RELEASE ratchet")
        conn.commit()
        return True, before, after
    except Exception:
        conn.execute("ROLLBACK TO ratchet")
        conn.execute("RELEASE ratchet")
        raise
