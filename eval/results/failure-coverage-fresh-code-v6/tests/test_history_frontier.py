import sqlite3

from tools import history_frontier


def test_address_joins_semantic_name_to_exact_historical_symbol():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions (
            addr integer primary key, name text, size integer,
            insn_count integer);
        create table attempts (
            id integer primary key, func_addr integer, score real,
            exact integer);
        insert into functions values (0x80001234, 'semanticName', 160, 40);
        insert into attempts values (1, 0x80001234, 77.5, 0);
    """)
    payload = {"functions": [{
        "function": "func_80001234",
        "history": [{
            "sha": "abc", "subject": "Match func_80001234", "exact": True,
            "changes": [{"status": "M", "path": "src/old.c"}],
        }],
    }]}
    rows = history_frontier.frontier(conn, payload)
    assert rows[0]["function"] == "semanticName"
    assert rows[0]["historical_symbol"] == "func_80001234"
    assert rows[0]["exact_history"][0]["paths"] == ["src/old.c"]


def test_matched_functions_and_excluded_tiers_are_not_returned():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions (
            addr integer primary key, name text, size integer,
            insn_count integer);
        create table attempts (
            id integer primary key, func_addr integer, score real,
            exact integer);
        insert into functions values (0x80000010, 'matched', 40, 10);
        insert into functions values (0x80000020, 'medium', 400, 100);
        insert into attempts values (1, 0x80000010, 100, 1);
        insert into attempts values (2, 0x80000020, 50, 0);
    """)
    payload = {"functions": [
        {"function": "func_80000010", "history": [{"exact": True}]},
        {"function": "func_80000020", "history": [{"exact": True}]},
    ]}
    assert history_frontier.frontier(
        conn, payload, exclude_tiers={"medium"}) == []
