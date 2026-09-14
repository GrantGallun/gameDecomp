import hashlib
import sqlite3

from solver import proposal_recovery


def test_recovery_is_function_and_cutoff_bound_and_verifies_parent_hash():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions(addr integer,name text);
        create table attempts(id integer,func_addr integer,source_code text,source_sha256 text);
        create table model_proposals(id integer,parent_attempt_id integer,raw_response text,kind text);
        insert into functions values(1,'f'),(2,'other');
    """)
    for ident, addr, source in ((1, 1, "original"), (2, 2, "other"), (3, 1, "corrupt")):
        conn.execute("insert into attempts values(?,?,?,?)", (ident, addr, source,
                     hashlib.sha256(source.encode()).hexdigest() if ident != 3 else "bad"))
    conn.executemany("insert into model_proposals values(?,?,?,?)", [
        (1, 1, "patch", "patch"), (2, 1, "thinking", "diagnosis"),
        (3, 2, "other patch", "patch"), (4, 3, "bad parent", "patch"),
        (5, 1, "future patch", "patch")])
    assert [p.proposal_id for p in proposal_recovery.load(conn, "f", "original", cutoff=4)] == [1]
