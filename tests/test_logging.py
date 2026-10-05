"""Attempts logging must not be silently skippable.

CLAUDE.md: "Log every attempt to the attempts table, including failures. It is
the debugging record now and the training set later."

Only refine.py ever did. Every ad-hoc harness used workspace.score directly, so
an entire day of experiments -- roughly 250 generations -- wrote zero rows and
is unrecoverable. These tests pin the fix.
"""
import sqlite3
import hashlib
import json
import subprocess

import pytest

from kb import attempts as attempt_receipts
from solver import refine, workspace
from eval import matched

SCHEMA = """
CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    func_addr INTEGER, iteration INTEGER, source_code TEXT,
    prompt_context TEXT, compiled INTEGER, compiler_stderr TEXT,
    score REAL, diff_summary TEXT, strategy TEXT, model TEXT,
    sampling TEXT, wall_ms INTEGER, token_cost INTEGER, created_at INTEGER);
"""


@pytest.mark.parametrize('extra', [None, {}, {'normalization': 'c89'}])
@pytest.mark.parametrize('with_frontend', [False, True])
def test_score_accepts_optional_metadata_and_preserves_evidence(monkeypatch, tmp_path, extra, with_frontend):
    from solver import compiler_recipe, frontend_check
    from contextlib import nullcontext
    recipe = {'target': 'build/src/f.o'}
    report = {'passed': False, 'status': 'rejected'}
    (tmp_path / 'Makefile').write_text('CC_CHECK=clang' if with_frontend else '')
    monkeypatch.setattr(workspace, '_workspace_lock', lambda *a: nullcontext())
    monkeypatch.setattr(workspace, '_candidate_compile_source', lambda repo, code: code)
    monkeypatch.setattr(compiler_recipe, 'prepare', lambda *a: (tmp_path / 'build.sh', recipe))
    monkeypatch.setattr(frontend_check, 'check', lambda *a: report)
    monkeypatch.setattr(workspace, 'sh', lambda *a, **kw: (1, 'candidate.c, line 1: Syntax Error'))
    recorded = []
    monkeypatch.setattr(workspace, 'record_attempt', lambda *a, **kw: recorded.append(kw))
    result = workspace.score(tmp_path, tmp_path, 'child', 'bad C', conn=object(), func='f', extra=extra)
    assert not result.compiled
    assert recorded[0]['extra']['compiler_recipe'] == recipe
    if with_frontend:
        assert recorded[0]['extra']['frontend'] == report
    if extra:
        assert recorded[0]['extra']['normalization'] == 'c89'


