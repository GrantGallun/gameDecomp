import sqlite3

import pytest

from eval import frozen_wavefront as frozen


def test_files_and_inventory_include_new_code_but_not_outputs(tmp_path):
    solver = tmp_path / "solver"
    solver.mkdir()
    code = solver / "a.py"
    code.write_text("a = 1")
    outputs = tmp_path / "eval/results"
    outputs.mkdir(parents=True)
    (outputs / "receipt.json").write_text("{}")
    assert frozen.code_paths(tmp_path) == [code]
    pins = frozen.file_hashes([code])
    frozen.verify_files(pins)
    code.write_text("a = 2")
    with pytest.raises(frozen.FrozenInputChanged):
        frozen.verify_files(pins)
    code.unlink()
    with pytest.raises(frozen.FrozenInputChanged):
        frozen.verify_files(pins)


def test_history_freeze_allows_new_attempts_but_rejects_old_edits(tmp_path):
    db = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table functions(addr integer, name text);
        create table attempts(id integer,func_addr integer,source_code text,
                              compiled integer,score real,exact integer,diff_summary text);
        create table model_proposals(id integer,parent_attempt_id integer,raw_response text);
        insert into functions values(1,'f');
        insert into attempts values(1,1,'code',1,90,0,'diff');
        insert into model_proposals values(1,1,'patch');
    """)
    conn.commit()
    digest = frozen.history_digest(db, ("f",), 1, 1)
    conn.execute("insert into attempts values(2,1,'new',1,95,0,'diff')")
    conn.commit()
    assert frozen.history_digest(db, ("f",), 1, 1) == digest
    conn.execute("update model_proposals set raw_response='changed' where id=1")
    conn.commit()
    assert frozen.history_digest(db, ("f",), 1, 1) != digest
    conn.close()
