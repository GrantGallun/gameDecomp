"""The ceiling KB must never be mistakable for a clean one.

The ceiling experiment deliberately feeds the KB the reference decomp's own
headers -- the same ones `eval/ground_truth.py` uses as its oracle -- to find
out whether perfect type knowledge is worth anything. That number is honest
only while it is impossible to quote it as a capability measurement, so these
tests pin the four things that keep the two apart:

  - a clean KB stays byte-identically clean (no table, no taint, no prompt
    change), so every number recorded before this path existed still stands
  - a tainted KB says so, permanently, in the database rather than in a commit
    message
  - the run fingerprint carries the taint, so a ceiling run cannot resume into
    a clean results file -- experiment.check_or_claim already refuses a
    mismatch, and this makes the mismatch happen
  - the importer refuses the primary KB and refuses to run unacknowledged

Run:  python3 -m pytest tests/ -q
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval import experiment                           # noqa: E402
from kb import provenance                             # noqa: E402
from solver import context                            # noqa: E402

ROOT = Path(__file__).parent.parent


def _kb() -> sqlite3.Connection:
    """A KB with just enough schema for the inference path."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY, kind TEXT, addr INTEGER, func_addr INTEGER,
            op TEXT, base TEXT, offset INTEGER, width INTEGER, signed INTEGER,
            class TEXT, access TEXT, is_load INTEGER, target_addr INTEGER);
        CREATE TABLE inference (
            id INTEGER PRIMARY KEY, kind TEXT, subject TEXT, value TEXT,
            confidence REAL, origin TEXT, status TEXT DEFAULT 'active',
            created_at INTEGER);
    """)
    conn.execute("INSERT INTO functions VALUES (0x80001000, 'doThing')")
    conn.execute(
        "INSERT INTO evidence (kind, addr, func_addr, op, base, offset, width,"
        " signed, class, access, is_load) VALUES"
        " ('mem_access', 1, 0x80001000, 'lw', 'param0', 0x2fc, 4, 1, 'int',"
        " 'full', 1)")
    return conn


def _add_types(conn: sqlite3.Connection) -> None:
    """What the ceiling import would write."""
    conn.execute(
        "INSERT INTO inference (kind, subject, value, confidence, origin,"
        " status, created_at) VALUES ('signature', 'func:doThing',"
        " '{\"params\": [\"Thing\"]}', 1.0, 'human', 'active', 0)")
    for off, decl in [(0x2f8, "unk2F8"), (0x2fc, "stateFlags"), (0x300, "mode")]:
        conn.execute(
            "INSERT INTO inference (kind, subject, value, confidence, origin,"
            " status, created_at) VALUES ('field', ?, ?, 1.0, 'human',"
            " 'active', 0)",
            (f"struct:Thing@{off:#x}",
             '{"type": "s32", "name": "%s", "elem_count": 1,'
             ' "is_pointer": false}' % decl))


# ------------------------------------------------------------------ clean KB

def test_kb_with_no_provenance_table_is_clean():
    # Every KB built before kb/provenance.py existed has no such table. It
    # must read as clean rather than raising, or old databases break.
    conn = _kb()
    assert provenance.taints(conn) == []
    assert provenance.is_clean(conn)
    assert provenance.digest(conn) == ""
    assert provenance.banner(conn) == ""


def test_clean_kb_prompt_is_unchanged_by_the_inference_path():
    # The inference path is inert until something populates the tier. This is
    # what protects every number already reported from the clean KB.
    conn = _kb()
    block = context.for_function(conn, "doThing")
    assert "OBSERVED MEMORY ACCESSES" in block
    assert "DECLARED TYPES" not in block


# ---------------------------------------------------------------- tainted KB

def test_stamp_is_visible_and_loud():
    conn = _kb()
    provenance.stamp(conn, provenance.ORACLE_TYPES, "sbk1 headers")
    assert provenance.digest(conn) == provenance.ORACLE_TYPES
    assert not provenance.is_clean(conn)
    assert "TAINTED" in provenance.banner(conn)
    assert "sbk1 headers" in provenance.banner(conn)


def test_stamp_is_idempotent():
    conn = _kb()
    provenance.stamp(conn, provenance.ORACLE_TYPES, "first")
    provenance.stamp(conn, provenance.ORACLE_TYPES, "second")
    assert provenance.taints(conn) == [(provenance.ORACLE_TYPES, "second")]


def test_imported_types_reach_the_prompt():
    conn = _kb()
    _add_types(conn)
    block = context.for_function(conn, "doThing")
    assert "DECLARED TYPES" in block
    assert "stateFlags" in block
    # Labelled as a claim, never as an observation. Invariant 3.
    assert "claims, not binary facts" in block


def test_layout_keeps_the_field_the_function_actually_touches():
    # The bug this encodes: truncating a 193-field struct at the first 24
    # dropped 0x2fc, the only field the function reads, so the ceiling was
    # measuring a struct that omitted the answer.
    conn = _kb()
    _add_types(conn)
    for off in range(0, 0x2f8, 4):
        conn.execute(
            "INSERT INTO inference (kind, subject, value, confidence, origin,"
            " status, created_at) VALUES ('field', ?, ?, 1.0, 'human',"
            " 'active', 0)",
            (f"struct:Thing@{off:#x}",
             '{"type": "s32", "name": "pad%x", "elem_count": 1,'
             ' "is_pointer": false}' % off))
    block = context.for_function(conn, "doThing")
    assert "stateFlags" in block
    assert "fields omitted" in block


# --------------------------------------------------------------- fingerprint

def test_taint_changes_the_run_fingerprint():
    # Without this a ceiling run resumes into a clean results file and the two
    # blend into one number describing no system that ever existed.
    args = (ROOT / "eval" / "sets" / "sbk1_v3.json", "dev", "m", 3, 0.7,
            "low", True, True, 90)
    clean = experiment.build(*args)
    tainted = experiment.build(*args, kb_taint=provenance.ORACLE_TYPES)
    assert clean.digest() != tainted.digest()


def test_old_fingerprint_sidecars_still_load():
    # kb_taint defaults to "", so sidecars written before it existed must
    # reconstruct and compare equal rather than invalidating stored results.
    old = {"git_rev": "abc", "git_dirty": False, "set_hash": "h",
           "split": "dev", "model": "m", "samples": 3, "temperature": 0.7,
           "think": "low", "pipeline": True, "siblings": True,
           "permute_seconds": 90, "source_hash": "s"}
    fp = experiment.Fingerprint(**old)
    assert fp.kb_taint == ""
    assert fp.digest() == experiment.Fingerprint(**old, kb_taint="").digest()


# ------------------------------------------------------------------ importer

def _run_import(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "miner.import_existing", *args],
        cwd=ROOT, capture_output=True, text=True)


def test_import_refuses_without_acknowledgement():
    r = _run_import("--repo", "/nonexistent", "--db", "/tmp/x-ceiling.sqlite")
    assert r.returncode == 2
    assert "--ceiling" in r.stderr


def test_import_refuses_the_primary_kb():
    r = _run_import("--repo", "/nonexistent", "--db",
                    "/tmp/kb-sbk1.sqlite", "--ceiling")
    assert r.returncode == 2
    assert "primary KB" in r.stderr


def test_import_refuses_to_create_a_kb_from_nothing():
    r = _run_import("--repo", "/nonexistent", "--db",
                    "/tmp/absent-ceiling.sqlite", "--ceiling")
    assert r.returncode == 2
    assert "--from" in r.stderr
