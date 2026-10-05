"""Pilot failures must be logged, and replay contexts must bind actual inputs."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dream_pilot", ROOT / "eval/results/dream-search-20260922/pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@pytest.mark.parametrize("relative", ["nonmatchings/f/target.o", "nonmatchings/f/target_object_dump_normalized.s",
    "nonmatchings/f/dist.py", "nonmatchings/f/.diff_algorithm", "src/lib/local.h", "tools/textconv.py", "tools/charmap.txt"])
def test_compiler_context_changes_with_real_build_and_scoring_inputs(tmp_path, relative):
    ws = tmp_path / "nonmatchings/f"
    ws.mkdir(parents=True)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first")
    before = pilot.compiler_identity(tmp_path, ws)
    path.write_text("changed")
    assert pilot.compiler_identity(tmp_path, ws) != before


def test_failed_compiler_callback_gets_a_real_sqlite_receipt(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    conn.execute("INSERT INTO functions(addr,name) VALUES(1,'probe')")
    conn.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("compiler crashed")

    monkeypatch.setattr(pilot.workspace, "score", fail)
    result = pilot.logged_score(tmp_path, tmp_path, "probe", "int probe;", conn, run_id="failure-test")
    row = conn.execute("SELECT compiled,exact,compiler_stderr FROM attempts WHERE id=?", (result.receipt_id,)).fetchone()
    assert row == (0, 0, "RuntimeError: compiler crashed")


def test_confirmation_selection_includes_calibration_and_deduplicates_sources():
    rows = [{"function": "cal", "exact": True, "source": "int cal;"},
            {"function": "cal", "exact": True, "source": "int cal;"},
            {"function": "fresh", "exact": True, "source": "int fresh;"},
            {"function": "miss", "exact": False, "source": "int miss;"}]
    selected = pilot.confirmation_sources(rows)
    assert set(selected.values()) == {"int cal;", "int fresh;"}
    assert len(selected) == 2