@pytest.mark.parametrize('stage', ['compiler-recipe', 'source-attribution', 'candidate-source', 'build'])
def test_compiler_infrastructure_failure_is_logged_before_original_exception(conn, monkeypatch, tmp_path, stage):
    from solver import compiler_recipe, source_attribution
    recipe = {'target': 'build/src/f.o', 'command': ['cc', '-O2']}
    failure = (subprocess.TimeoutExpired('candidate build', 300, output=b'partial stdout',
                                       stderr=b'partial stderr') if stage == 'build'
               else ValueError('compiler setup witness'))
    called = []
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(compiler_recipe, 'prepare', fail if stage == 'compiler-recipe'
                        else lambda *a: (tmp_path / 'build.sh', recipe))
    monkeypatch.setattr(source_attribution, 'prepare', fail if stage == 'source-attribution'
                        else lambda ws, name, script: script)
    monkeypatch.setattr(workspace, '_candidate_compile_source', fail if stage == 'candidate-source'
                        else lambda repo, code: code)
    def build(*args, **kwargs):
        called.append('build')
        return fail()
    monkeypatch.setattr(workspace, 'sh', build)
    source = 'int someFunc(void) { return 1; }'
    parent = workspace.record_attempt(conn, 'someFunc', 'parent', _att(), run_id='compiler-failure')
    with pytest.raises(type(failure)) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', source, conn=conn, func='someFunc',
                        strategy='test-compiler', run_id='compiler-failure', parent_attempt_id=parent,
                        extra={'hypothesis': 'preserve me', 'training_eligible': True})
    assert raised.value is failure
    rows = conn.execute('SELECT id,source_code,source_sha256,compiled,exact,score,sampling,compiler_stderr '
                        'FROM attempts WHERE strategy=?', ('test-compiler',)).fetchall()
    assert len(rows) == 1, 'failed setup must leave exactly one durable attempt'
    receipt_id, saved, digest, compiled, exact, score, sampling, stderr = rows[0]
    assert saved == source and digest == hashlib.sha256(source.encode()).hexdigest()
    assert (compiled, exact, score) == (0, 0, 0.0)
    evidence = json.loads(sampling)
    assert evidence['hypothesis'] == 'preserve me'
    assert evidence['training_eligible'] is False
    fault = evidence['compiler_failure']
    assert fault['stage'] == stage
    assert fault['source_sha256'] == digest
    assert fault['build_invoked'] == (stage == 'build')
    assert fault['compiler_invocations'] == (None if stage == 'build' else 0)
    assert fault['exception_type'] == type(failure).__name__
    assert called == (['build'] if stage == 'build' else [])
    if stage == 'build':
        assert 'partial stdout' in stderr and 'partial stderr' in stderr
    if stage != 'compiler-recipe':
        assert evidence['compiler_recipe'] == recipe
    assert conn.execute('SELECT parent_attempt_id,child_attempt_id FROM attempt_edges '
                        'WHERE child_attempt_id=?', (receipt_id,)).fetchone() == (parent, receipt_id)


def test_object_backend_failure_retains_resolved_evidence_and_does_not_run_build(conn, monkeypatch, tmp_path):
    from solver import compiler_recipe
    failure = compiler_recipe.ObjectBackendRequired('build/src/menu/race_setup/race_setup_menu.o',
        {'C_OBJ_POSTPROCESS': 'python3 tools/trim_elf_section_tail.py target.o .data 16',
         'ASFLAGS': '-mips3 -mabi=32'}, 'makefile-pin', 'projection-pin')
    def fail(*args):
        raise failure
    monkeypatch.setattr(compiler_recipe, 'prepare', fail)
    monkeypatch.setattr(workspace, 'sh', lambda *a, **kw: pytest.fail('backend stop must precede build'))
    with pytest.raises(compiler_recipe.ObjectBackendRequired) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', 'int someFunc(void) { return 0; }',
                        conn=conn, func='someFunc')
    assert raised.value is failure
    saved = json.loads(conn.execute('SELECT sampling FROM attempts').fetchone()[0])
    assert saved['compiler_failure']['evidence'] == failure.evidence
    assert saved['compiler_failure']['compiler_invocations'] == 0


@pytest.mark.parametrize('failure', [FileNotFoundError('compiler unavailable'),
                                    OSError('cannot launch helper')])
def test_build_launch_failure_is_logged_and_rethrown(conn, monkeypatch, tmp_path, failure):
    from solver import compiler_recipe, source_attribution
    monkeypatch.setattr(compiler_recipe, 'prepare', lambda *a: None)
    monkeypatch.setattr(source_attribution, 'prepare', lambda ws, name, script: script)
    monkeypatch.setattr(workspace, '_candidate_compile_source', lambda repo, code: code)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(workspace, 'sh', fail)
    with pytest.raises(OSError) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', 'bad source', conn=conn, func='someFunc')
    assert raised.value is failure
    assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 1
    assert conn.execute('SELECT compiled,exact FROM attempts').fetchone() == (0, 0)


@pytest.mark.parametrize('failure', [RuntimeError('compiler controller failed'),
                                    sqlite3.OperationalError('identity query failed')])
