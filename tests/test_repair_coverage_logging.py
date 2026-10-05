"""Timeouts in confirmation must remain visible in the experiment's receipts."""
import importlib.util
from pathlib import Path
import sqlite3
import subprocess

from solver import workspace


def test_confirmation_timeout_records_failed_source_and_lineage(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    path = root / "eval/results/repair-coverage-20260922/logged_compile.py"
    spec = importlib.util.spec_from_file_location("coverage_logging", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = sqlite3.connect(tmp_path / "attempts.sqlite")
    conn.executescript((root / "kb/schema.sql").read_text())
    conn.execute("INSERT INTO functions (addr, name) VALUES (1, 'f')")
    parent = workspace.Attempt(True, 90, False, "", "", "")
    workspace.record_attempt(conn, "f", "PARENT", parent)

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("compiler", 30)

    monkeypatch.setattr(workspace, "score", timed_out)
    attempt = module.score_logged(tmp_path, tmp_path, "f", "CHILD", conn=conn,
        action="independent-confirmation", parent_attempt_id=parent.receipt_id,
        extra={"training_eligible": False})
    assert not attempt.compiled and not attempt.exact
    assert "TimeoutExpired" in attempt.compiler_stderr
    row = conn.execute("SELECT source_code, compiled, exact, parent_attempt_id FROM attempts WHERE id=?",
                       (attempt.receipt_id,)).fetchone()
    assert row == ("CHILD", 0, 0, parent.receipt_id)
    assert conn.execute("SELECT count(*) FROM attempt_edges WHERE child_attempt_id=?",
                        (attempt.receipt_id,)).fetchone()[0] == 1
    conn.close()
