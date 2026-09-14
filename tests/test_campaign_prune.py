"""Pruning a checkpoint store keeps every pointer's state and the next commit id."""
import json
import sqlite3

import pytest

from eval import campaign_prune, campaign_state


def make_run(tmp_path, saves=12):
    run = tmp_path / "run"
    run.mkdir()
    store = campaign_state.Store(run / "campaign.json")
    state = {"status": "running", "summary": {}, "nodes": {f"f{i}": {"status": "pending", "score": 0} for i in range(5)}}
    for step in range(saves):
        state["nodes"][f"f{step % 5}"]["score"] = step
        store.save(state, changed={f"f{step % 5}"})
    return run, store, state


def test_select_keeps_recent_daily_and_required():
    day = 86400
    commits = [(1, 0.0), (2, 10.0), (3, day + 5), (4, day + 6), (5, 2 * day + 1)]
    assert campaign_prune.select(commits, 1, {2}) == {5, 2, 4}


def test_build_and_swap_preserve_state_and_continue_saving(tmp_path):
    run, _store, state = make_run(tmp_path)
    (run / "checkpoint.previous.json").write_bytes((run / "campaign.json").read_bytes())
    before = campaign_state.encode(campaign_state.read(run / "campaign.json"))
    receipt = campaign_prune.build(run, keep_recent=2)
    assert receipt["commits_before"] == 12 and receipt["commits_kept"] <= 3
    receipt = campaign_prune.swap(run, receipt)
    assert (run / receipt["backup"]).is_file()                      # original kept, never deleted
    assert campaign_state.encode(campaign_state.read(run / "campaign.json")) == before
    # The campaign resumes from the compacted store: next commit id continues.
    resumed = campaign_state.Store(run / "campaign.json")
    state["nodes"]["f0"]["score"] = 99
    resumed.save(state, changed={"f0"})
    assert json.loads((run / "campaign.json").read_bytes())["commit"] == 13
    assert campaign_state.read(run / "campaign.json")["nodes"]["f0"]["score"] == 99


def test_refuses_with_a_journal_or_existing_output(tmp_path):
    run, _store, _state = make_run(tmp_path, saves=3)
    (run / "campaign.state.sqlite-journal").write_bytes(b"x")
    with pytest.raises(ValueError, match="journal"):
        campaign_prune.build(run, 1)
    (run / "campaign.state.sqlite-journal").unlink()
    (run / ".campaign.state.compact.sqlite").write_bytes(b"")
    with pytest.raises(ValueError, match="already exists"):
        campaign_prune.build(run, 1)


def test_missing_object_is_refused_not_silently_dropped(tmp_path):
    run, _store, _state = make_run(tmp_path, saves=3)
    with sqlite3.connect(run / "campaign.state.sqlite") as conn:
        manifest = json.loads(conn.execute("SELECT manifest FROM commits ORDER BY id DESC").fetchone()[0])
        conn.execute("DELETE FROM objects WHERE hash=?", (manifest["nodes"]["f0"],))
    with pytest.raises(ValueError, match="missing"):
        campaign_prune.build(run, 1)
