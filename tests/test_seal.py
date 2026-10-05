import json
import sqlite3

import pytest

from eval import clean_set, seal, zero_token_harvest

SCHEMA = """
    CREATE TABLE tus(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE functions(addr INTEGER PRIMARY KEY, name TEXT, tu_id INTEGER, insn_count INTEGER);
    CREATE TABLE attempts(id INTEGER PRIMARY KEY, func_addr INTEGER, compiled INTEGER, score REAL,
                          exact INTEGER, strategy TEXT, source_sha256 TEXT, source_code TEXT);
"""


def make_db(n_tus=40, per_tu=3, prefix="fn"):
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    addr = 0
    for t in range(n_tus):
        conn.execute("INSERT INTO tus VALUES(?,?)", (t, f"build/src/tu{t}.o"))   # real KBs use build/src/*.o
        for k in range(per_tu):
            addr += 1
            conn.execute("INSERT INTO functions VALUES(?,?,?,?)", (addr, f"{prefix}{t}_{k}", t, 30))
            conn.execute("INSERT INTO attempts(func_addr,compiled,score,exact,strategy,source_code) VALUES(?,?,?,?,?,?)",
                         (addr, 1, 90.0 + k, 0, "s1", f"int {prefix}{t}_{k}(){{}}"))
    return conn


@pytest.fixture
def prereg(tmp_path):
    p = tmp_path / "prereg.md"
    p.write_text("primary: paired best score, TU-clustered sign test")
    return p


def freeze(conn, prereg, **kw):
    return seal.freeze({"kb": conn}, prereg=prereg, excluded=kw.pop("excluded", {}), **kw)


def test_pool_fires_on_near_misses_and_declines_each_wrong_shape():
    kb, camp = make_db(2, 1), sqlite3.connect(":memory:")
    kb.execute("INSERT INTO tus VALUES(90,'build/src/other.o'),(91,'build/src/libultra/x.o')")
    kb.execute("INSERT INTO functions VALUES(100,'ex',90,30),(101,'low',90,30),(102,'lib',91,30),"
               "(103,'nc',90,30),(104,'a,b',90,NULL),(105,'campex',90,30),(106,'nullexact',90,30)")
    kb.executemany("INSERT INTO attempts(func_addr,compiled,score,exact,strategy) VALUES(?,?,?,?,?)",
                   [(100, 1, 99, 0, "a"), (100, 1, 100, 1, "b"), (101, 1, 50, 0, "a"), (102, 1, 99, 0, "a"),
                    (103, 0, 99, 0, "a"), (104, 1, 95, 0, "a"), (105, 1, 99, 0, "a"), (106, 1, 97, None, "a")])
    camp.executescript(SCHEMA)
    camp.execute("INSERT INTO tus VALUES(90,'build/src/other.o')")
    camp.execute("INSERT INTO functions VALUES(100,'campex',90,30)")
    camp.execute("INSERT INTO attempts(func_addr,compiled,score,exact,strategy) VALUES(100,1,100,1,'c')")
    rows = seal.pool({"kb": kb, "campaign": camp}, 90, excluded={"fn1_0"})
    names = [r["function"] for r in rows]
    # ex: exact in kb; low/lib/nc: score, library TU, never compiled; campex: exact only in the OTHER ledger;
    # fn1_0: excluded by an earlier frame. a,b (comma, NULL insn_count) and nullexact (exact IS NULL) stay.
    assert names == ["a,b", "fn0_0", "nullexact"]
    by = {r["function"]: r for r in rows}
    assert by["a,b"]["tier"] == "unknown"
    assert by["nullexact"]["unknown_exact_rows"] == 1
    assert by["fn0_0"]["start"] == {"ledger": "kb", "attempt_id": 1,
                                    "source_sha256": by["fn0_0"]["start"]["source_sha256"]}
    assert len(by["fn0_0"]["start"]["source_sha256"]) == 64


def test_split_is_tu_disjoint_deterministic_and_salt_is_the_prereg(prereg, tmp_path):
    conn = make_db()
    a, b = freeze(conn, prereg), freeze(conn, prereg)
    assert a["dev"] == b["dev"] and a["sealed"] == b["sealed"] and a["dev"] and a["sealed"]
    assert seal.audit(a)["clean"]
    other = tmp_path / "other.md"
    other.write_text("different registration")
    assert freeze(conn, other)["sealed"] != a["sealed"]


def test_manifest_is_enforced_by_existing_exclusion_code(prereg, tmp_path):
    m = freeze(make_db(), prereg)
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "sbk1_v5_sealed_nearmiss.json").write_text(json.dumps(m))
    assert zero_token_harvest.heldout_names(sets) == seal.sealed_names(m)
    assert seal.sealed_names(m) <= clean_set._set_names(sets)   # clean_set also takes dev: conservative


