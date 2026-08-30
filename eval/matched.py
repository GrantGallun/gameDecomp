"""Which functions are already byte-exact. One definition, used everywhere.

A match can be recorded in two places -- a logged attempt scoring 100, or a
verified file in matched_recovered/ produced by a harness that did not log --
and eval/status.py already takes the union for exactly that reason.

Every other tool asked only the database, and the omission is not cosmetic.
`updateTimeTrialRecordDeltaPopupSlideIn` is matched on disk while its logged
attempts top out at 99.999, so a "near miss" query built on max(score) < 100
surfaced it as the closest unsolved function. It was solved. Repairing it to
byte-exact looked like a new match and was a re-derivation of an existing one,
which is the same mistake this project made when 33 was reported as 34.

Import this rather than writing the query again.
"""

from __future__ import annotations

import glob
import os
import sqlite3


def matched_in_db(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr where a.score >= 100")}


def matched_on_disk(root: str = "matched_recovered") -> set[str]:
    return {os.path.basename(p)[:-2] for p in glob.glob(f"{root}/*.c")}


def already_matched(conn: sqlite3.Connection,
                    root: str = "matched_recovered") -> set[str]:
    """Union of both records -- the only honest answer to "is this done"."""
    return matched_in_db(conn) | matched_on_disk(root)
