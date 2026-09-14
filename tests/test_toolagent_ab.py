"""The richer-search smoke changes tools, not roots or budgets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from eval import toolagent_ab
from solver import modelrepair, refine, toolagent, workspace


class Provider:
    provider_id = "scripted"

    def generate(self, _request):
        raise AssertionError("mock arms should not call the provider")


def _database(path: Path) -> int:
    conn = sqlite3.connect(path)
    refine.ensure_schema(conn)
    conn.execute(
        "INSERT INTO functions (addr,name,state) VALUES (1,'f','attempted')")
    conn.execute(
        "INSERT INTO attempts (func_addr,iteration,source_code,compiled,score,"
        "exact,created_at) VALUES (1,0,'int f(void) { return 0; }',1,90,0,1)")
    attempt_id = conn.execute("SELECT id FROM attempts").fetchone()[0]
    conn.commit()
    conn.close()
    return attempt_id


def test_ab_uses_same_root_seed_schedule_and_caps(monkeypatch, tmp_path):
    db = tmp_path / "kb.sqlite"
    attempt_id = _database(db)
    ws = tmp_path / "ws"
    ws.mkdir()
    root = workspace.Attempt(True, 90.0, False, "-jr ra\n+jr v0", "", "", 77)
    monkeypatch.setattr(toolagent_ab.workspace, "bootstrap",
                        lambda _repo, _function: ws)
    monkeypatch.setattr(toolagent_ab.workspace, "target_asm",
                        lambda _ws, _function: "glabel f\njr ra\nnop")
    monkeypatch.setattr(toolagent_ab.workspace, "score",
                        lambda *_args, **_kwargs: root)
    monkeypatch.setattr(toolagent_ab.agentrepair, "_diagnosis",
                        lambda *_args, **_kwargs: "same diagnosis")
    captured = {}

    def proposal_search(_repo, _name, source, _ws, **kwargs):
        captured["proposal"] = kwargs
        return modelrepair.Result(
            "f", root, source, calls_attempted=4, generations=4,
            tokens=20, charged_tokens=20, compiling_children=2)

    def tool_search(_repo, _name, source, _ws, **kwargs):
        captured["tool"] = kwargs
        candidate = toolagent.Candidate("c0", source, root)
        return toolagent.Result(
            "f", candidate, candidate, calls_attempted=4, generations=4,
            compiles=2, tokens=20, charged_tokens=20, tool_actions=2)

    monkeypatch.setattr(toolagent_ab.modelrepair, "search", proposal_search)
    monkeypatch.setattr(toolagent_ab.toolagent, "search", tool_search)
    out = tmp_path / "ab.json"
    receipt = toolagent_ab.run(
        repo=tmp_path, db=db, function="f", attempt_id=attempt_id,
        out=out, endpoint="provider://test", provider=Provider(),
        max_calls=4, seed=100, open_book=True,
        workbench_root=tmp_path)

    assert captured["proposal"]["base_attempt"] is root
    assert captured["tool"]["base_attempt"] is root
    assert captured["proposal"]["call_seeds"] == (101, 102, 103, 104)
    assert captured["tool"]["call_seeds"] == (101, 102, 103, 104)
    assert captured["proposal"]["max_calls"] == 4
    assert captured["tool"]["max_calls"] == 4
    assert captured["tool"]["max_compiles"] == 4
    assert captured["tool"]["open_book"] is True
    assert captured["tool"]["require_inspection_before_patch"] is False
    assert receipt["kind"] == "proposal-only-vs-open-book-agent"
    assert captured["proposal"]["diagnosis"] == "same diagnosis"
    assert captured["tool"]["diagnosis"] == "same diagnosis"
    assert receipt["aggregate"]["interpretation"].startswith(
        "single-parent smoke")
    assert out.is_file()
