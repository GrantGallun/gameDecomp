"""Frozen-parent A/B selection, accounting, and interpretation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from eval import modelrepair_ab
from solver import refine, workspace


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    refine.ensure_schema(conn)
    return conn


def _add_function(conn, addr: int, name: str) -> None:
    conn.execute(
        "INSERT INTO functions (addr, name, state) VALUES (?,?,'attempted')",
        (addr, name))
    conn.commit()


def _record(conn, name: str, source: str, *, compiled: bool,
            score: float = 0.0, exact: bool = False, stderr: str = "") -> int:
    attempt = workspace.Attempt(
        compiled, score, exact, "- target\n+ current" if compiled else "",
        stderr, "")
    return workspace.record_attempt(conn, name, source, attempt) or 0


def test_freeze_selects_one_parent_per_bucket_and_excludes_exact(tmp_path):
    conn = _database()
    names = ["failed", "middle", "near", "alreadyExact"]
    for index, name in enumerate(names, 1):
        _add_function(conn, index, name)
    _record(conn, "failed", "int failed(void) { return x; }",
            compiled=False, stderr="x undefined")
    _record(conn, "middle", "int middle(void) { return 1; }",
            compiled=True, score=88.0)
    _record(conn, "near", "int near(void) { return 2; }",
            compiled=True, score=98.0)
    _record(conn, "alreadyExact", "int alreadyExact(void) { return 3; }",
            compiled=True, score=100.0, exact=True)

    set_path = tmp_path / "set.json"
    set_path.write_text(json.dumps({
        "dev": [{"function": name, "tier": "tiny", "leaf": True}
                for name in names],
        "heldout": [],
    }))
    manifest = modelrepair_ab.freeze_manifest(
        conn, set_path, per_bucket=1, seed=7)

    assert [row["bucket"] for row in manifest["candidates"]] == list(
        modelrepair_ab.BUCKETS)
    assert {row["function"] for row in manifest["candidates"]} == {
        "failed", "middle", "near"}
    assert "alreadyExact" in manifest["excluded_exact_functions"]
    assert modelrepair_ab._json_digest(manifest) == manifest["manifest_digest"]


def test_freeze_refuses_heldout(tmp_path):
    set_path = tmp_path / "set.json"
    set_path.write_text('{"dev": [], "heldout": []}')
    try:
        modelrepair_ab.freeze_manifest(_database(), set_path, split="heldout")
    except ValueError as exc:
        assert "held-out" in str(exc)
    else:
        raise AssertionError("held-out freeze was accepted")


def test_control_draws_are_independent_and_oracle_stops_on_exact(
        monkeypatch, tmp_path):
    conn = _database()
    _add_function(conn, 1, "f")
    parent = workspace.Attempt(True, 90.0, False, "- target\n+ current", "", "")
    workspace.record_attempt(conn, "f", "int f(void) { return 0; }", parent)
    responses = iter([
        ('```c\nint f(void) { return 1; }\n```', {"eval_count": 10}),
        ('```c\nint f(void) { return 2; }\n```', {"eval_count": 11}),
        ('```c\nint f(void) { return 3; }\n```', {"eval_count": 12}),
    ])
    prompts = []

    def generate(_endpoint, _model, prompt, **_kwargs):
        prompts.append(prompt)
        return next(responses)

    def score(_ws, _repo, _name, code, **_kwargs):
        exact = "return 2;" in code
        return workspace.Attempt(True, 100.0 if exact else 91.0,
                                 exact, "" if exact else "diff", "", "")

    monkeypatch.setattr(modelrepair_ab.llm, "generate", generate)
    monkeypatch.setattr(modelrepair_ab.workspace, "score", score)
    monkeypatch.setattr(modelrepair_ab.workspace, "assert_uncontaminated",
                        lambda *_args, **_kwargs: None)
    result = modelrepair_ab.run_control(
        Path("repo"), conn, "f", Path("ws"), "jr ra",
        "int f(void) { return 0; }", parent, diagnosis="", model="local",
        endpoint="http://local", seeds=[10, 11, 12], timeout=10,
        think="low", num_thread=1, temperature=0.4, num_predict=100,
        cache_dir=tmp_path / "cache", cache_namespace="test", run_id="run")

    assert result["best"]["exact"] is True
    assert result["calls_attempted"] == 2
    assert result["charged_tokens"] == 21
    assert len(set(prompts)) == 1, "every control draw must anchor the same parent"
    assert conn.execute("select count(*) from model_proposals").fetchone()[0] == 2


def test_aggregate_uses_three_exact_floor_and_smoke_never_claims_efficacy():
    def result(control_exact, treatment_exact):
        arm = lambda exact: {
            "best": {"exact": exact}, "calls_attempted": 4,
            "responses": 4, "recorded_tokens": 100,
            "charged_tokens": 100, "compile_conversion": False,
            "score_delta": 1.0,
        }
        return {"status": "complete", "control": arm(control_exact),
                "treatment": arm(treatment_exact)}

    rows = [result(False, True) for _ in range(3)]
    assert modelrepair_ab.aggregate(rows)["verdict"] == \
        "treatment-passes-exact-floor"
    assert modelrepair_ab.aggregate(rows[:2])["verdict"] == "inconclusive"
    assert modelrepair_ab.aggregate(rows, smoke=True)["verdict"] == \
        "mechanical-only"


def test_full_run_refuses_an_underfilled_preregistered_panel(tmp_path):
    manifest = {
        "schema_version": 1,
        "split": "dev",
        "shortfalls": {"compiled-95-plus": 4},
        "candidates": [],
    }
    manifest["manifest_digest"] = modelrepair_ab._json_digest(manifest)

    with pytest.raises(ValueError, match="six frozen parents"):
        modelrepair_ab.run_experiment(
            Path("repo"), sqlite3.connect(":memory:"), manifest,
            out=tmp_path / "out.json", model="local",
            endpoint="http://local", cache_dir=tmp_path / "cache")
