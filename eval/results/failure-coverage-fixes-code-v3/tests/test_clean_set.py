"""Fresh split assignment uses metadata and bars all touched functions."""

from __future__ import annotations

import sqlite3

from eval import clean_set
from solver import refine


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    refine.ensure_schema(conn)
    conn.execute(
        "INSERT INTO tus (id,name) VALUES (1,'src/test.c')")
    for address, name in enumerate(
            ("freshA", "freshB", "freshC", "freshD", "attempted",
             "freshE"), 1):
        conn.execute(
            "INSERT INTO functions "
            "(addr,name,tu_id,insn_count,is_leaf,state) "
            "VALUES (?,?,1,10,1,'matched')", (address, name))
    conn.execute(
        "INSERT INTO attempts "
        "(func_addr,iteration,source_code,compiled,created_at) "
        "VALUES (5,0,'int attempted(void);',0,1)")
    conn.commit()
    return conn


def test_freeze_excludes_attempts_prior_sets_and_recovered_sources(tmp_path):
    sets_dir = tmp_path / "eval" / "sets"
    sets_dir.mkdir(parents=True)
    (sets_dir / "old.json").write_text(
        '{"dev":[{"function":"freshA"}],"heldout":[]}')
    recovered = tmp_path / "matched_recovered"
    recovered.mkdir()
    (recovered / "freshB.c").write_text("int freshB(void);")
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    (results / "old.json").write_text(
        '{"results":[{"function":"freshC"}]}')

    manifest = clean_set.freeze(
        _database(), project_root=tmp_path, sets_dir=sets_dir,
        out=sets_dir / "new.json", per_stratum=1, seed=7)
    chosen = {row["function"]
              for split in ("dev", "heldout")
              for row in manifest[split]}

    assert chosen == {"freshD", "freshE"}
    assert manifest["policy"]["target_source_inspected_during_assignment"] \
        is False
    assert "freshD" in chosen, "legacy matched state must not fake a touch"
    assert clean_set.audit(
        _database(), manifest, project_root=tmp_path)["clean"]


def test_audit_detects_later_heldout_attempt(tmp_path):
    conn = _database()
    manifest = {
        "schema_version": 1,
        "heldout": [{"function": "freshC"}],
        "dev": [],
    }
    manifest["manifest_digest"] = clean_set._json_digest(manifest)
    conn.execute(
        "INSERT INTO attempts "
        "(func_addr,iteration,source_code,compiled,created_at) "
        "VALUES (3,0,'int freshC(void);',0,2)")
    conn.commit()

    result = clean_set.audit(conn, manifest, project_root=tmp_path)

    assert not result["clean"]
    assert result["attempted_heldout"] == ["freshC"]


def test_audit_detects_unlogged_result_artifact(tmp_path):
    conn = _database()
    manifest = {
        "schema_version": 1,
        "heldout": [{"function": "freshD"}],
        "dev": [],
    }
    manifest["manifest_digest"] = clean_set._json_digest(manifest)
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    (results / "unlogged.jsonl").write_text(
        '{"function":"freshD","score":90}\n')

    result = clean_set.audit(conn, manifest, project_root=tmp_path)

    assert not result["clean"]
    assert result["result_artifact_heldout"] == ["freshD"]
