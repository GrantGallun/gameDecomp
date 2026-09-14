"""Recover bounded, provenance-bound model patches without another generation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3


@dataclass(frozen=True)
class SavedProposal:
    proposal_id: int
    parent_attempt_id: int
    source: str
    response: str


def load(conn: sqlite3.Connection, function: str, source: str, *,
         limit: int = 32, cutoff: int | None = None) -> tuple[SavedProposal, ...]:
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    if not {"model_proposals", "attempts", "functions"} <= tables:
        return ()
    if cutoff is None:
        cutoff = conn.execute("select coalesce(max(id),0) from model_proposals").fetchone()[0]
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    rows = conn.execute(
        "select p.id,p.parent_attempt_id,a.source_code,p.raw_response,a.source_sha256 "
        "from model_proposals p join attempts a on a.id=p.parent_attempt_id "
        "join functions f on f.addr=a.func_addr "
        "where f.name=? and p.id<=? and a.source_code is not null "
        "and p.kind not like '%diagnosis%' and p.kind not like '%handoff%' "
        "order by (a.source_sha256=?) desc,p.id desc limit ?",
        (function, cutoff, source_hash, max(0, limit)))
    result = []
    for ident, parent, old_source, response, stored_hash in rows:
        if stored_hash and hashlib.sha256(old_source.encode()).hexdigest() != stored_hash:
            continue
        result.append(SavedProposal(ident, parent, old_source, response))
    return tuple(result)
