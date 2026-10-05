"""The seal is enforced where corpus data is selected, not by an opt-in helper.

Each consumer gets a test that it FIRES (drops a sealed name) and one that it declines
(keeps a dev name) -- a filter that silently drops everything looks identical to one that works.
"""
import json
import sqlite3

import pytest

from eval import rule_mine, seal, tool_action_dataset
from test_seal import SCHEMA, make_db


def patch_seal(monkeypatch, d, m):
    real_names, real_tus = seal.sealed_in_sets, seal.sealed_tus_in_sets
    expected = {m["manifest_digest"]}
    monkeypatch.setattr(seal, "sealed_in_sets", lambda *a, **k: real_names(d, expected))
    monkeypatch.setattr(seal, "sealed_tus_in_sets", lambda *a, **k: real_tus(d, expected))


@pytest.fixture
def sets(tmp_path):
    conn = make_db()
    prereg = tmp_path / "p.md"
    prereg.write_text("x")
    m = seal.freeze({"kb": conn}, prereg=prereg, excluded={})
    d = tmp_path / "sets"
    d.mkdir()
    (d / "sbk1_v5_sealed_nearmiss.json").write_text(json.dumps(m))
    return d, m


def test_sealed_in_sets_reads_by_kind_and_verifies_digest(sets):
    d, m = sets
    assert seal.sealed_in_sets(d, {m["manifest_digest"]}) == seal.sealed_names(m)
    assert len(seal.sealed_in_sets(d, {m["manifest_digest"]})) > 0
    (d / "other.json").write_text(json.dumps({"kind": "something-else", "sealed": [{"function": "zz"}]}))
    assert "zz" not in seal.sealed_in_sets(d, {m["manifest_digest"]})
    blob = json.loads((d / "sbk1_v5_sealed_nearmiss.json").read_text())
    blob["sealed"].pop()
    (d / "sbk1_v5_sealed_nearmiss.json").write_text(json.dumps(blob))
    with pytest.raises(RuntimeError):                       # a tampered seal must not shrink the exclusion
        seal.sealed_in_sets(d, {m["manifest_digest"]})


def test_engine_a_corpus_drops_sealed_keeps_dev(sets, tmp_path, monkeypatch):
    d, m = sets
    sealed_fn, dev_fn = m["sealed"][0]["function"], m["dev"][0]["function"]
    kb = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(kb)
    conn.executescript(SCHEMA)
    sealed_tu, dev_tu = m["sealed"][0]["tu"], m["dev"][0]["tu"]
    conn.execute("INSERT INTO tus VALUES(1,?),(2,?)", (sealed_tu, dev_tu))
    for addr, name, tu_id in ((1, sealed_fn, 1), (2, dev_fn, 2)):
        conn.execute("INSERT INTO functions VALUES(?,?,?,30)", (addr, name, tu_id))
        conn.execute("INSERT INTO attempts(func_addr,compiled,score,exact,strategy,source_code) VALUES(?,1,100,1,'gen','x')", (addr,))
        (tmp_path / "nonmatchings" / name).mkdir(parents=True)
        (tmp_path / "nonmatchings" / name / "target.s").write_text("")
    conn.commit()
    conn.close()
    monkeypatch.setattr(rule_mine, "KB", kb)
    monkeypatch.setattr(rule_mine, "CAMPAIGN", kb)
    monkeypatch.setattr(rule_mine, "REPO", tmp_path)
    patch_seal(monkeypatch, d, m)
    assert [r["name"] for r in rule_mine.corpus(10)] == [dev_fn]


def test_engine_b_edges_drop_sealed_keep_dev(sets, tmp_path, monkeypatch):
    d, m = sets
    sealed_fn, dev_fn = m["sealed"][0]["function"], m["dev"][0]["function"]
    db = tmp_path / "c.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.replace("source_code TEXT", "source_code TEXT, diff_summary TEXT") + "CREATE TABLE attempt_edges(parent_attempt_id INT, child_attempt_id INT, relation TEXT);")
    sealed_tu, dev_tu = m["sealed"][0]["tu"], m["dev"][0]["tu"]
    conn.execute("INSERT INTO tus VALUES(1,?),(2,?)", (sealed_tu, dev_tu))
    aid = 0
    for addr, name, tu_id in ((1, sealed_fn, 1), (2, dev_fn, 2)):
        conn.execute("INSERT INTO functions VALUES(?,?,?,30)", (addr, name, tu_id))
        for src in ("a", "b"):
            aid += 1
            conn.execute("INSERT INTO attempts(id,func_addr,compiled,score,exact,strategy,source_code) VALUES(?,?,1,95,0,'g',?)",
                         (aid, addr, src))
        conn.execute("INSERT INTO attempt_edges VALUES(?,?,'refine')", (aid - 1, aid))
    conn.commit()
    conn.close()
    monkeypatch.setattr(rule_mine, "CAMPAIGN", db)
    patch_seal(monkeypatch, d, m)
    assert [row[1] for row in rule_mine._edges()] == [dev_fn]


def test_default_directory_returns_the_real_seal_not_empty():
    names = seal.sealed_in_sets()                    # default dir + pinned digest: the production path
    assert len(names) == 121 and len(seal.sealed_tus_in_sets()) == 29


def test_a_tree_without_the_manifest_raises_instead_of_returning_empty(tmp_path, monkeypatch):
    (tmp_path / "other.json").write_text(json.dumps({"heldout": []}))
    with pytest.raises(RuntimeError, match="not found"):
        seal.sealed_in_sets(tmp_path)                # stale/copied eval/sets
    with pytest.raises(RuntimeError, match="not found"):
        seal.sealed_in_sets(tmp_path / "missing")
    monkeypatch.setenv("GAMEDECOMP_SETS_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        seal.sealed_in_sets()                        # env override is honoured, and still loud


def test_truncated_manifest_raises(sets):
    d, m = sets
    (d / "sbk1_v5_sealed_nearmiss.json").write_text("")            # the cp1252 zero-byte trap
    with pytest.raises(RuntimeError):
        seal.sealed_in_sets(d, {m["manifest_digest"]})


def test_dev_side_is_not_treated_as_sealed_by_trajectory_factory_and_repair_dataset(sets):
    from eval import repair_dataset, trajectory_factory
    d, m = sets
    blocked = trajectory_factory.sealed_functions(d)
    assert blocked == seal.sealed_names(m)                         # fires on sealed, declines dev
    members = repair_dataset.sealed_members(d)["members"]
    assert set().union(*members.values()) == seal.sealed_names(m)


def test_rule_mine_also_excludes_tu_mates_of_sealed_functions(sets, tmp_path, monkeypatch):
    d, m = sets
    mate = "sealed_tu_mate_not_in_pool"
    db = tmp_path / "c.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.replace("source_code TEXT", "source_code TEXT, diff_summary TEXT")
                       + "CREATE TABLE attempt_edges(parent_attempt_id INT, child_attempt_id INT, relation TEXT);")
    conn.execute("INSERT INTO tus VALUES(1,?)", (m["sealed"][0]["tu"],))
    conn.execute("INSERT INTO functions VALUES(1,?,1,30)", (mate,))
    for i, src in ((1, "a"), (2, "b")):
        conn.execute("INSERT INTO attempts(id,func_addr,compiled,score,exact,strategy,source_code) VALUES(?,1,1,95,0,'g',?)", (i, src))
    conn.execute("INSERT INTO attempt_edges VALUES(1,2,'refine')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(rule_mine, "CAMPAIGN", db)
    patch_seal(monkeypatch, d, m)
    assert list(rule_mine._edges()) == []