def test_unexpected_setup_failure_is_logged_without_changing_exception(conn, monkeypatch, tmp_path, failure):
    from solver import compiler_recipe
    def fail(*args):
        raise failure
    monkeypatch.setattr(compiler_recipe, 'prepare', fail)
    with pytest.raises(type(failure)) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', 'source', conn=conn, func='someFunc')
    assert raised.value is failure
    assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 1


def test_receipt_write_failure_does_not_mask_original_backend_stop(conn, monkeypatch, tmp_path):
    from solver import compiler_recipe
    failure = compiler_recipe.ObjectBackendRequired('build/src/f.o',
        {'C_OBJ_POSTPROCESS': 'unsupported', 'ASFLAGS': '-mips3'}, 'make-pin', 'projection-pin')
    def fail(*args):
        raise failure
    def log_failure(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(compiler_recipe, 'prepare', fail)
    monkeypatch.setattr(workspace, 'record_attempt', log_failure)
    with pytest.raises(compiler_recipe.ObjectBackendRequired) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', 'source', conn=conn, func='someFunc')
    assert raised.value is failure
    assert any('receipt could not be recorded' in note and 'database is locked' in note
               for note in failure.__notes__)


@pytest.mark.parametrize('failure', [KeyboardInterrupt(), SystemExit(2)])
def test_process_interrupts_keep_their_behavior_without_becoming_compiler_failures(monkeypatch, tmp_path, failure):
    from solver import compiler_recipe
    def fail(*args):
        raise failure
    monkeypatch.setattr(compiler_recipe, 'prepare', fail)
    monkeypatch.setattr(workspace, 'record_attempt', lambda *a, **kw: pytest.fail('interrupt is not compiler failure'))
    with pytest.raises(type(failure)) as raised:
        workspace.score(tmp_path, tmp_path, 'candidate', 'source', conn=object(), func='someFunc')
    assert raised.value is failure


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO functions (addr, name) VALUES (?,?)",
              (0x80001234, "someFunc"))
    c.commit()
    return c


def _att(compiled=True, score=42.5, exact=False):
    return workspace.Attempt(compiled, score, exact, "diff text",
                             "stderr text", "raw")


def test_successful_attempt_is_logged(conn):
    assert workspace.log_attempt(conn, "someFunc", "int x;", _att()) is True
    row = conn.execute("select score, compiled, strategy from attempts").fetchone()
    assert row[0] == 42.5 and row[1] == 1


def test_verifier_verdict_is_persisted_not_inferred_from_score(conn):
    workspace.log_attempt(conn, "someFunc", "wrong reloc",
                          _att(score=100.0, exact=False))
    workspace.log_attempt(conn, "someFunc", "right object",
                          _att(score=99.0, exact=True))
    rows = conn.execute("select score, exact from attempts order by id").fetchall()
    assert rows == [(100.0, 0), (99.0, 1)]


def test_old_rows_migrate_to_unknown_not_false_or_true(conn):
    conn.execute(
        "insert into attempts (func_addr, iteration, source_code, compiled, score) "
        "values (0x80001234, 0, 'old', 1, 100.0)")
    conn.commit()
    workspace.log_attempt(conn, "someFunc", "new", _att(exact=True))
    assert conn.execute(
        "select exact from attempts where source_code='old'").fetchone()[0] is None


def test_score_100_without_exact_verdict_is_not_matched(conn):
    workspace.log_attempt(conn, "someFunc", "wrong reloc",
                          _att(score=100.0, exact=False))
    assert matched.matched_in_db(conn) == set()


def test_only_positive_exact_receipt_marks_function_matched(conn):
    workspace.log_attempt(conn, "someFunc", "right object",
                          _att(score=42.0, exact=True))
    assert matched.matched_in_db(conn) == {"someFunc"}