def test_audit_detects_tampering_overlap_and_heldout_drift(prereg):
    m = freeze(make_db(), prereg)
    m["sealed"].pop()
    assert not seal.audit(m)["manifest_digest_valid"]
    m = freeze(make_db(), prereg)
    m["dev"].append(dict(m["sealed"][0]))
    m["manifest_digest"] = seal._digest(m)
    assert seal.audit(m)["overlap_functions"]
    m = freeze(make_db(), prereg)
    m["heldout"].pop()
    m["manifest_digest"] = seal._digest(m)
    assert not seal.audit(m)["heldout_list_matches_sealed"]


def test_dev_guard_refuses_sealed_names(prereg):
    m = freeze(make_db(), prereg)
    seal.assert_dev_only([r["function"] for r in m["dev"]], m)
    with pytest.raises(PermissionError):
        seal.assert_dev_only([m["sealed"][0]["function"]], m)


def test_power_is_stated_and_refuses_tiny_seals():
    assert seal.power(5)["min_same_direction_tus"] is None
    assert seal.power(40)["min_same_direction_tus"] == 6


def make_outcomes(conn, m, improve):
    """Add a treatment attempt per sealed function; control = the pinned start."""
    out = {}
    for r in m["sealed"]:
        addr = conn.execute("SELECT func_addr FROM attempts WHERE id=?", (r["start"]["attempt_id"],)).fetchone()[0]
        better = r["function"] in improve
        cur = conn.execute("INSERT INTO attempts(func_addr,compiled,score,exact,strategy,source_code) VALUES(?,?,?,?,?,?)",
                           (addr, 1, 100.0 if better else r["best_score"], 1 if better else 0, "gen", "x"))
        out[r["function"]] = {"control": r["start"], "treatment": {"ledger": "kb", "attempt_id": cur.lastrowid}}
    return out


def test_look_checks_the_ledger_hashes_the_tool_and_chains(prereg, tmp_path):
    conn = make_db()
    m = freeze(conn, prereg, max_looks=2)
    tool = tmp_path / "gen.py"
    tool.write_text("v1")
    log = tmp_path / "looks.jsonl"
    names = sorted(seal.sealed_names(m))
    outcomes = make_outcomes(conn, m, improve={names[0]})
    budget = {"control_compiles": 24, "treatment_compiles": 24}
    ledgers = {"kb": conn}
    with pytest.raises(ValueError):                                   # cherry-picked subset
        seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes={names[0]: outcomes[names[0]]}, budget=budget)
    with pytest.raises(ValueError):                                   # unequal budgets
        seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes=outcomes,
                  budget={"control_compiles": 24, "treatment_compiles": 48})
    bad = {**outcomes, names[1]: {"control": outcomes[names[0]]["control"], "treatment": outcomes[names[1]]["treatment"]}}
    with pytest.raises(ValueError):                                   # attempt belongs to another function
        seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes=bad, budget=budget)
    first = seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes=outcomes, budget=budget)
    assert first["report"]["exact_new"] == [names[0]] and first["prev"] == m["manifest_digest"]
    tool.write_text("v2")
    second = seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes=outcomes, budget=budget)
    assert second["look_number"] == 2 and second["tool_sha256"] != first["tool_sha256"]
    with pytest.raises(RuntimeError):                                 # seal spent
        seal.look(m, log, ledgers, tool="g", tool_files=[tool], outcomes=outcomes, budget=budget)
    assert seal.verify_ledger(log, m) == {"looks": 2, "valid": True, "bad_entries": []}


def test_ledger_truncation_and_rewrite_are_detected(prereg, tmp_path):
    conn = make_db()
    m = freeze(conn, prereg, max_looks=5)
    tool = tmp_path / "gen.py"
    tool.write_text("v1")
    log = tmp_path / "looks.jsonl"
    outcomes = make_outcomes(conn, m, improve=set())
    budget = {"control_compiles": 1, "treatment_compiles": 1}
    for _ in range(2):
        seal.look(m, log, {"kb": conn}, tool="g", tool_files=[tool], outcomes=outcomes, budget=budget)
    rows = log.read_text().splitlines()
    log.write_text(rows[1] + "\n")                                    # drop the first look
    assert not seal.verify_ledger(log, m)["valid"]
    entry = json.loads(rows[0])
    entry["report"]["exact_new"] = ["fabricated"]                     # rewrite a result in place
    log.write_text(json.dumps(entry) + "\n")
    assert not seal.verify_ledger(log, m)["valid"]
    other = freeze(conn, tmp_path / "prereg.md", fraction=0.5)        # ledger from a different manifest
    log.write_text(rows[0] + "\n")
    assert not seal.verify_ledger(log, other)["valid"]


def test_paired_report_is_clustered_by_tu():
    def row(fn, tu, c, t):
        return {"function": fn, "tu": tu, "tier": "small", "control": {"score": c, "exact": False},
                "treatment": {"score": t, "exact": t == 100}}
    rows = [row("a", "t1", 90, 95), row("b", "t1", 90, 96), row("c", "t1", 90, 97),   # one TU, three functions
            row("d", "t2", 90, 90), row("e", "t3", 92, 91)]
    r = seal.paired_report(rows)
    assert (r["tus"], r["tus_up"], r["tus_down"]) == (3, 1, 1)                       # not 3 up
