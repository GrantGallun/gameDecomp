import sqlite3
import json
import pytest

from eval.gamewide_probe import select, replay_selection, candidate_rank
from solver.workspace import Attempt
from eval import intake_ledger


def test_fresh_probe_selection_is_stratified_and_excludes_prior_attempts_and_heldout():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions(addr integer,name text,insn_count integer,is_leaf integer,tu_id integer);
        create table attempts(func_addr integer);
        insert into functions values(1,'used',30,1,1),(2,'heldout',30,1,1),
          (3,'leafSmall',30,1,1),(4,'leafLarger',90,1,1),
          (5,'callerSmall',40,0,2),(6,'callerLarger',150,0,2);
        insert into attempts values(1);
    """)
    rows = select(conn, {"heldout"})
    assert [r["function"] for r in rows] == ["leafSmall", "leafLarger", "callerSmall", "callerLarger"]
    assert len({r["stratum"] for r in rows}) == 4
    conn.close()


def test_failed_compile_handoff_preserves_removed_unknown_declarations():
    failed = Attempt(False, 0, False, '', 'compile error', '')
    compiled = Attempt(True, 1, False, '', '', '')
    raw = '? send(? *);\nextern ? gQueue;\nvoid f(void) {}'
    repaired = 'extern Queue gQueue;\nvoid f(void) { bad.member = 1; }'
    assert candidate_rank(failed, repaired) > candidate_rank(failed, raw)
    assert candidate_rank(compiled, raw) > candidate_rank(failed, repaired)


def test_replay_preserves_original_cohort_even_after_attempts(tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.executescript('create table functions(name text, insn_count integer,is_leaf integer,tu_id integer);'
                      "insert into functions values('f',30,1,7);")
    selected = [{'function': 'f', 'instruction_count': 30, 'is_leaf': True, 'tu_id': 7}]
    path = tmp_path / 'prior.json'
    path.write_text(json.dumps({'kind': 'fresh-gamewide-intake-probe', 'selection': selected}))
    assert replay_selection(conn, path, set()) == selected
    with pytest.raises(ValueError, match='held-out'):
        replay_selection(conn, path, {'f'})
    conn.execute('update functions set insn_count=31')
    with pytest.raises(ValueError, match='inventory changed'):
        replay_selection(conn, path, set())
    conn.close()


def test_precompiler_failures_are_parked_only_for_same_revision():
    conn = sqlite3.connect(':memory:')
    intake_ledger.record(conn, 'rev1', 'receipt.json', [
        {'function': 'cacheRoutine', 'status': 'assembly_backend_required'},
        {'function': 'unfinished', 'status': 'running'}])
    assert intake_ledger.parked(conn, 'rev1') == {'cacheRoutine'}
    assert intake_ledger.parked(conn, 'rev2') == set()


def test_all_size_intake_does_not_drop_tiny_or_huge_functions():
    conn = sqlite3.connect(':memory:')
    conn.executescript('''create table functions(addr integer,name text,insn_count integer,is_leaf integer,tu_id integer);
        create table attempts(func_addr integer);
        insert into functions values(1,'tiny',3,1,1),(2,'huge',500,1,1),(3,'caller',20,0,2);''')
    assert [r['function'] for r in select(conn, set(), 3)] == ['tiny', 'huge', 'caller']
    assert [r['function'] for r in select(conn, {'huge'}, 3)] == ['tiny', 'caller']