def test_FAILURES_are_logged_too(conn):
    """The non-compiling rows are what made the extraction bugs findable."""
    workspace.log_attempt(conn, "someFunc", "garbage",
                          _att(compiled=False, score=0.0))
    row = conn.execute(
        "select compiled, compiler_stderr from attempts").fetchone()
    assert row[0] == 0
    assert row[1] == "stderr text", "stderr must survive; it is the diagnosis"


def test_unknown_function_does_not_raise(conn):
    """A harness must never crash because a name is missing -- it should just
    report that nothing was written."""
    assert workspace.log_attempt(conn, "noSuchFunc", "x", _att()) is False


def test_no_conn_is_a_no_op_not_an_error(conn):
    assert workspace.log_attempt(None, "someFunc", "x", _att()) is False


def test_sampling_metadata_round_trips(conn):
    workspace.log_attempt(conn, "someFunc", "x", _att(),
                          temperature=0.7, run_id="abc123",
                          extra={"num_predict": 6000})
    import json
    s = json.loads(conn.execute("select sampling from attempts").fetchone()[0])
    assert s["temperature"] == 0.7, "recorded temperature must be the real one"
    assert s["run_id"] == "abc123"
    assert s["num_predict"] == 6000


def test_generation_token_cost_round_trips(conn):
    workspace.log_attempt(conn, "someFunc", "x", _att(), token_cost=731)

    assert conn.execute("select token_cost from attempts").fetchone()[0] == 731


def test_score_accepts_conn_and_func_kwargs():
    """The signature must allow logging, or harnesses cannot comply."""
    import inspect
    sig = inspect.signature(workspace.score)
    assert "conn" in sig.parameters
    assert "func" in sig.parameters


def test_raw_response_is_captured_when_the_column_exists(conn):
    """Only post-extraction source was ever stored, so a failed extraction was
    unreadable afterwards -- which is how 13.8% of compile failures hid."""
    conn.execute("ALTER TABLE attempts ADD COLUMN raw_response TEXT")
    conn.execute("ALTER TABLE attempts ADD COLUMN extract_status TEXT")
    conn.commit()
    workspace.log_attempt(conn, "someFunc", "", _att(compiled=False, score=0.0),
                          raw_response="```c\nint x;  (truncated",
                          extract_status="unterminated")
    row = conn.execute(
        "select raw_response, extract_status from attempts").fetchone()
    assert "truncated" in row[0], "raw model output must survive"
    assert row[1] == "unterminated"


def test_logging_migrates_old_tables_and_keeps_raw_response(conn):
    """Older databases are upgraded in place instead of losing model text."""
    assert workspace.log_attempt(conn, "someFunc", "x", _att(),
                                 raw_response="ignored me") is True
    cols = {row[1] for row in conn.execute("pragma table_info(attempts)")}
    assert {"run_id", "parent_attempt_id", "source_sha256",
            "prompt_sha256", "raw_response"} <= cols
    assert conn.execute(
        "select raw_response from attempts").fetchone()[0] == "ignored me"


def test_run_and_parent_edge_are_first_class_receipts(conn):
    parent = workspace.record_attempt(
        conn, "someFunc", "int a;", _att(score=10.0),
        iteration=1, run_id="run-1", run_kind="refine", model="local",
        prompt="first prompt", temperature=0.2,
        run_config={"max_iters": 4})
    child = workspace.record_attempt(
        conn, "someFunc", "int b;", _att(score=20.0),
        iteration=2, run_id="run-1", run_kind="refine", model="local",
        prompt="repair prompt", temperature=0.4,
        parent_attempt_id=parent, relation="refine", action="fix-diff",
        feedback="- target\n+ candidate")

    run = conn.execute(
        "select kind, model, config from attempt_runs where id='run-1'"
    ).fetchone()
    assert run[0:2] == ("refine", "local")
    assert '"max_iters": 4' in run[2]
    row = conn.execute(
        "select run_id, parent_attempt_id, source_sha256, prompt_sha256 "
        "from attempts where id=?", (child,)).fetchone()
    assert row[0:2] == ("run-1", parent)
    assert row[2] == hashlib.sha256(b"int b;").hexdigest()
    assert row[3] == hashlib.sha256(b"repair prompt").hexdigest()
    edge = conn.execute(
        "select relation, action, feedback from attempt_edges "
        "where parent_attempt_id=? and child_attempt_id=?", (parent, child)
    ).fetchone()
    assert edge == ("refine", "fix-diff", "- target\n+ candidate")


