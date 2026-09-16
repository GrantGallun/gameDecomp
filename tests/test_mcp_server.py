"""The self-test tools must return what the compiler says, not what a model would like to hear.

The fire test at the bottom runs the real per-function workspace and the real compiler, and is opt-in
because bootstrapping a workspace takes a minute.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcpserver import gamedecomp_server as srv


def _kb(tmp_path: Path) -> Path:
    """A fixture knowledge base with the three tables the tools read."""
    path = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table functions (addr integer primary key, name text, tu_id integer,
                                size integer, insn_count integer, is_leaf integer,
                                state text, best_score real, attempts integer);
        create table attempts (id integer primary key, func_addr integer, parent_attempt_id integer,
                               iteration integer, source_code text, compiled integer, score real,
                               exact integer, diff_summary text, strategy text, model text,
                               compiler_stderr text);
        create table evidence (id integer primary key, kind text, addr integer, func_addr integer,
                               op text, base text, offset integer, width integer, signed integer,
                               access text, is_load integer, target_addr integer);
        create table attempt_edges (parent_attempt_id integer, child_attempt_id integer);
    """)
    layout_diff = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
                   "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-lw    t1,0x28(a0)\n+lw    t1,0x2c(a0)\n")
    structural_diff = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
                       "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-bne   t0,zero,10\n+beq   t0,zero,10\n")
    conn.execute("insert into functions values (4096,'tractable',1,64,16,1,'attempted',88.0,9)")
    conn.execute("insert into functions values (8192,'stuck',1,256,64,0,'attempted',96.0,40)")
    conn.execute("insert into attempts values (1,4096,null,0,'',1,88.0,0,?,'diffrepair','m',null)",
                 (layout_diff,))
    conn.execute("insert into attempts values (2,8192,null,0,'',1,96.0,0,?,'model','m',null)",
                 (structural_diff,))
    conn.execute("insert into evidence values (1,'mem_access',16,4096,'lw','param0',0x24,4,1,"
                 "'full',1,null)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def ctx(tmp_path) -> srv.Context:
    return srv.Context(repo=tmp_path / "repo", kb=_kb(tmp_path))


# --- ranking ------------------------------------------------------------------

def test_functions_are_ranked_by_owned_residual_not_by_score(ctx):
    """88% with a repairable layout fault beats 96% with an unrepairable structural one."""
    rows = srv.list_functions(ctx, limit=5)
    assert [row["name"] for row in rows] == ["tractable", "stuck"]
    assert rows[0]["owned_share"] > rows[1]["owned_share"]
    assert rows[0]["tractable_faults"] >= 1 and rows[1]["tractable_faults"] == 0


def test_the_ranking_carries_the_fault_profile_that_justifies_it(ctx):
    row = srv.list_functions(ctx, limit=1)[0]
    assert row["faults"]["layout"] >= 1
    assert set(row["faults"]) == set(srv.AXES)


def test_an_empty_selection_returns_nothing_rather_than_raising(ctx):
    assert srv.list_functions(ctx, limit=5, min_attempts=99) == []


# --- status and evidence ------------------------------------------------------

def test_function_status_reports_the_residual_and_its_kind(ctx):
    status = srv.function_status(ctx, 4096)
    assert status["name"] == "tractable" and status["best_score"] == 88.0
    assert status["faults"]["layout"] >= 1
    assert "lw" in status["diff_head"]


def test_function_status_says_so_when_the_function_is_unknown(ctx):
    assert "error" in srv.function_status(ctx, 999999)


def test_evidence_is_returned_as_facts_with_no_invented_fields(ctx):
    row = srv.evidence_for(ctx, 4096)
    assert row["rows"][0]["width"] == 4 and row["rows"][0]["offset"] == 0x24
    assert "inferred" in row["note"]


# --- the classifier -----------------------------------------------------------

def test_a_moved_field_access_is_layout_and_an_inverted_branch_is_structural(ctx):
    layout = srv.classify_residual(ctx, " lw t0,0x24(a0)\n-lw t1,0x28(a0)\n+lw t1,0x2c(a0)\n")
    structural = srv.classify_residual(ctx, " lw t0,0x24(a0)\n-bne t0,zero,10\n+beq t0,zero,10\n")
    assert layout["faults"]["layout"] >= 1 and layout["faults"]["structural"] == 0
    assert structural["faults"]["structural"] >= 1 and structural["faults"]["layout"] == 0


def test_the_classifier_separates_what_a_pass_owns_from_what_nothing_owns(ctx):
    result = srv.classify_residual(ctx, " bne t0,zero,10\n+beq t0,zero,10\n")
    assert result["unrepairable"] >= 1, "a structural fault has no implemented pass"
    assert result["repairable"] == 0
    # The buckets are reported separately rather than as one "fault count", because a pass owning a
    # fault and a fault merely being classified are different facts about what to do next.
    assert {"repairable", "conditional_repair", "no_repair_implemented", "unrepairable"} <= set(result)


# --- practice and scarcity ----------------------------------------------------

def test_practice_lists_families_then_serves_one_with_a_known_answer(ctx):
    listing = srv.practice_function(ctx)
    assert "families" in listing and listing["families"]
    one = srv.practice_function(ctx, family=listing["families"][0], seed=3)
    assert one["source"] and one["expectation"] and one["name"].startswith("syn_")
    assert srv.practice_function(ctx, family="no-such-family")["error"]


def test_trajectory_status_shows_the_scarcity_the_model_is_in(ctx):
    status = srv.trajectory_status(ctx)
    assert status["improving_edges"] == 0, "the fixture has no improvement edges"
    assert "scarce" in status["note"]


# --- logging: a scored candidate IS an attempt --------------------------------
#
# The tool this server exists for compiles C and returns the compiler's verdict. That verdict is an
# attempt, and CLAUDE.md requires every attempt to be recorded -- solver/workspace.py:470 records what
# happens otherwise: a whole day of ~250 generations wrote ZERO rows. Two defects made that impossible
# here, and both are pinned below:
#
#   1. `log` defaulted to False, so the ordinary call recorded nothing;
#   2. `Context.connect()` is READ-ONLY, so even `log=True` would have raised rather than written;
#   3. `parent_attempt_id` was never passed, so no parent->child edge could be created -- and the
#      explicit edge is the entire basis of the refinement dataset (TRAINING.md).

def test_context_connect_is_read_only_but_connect_write_is_not(ctx):
    with pytest.raises(sqlite3.OperationalError):
        ctx.connect().execute("create table nope (x integer)")
    writer = ctx.connect_write()
    try:
        writer.execute("create table scratch (x integer)")
        writer.commit()
    finally:
        writer.close()


def test_score_candidate_logs_by_default_and_writes_the_parent_edge(ctx, monkeypatch, tmp_path):
    captured = {}

    class FakeAttempt:
        compiled, score, exact = True, 91.5, False
        diff, compiler_stderr, receipt_id = "", "", 4242

    def fake_score(ws, repo, name, source, **kwargs):
        captured["kwargs"] = kwargs
        return FakeAttempt()

    # The workspace is incidental here: this pins the LOGGING WIRING, and bootstrapping a real
    # per-function workspace takes a minute and a compiler (the opt-in fire test at the bottom).
    monkeypatch.setattr(srv, "_workspace", lambda c, n: tmp_path)
    monkeypatch.setattr(srv.workspace, "score", fake_score)
    verdict = srv.score_candidate(ctx, 4096, "void f(void) { }\n", parent=7, run_id="mcp-test")

    assert captured["kwargs"]["conn"] is not None, "log must default to True"
    assert captured["kwargs"]["parent_attempt_id"] == 7
    assert captured["kwargs"]["relation"] == "derive"
    assert captured["kwargs"]["func"] == "tractable"
    assert verdict["logged"] is True
    assert verdict["receipt_id"] == 4242
    assert verdict["parent_attempt_id"] == 7


def test_score_candidate_without_a_parent_records_no_edge(ctx, monkeypatch, tmp_path):
    captured = {}

    class FakeAttempt:
        compiled, score, exact = False, 0.0, False
        diff, compiler_stderr, receipt_id = "", "cfe: Error: candidate.c, line 1: Syntax Error", None

    def fake_score(ws, repo, name, source, **kwargs):
        captured["kwargs"] = kwargs
        return FakeAttempt()

    monkeypatch.setattr(srv, "_workspace", lambda c, n: tmp_path)
    monkeypatch.setattr(srv.workspace, "score", fake_score)
    verdict = srv.score_candidate(ctx, 4096, "not c\n")
    # An independent draw has no parent, and inventing one from the previous row is forbidden.
    assert captured["kwargs"]["parent_attempt_id"] is None
    assert captured["kwargs"]["relation"] == ""
    assert verdict["logged"] is True


def test_score_candidate_can_opt_out_of_logging(ctx, monkeypatch, tmp_path):
    captured = {}

    class FakeAttempt:
        compiled, score, exact = True, 50.0, False
        diff, compiler_stderr, receipt_id = "", "", None

    def fake_score(ws, repo, name, source, **kwargs):
        captured["kwargs"] = kwargs
        return FakeAttempt()

    monkeypatch.setattr(srv, "_workspace", lambda c, n: tmp_path)
    monkeypatch.setattr(srv.workspace, "score", fake_score)
    verdict = srv.score_candidate(ctx, 4096, "void f(void) { }\n", log=False)
    assert captured["kwargs"]["conn"] is None
    assert verdict["logged"] is False


# --- fire test: the real compiler ---------------------------------------------

@pytest.mark.skipif(os.environ.get("GAMEDECOMP_MCP_FIRE") != "1",
                    reason="bootstraps a workspace and runs the real compiler; set GAMEDECOMP_MCP_FIRE=1")
def test_score_candidate_returns_the_compilers_verdict_not_an_opinion():
    ctx = srv.Context()
    if not (ctx.repo / "tools" / "claude").exists():
        pytest.skip("matching workspace tooling unavailable")
    rows = srv.list_functions(ctx, limit=1)
    assert rows, "no candidate functions in the knowledge base"
    addr = rows[0]["addr"]
    status = srv.function_status(ctx, addr)
    verdict = srv.score_candidate(ctx, addr, "void placeholder(void) { }\n")
    assert verdict["func"] == addr and verdict["name"] == status["name"]
    assert isinstance(verdict["score"], float)
    assert set(verdict["faults"]) == set(srv.AXES)
    # A stub cannot be byte-exact, and the tool must say so rather than guessing.
    assert verdict["exact"] is False
