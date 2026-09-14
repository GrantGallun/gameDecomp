from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from eval import principle_agent_ab
from solver import principles, refine, residual, toolagent, workspace


class Provider:
    provider_id = "scripted"

    def generate(self, _request):
        raise AssertionError("mock search should not generate")


def _packet():
    return residual.ResidualPacket(
        compiled=True, exact=False, weighted_progress_score=99.0,
        compiler_error_signature="", target_instructions=3,
        candidate_instructions=3, instruction_delta=0,
        target_text_bytes=16, candidate_text_bytes=16,
        text_length_delta=0, positional_byte_distance=3,
        positional_equal_bytes=13, changed_diff_lines=2,
        faults={"structural": 0, "layout": 0, "offset": 0, "width": 0,
                "relocation": 0, "register_allocation": 1, "immediate": 0},
        first_difference=())


def _db(path: Path) -> int:
    conn = sqlite3.connect(path)
    refine.ensure_schema(conn)
    conn.execute(
        "INSERT INTO functions (addr,name,state) VALUES (1,'f','attempted')")
    conn.execute(
        "INSERT INTO attempts (func_addr,iteration,source_code,compiled,score,"
        "exact,created_at) VALUES (1,0,'int f(void){return 0;}',1,99,0,1)")
    attempt_id = conn.execute("SELECT id FROM attempts").fetchone()[0]
    conn.commit()
    conn.close()
    return int(attempt_id)


def test_runner_changes_policy_then_principles(monkeypatch, tmp_path):
    db = tmp_path / "kb.sqlite"
    attempt_id = _db(db)
    source = "int f(void){return 0;}"
    manifest = {
        "schema_version": 1,
        "kind": "true-residual-dev-principle-panel",
        "panel": [{"function": "f", "attempt_id": attempt_id,
                   "source_sha256": hashlib.sha256(source.encode()).hexdigest()}],
    }
    manifest["manifest_digest"] = principle_agent_ab._manifest_digest(manifest)
    manifest_path = tmp_path / "panel.json"
    manifest_path.write_text(json.dumps(manifest))
    sets = tmp_path / "sets"
    sets.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    root = workspace.Attempt(True, 99.0, False, "-a\n+b", "", "", 10)
    monkeypatch.setattr(principle_agent_ab.workspace, "bootstrap",
                        lambda *_args: ws)
    monkeypatch.setattr(principle_agent_ab.workspace, "target_asm",
                        lambda *_args: "glabel f\n")
    monkeypatch.setattr(principle_agent_ab.workspace, "score",
                        lambda *_args, **_kwargs: root)
    monkeypatch.setattr(principle_agent_ab.residual, "build",
                        lambda *_args, **_kwargs: _packet())
    monkeypatch.setattr(principle_agent_ab.agentrepair, "_diagnosis",
                        lambda *_args: "diagnosis")
    match = principles.Match("p", "CONFIRMED", "fires", "try shape")
    monkeypatch.setattr(principle_agent_ab.principles, "retrieve",
                        lambda *_args, **_kwargs: (match,))
    captured = {}

    def fake_search(_repo, _name, candidate_source, _ws, **kwargs):
        arm = kwargs["run_id"].rsplit("-", 1)[-1]
        captured[arm] = kwargs
        candidate = toolagent.Candidate("c0", candidate_source, root)
        return toolagent.Result("f", candidate, candidate)

    monkeypatch.setattr(principle_agent_ab.toolagent, "search", fake_search)
    receipt = principle_agent_ab.run(
        repo=tmp_path, db=db, sets=sets, manifest_path=manifest_path,
        out=tmp_path / "out.json", endpoint="provider://test",
        provider=Provider(), max_calls=4, curiosity_min_calls=3,
        curiosity_min_compiles=1, curiosity_min_tools=1)

    assert captured["free_open_book"]["min_calls_before_finish"] == 0
    assert captured["curiosity"]["min_calls_before_finish"] == 3
    assert captured["principled"]["min_calls_before_finish"] == 3
    assert captured["curiosity"]["principles"] == ()
    assert "try shape" in captured["principled"]["principles"][0]
    assert receipt["aggregate"]["comparisons"]["principled_vs_curiosity_n"] == 1