def test_attempt_object_receives_its_persisted_id(conn):
    att = _att()
    receipt_id = workspace.record_attempt(conn, "someFunc", "x", att)

    assert receipt_id == att.receipt_id


def test_invalid_and_valid_model_proposals_have_separate_receipts(conn):
    parent = workspace.record_attempt(
        conn, "someFunc", "int a;", _att(), run_id="model-run",
        run_kind="model-repair", model="local")
    proposal = attempt_receipts.record_model_proposal(
        conn, run_id="model-run", parent_attempt_id=parent, prompt="repair",
        raw_response="not json", status="invalid", model="local")

    row = conn.execute(
        "select status, raw_response, child_attempt_id from model_proposals "
        "where id=?", (proposal,)).fetchone()
    assert row == ("invalid", "not json", None)

    child = workspace.record_attempt(
        conn, "someFunc", "int b;", _att(score=50), run_id="model-run",
        parent_attempt_id=parent, relation="model-repair")
    attempt_receipts.link_model_proposal(conn, proposal, child)
    assert conn.execute(
        "select child_attempt_id from model_proposals where id=?", (proposal,)
    ).fetchone()[0] == child


def test_refine_logger_preserves_real_sampling_and_parentage(conn):
    first = refine.log_attempt(
        conn, 0x80001234, "someFunc", 1, "a", "p1", _att(score=10),
        {"eval_count": 11, "_num_predict": 6000, "done_reason": "stop"},
        "draft", "local", 20, temperature=0.2, run_id="refine-run",
        raw_response="raw one", run_kind="refine")
    second = refine.log_attempt(
        conn, 0x80001234, "someFunc", 2, "b", "p2", _att(score=20),
        {"eval_count": 12, "_num_predict": 6000, "done_reason": "stop"},
        "fix-diff", "local", 30, temperature=0.45, run_id="refine-run",
        raw_response="raw two", parent_attempt_id=first,
        relation="refine", action="fix-diff", feedback="instruction diff",
        run_kind="refine")

    import json
    sampling, parent, raw = conn.execute(
        "select sampling, parent_attempt_id, raw_response from attempts "
        "where id=?", (second,)).fetchone()
    assert json.loads(sampling)["temperature"] == 0.45
    assert parent == first and raw == "raw two"


def test_full_schema_script_can_upgrade_a_pre_lineage_database():
    old = sqlite3.connect(":memory:")
    old.executescript("""
        CREATE TABLE functions (
            addr INTEGER PRIMARY KEY, name TEXT, tu_id INTEGER, size INTEGER,
            insn_count INTEGER, is_leaf INTEGER, state TEXT,
            best_score REAL, attempts INTEGER
        );
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY, func_addr INTEGER, iteration INTEGER,
            source_code TEXT, prompt_context TEXT, compiled INTEGER,
            compiler_stderr TEXT, score REAL, diff_summary TEXT,
            strategy TEXT, model TEXT, sampling TEXT, wall_ms INTEGER,
            token_cost INTEGER, created_at INTEGER
        );
    """)

    refine.ensure_schema(old)

    cols = {row[1] for row in old.execute("pragma table_info(attempts)")}
    indexes = {row[1] for row in old.execute("pragma index_list(attempts)")}
    assert {"exact", "run_id", "parent_attempt_id", "raw_response"} <= cols
    assert {"att_run", "att_parent"} <= indexes
