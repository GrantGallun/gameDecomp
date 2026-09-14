"""The flywheel must generate leverage without manufacturing evidence."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.flywheel import (  # noqa: E402
    SimilarityCache,
    add_experiment_commands,
    build_snapshot,
    compare_arms,
    compare_snapshots,
)


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE functions (
            addr INTEGER PRIMARY KEY, name TEXT, size INTEGER,
            insn_count INTEGER, is_leaf INTEGER);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY, func_addr INTEGER, compiled INTEGER,
            score REAL, exact INTEGER, source_code TEXT);
    """)


def _fixture_db(conn: sqlite3.Connection) -> None:
    functions = [
        (1, "exactSmall", 48, 12, 1),
        (2, "retiredTarget", 320, 80, 0),
        (3, "targetA", 640, 160, 0),
        (4, "targetB", 1280, 320, 0),
        (5, "bridgeMedium", 320, 80, 1),
        (6, "hardHub", 640, 160, 0),
    ]
    conn.executemany("INSERT INTO functions VALUES (?,?,?,?,?)", functions)
    conn.executemany(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?)",
        [
            (1, 1, 1, 100.0, 1, "s32 exactSmall(void) { return 1; }"),
            (2, 2, 1, 100.0, 1, "s32 retiredTarget(void) { return 2; }"),
            (3, 3, 1, 80.0, 0, "candidate A"),
            (4, 4, 1, 70.0, 0, "candidate B"),
            (5, 5, 1, 96.0, 0, "bridge candidate"),
            (6, 6, 1, 98.0, 0, "hard candidate"),
        ],
    )
    conn.commit()


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({
        "note": "frozen fixture",
        "dev": [
            {"function": "targetA", "tier": "large", "leaf": False},
            {"function": "targetB", "tier": "huge", "leaf": False},
            {"function": "retiredTarget", "tier": "medium", "leaf": False},
        ],
        "heldout": [],
    }), encoding="utf-8")
    triage = tmp_path / "triage.json"
    triage.write_text(json.dumps([
        {"function": "bridgeMedium", "score": 96.0, "structural": 0,
         "offset": 1, "width": 0, "reloc": 0, "regalloc": 1},
        {"function": "hardHub", "score": 98.0, "structural": 10,
         "offset": 0, "width": 0, "reloc": 0, "regalloc": 0},
    ]), encoding="utf-8")
    matched_root = tmp_path / "matched_recovered"
    matched_root.mkdir()
    (matched_root / "diskOnly.c").write_text("verified elsewhere", encoding="utf-8")
    return panel, triage, matched_root


def _retriever(_repo: Path, target: str, allowed: set[str]):
    assert {"exactSmall", "retiredTarget", "bridgeMedium", "hardHub"} <= allowed
    if target == "targetA":
        return [
            ("hardHub", 0.95, Path("reference-must-not-be-read.c")),
            ("bridgeMedium", 0.91, Path("reference-must-not-be-read.c")),
            ("exactSmall", 0.65, Path("reference-must-not-be-read.c")),
        ]
    if target == "targetB":
        return [
            ("bridgeMedium", 0.82, Path("reference-must-not-be-read.c")),
            ("exactSmall", 0.50, Path("reference-must-not-be-read.c")),
        ]
    raise AssertionError(f"retired target should not be ranked: {target}")


def test_snapshot_generates_a_tractable_bridge_queue_without_reference_c(tmp_path):
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _fixture_db(conn)
    panel, triage, matched_root = _inputs(tmp_path)

    snapshot, active_set = build_snapshot(
        conn, tmp_path, panel, triage_path=triage, matched_root=matched_root,
        retriever=_retriever)

    assert snapshot["panel"]["total"] == 3
    assert snapshot["panel"]["active"] == 2
    assert snapshot["panel"]["retired_exact"] == ["retiredTarget"]
    assert [entry["function"] for entry in active_set["dev"]] == [
        "targetA", "targetB"]

    assert snapshot["pool"]["exact_sources"] == 2
    assert snapshot["pool"]["medium_plus_sources"] == 1
    assert snapshot["pool"]["disk_exact_without_source"] == ["diskOnly"]
    assert snapshot["coverage"]["usable"] == 0

    queue = snapshot["bridge_queue"]
    assert [row["function"] for row in queue] == ["bridgeMedium", "hardHub"]
    assert queue[0]["unlock_count"] == 2
    assert queue[0]["usable_unlock_count"] == 2
    assert queue[0]["exploratory_unlock_count"] == 0
    assert queue[0]["structural_faults"] == 0
    assert queue[0]["priority"] > queue[1]["priority"]
    assert {edge["function"] for edge in queue[0]["unlocks"]} == {
        "targetA", "targetB"}

    rendered = json.dumps(snapshot)
    assert "reference-must-not-be-read.c" not in rendered
    assert "return 1" not in rendered
    assert snapshot["guardrails"]["similarity_role"] == "ranking only"


def test_similarity_cache_refilters_without_storing_reference_paths(
        tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "find_similar_functions.py").write_text("index-v1")
    cache_path = tmp_path / "rankings.json"
    calls = []

    def fake_find(_repo, target, **kwargs):
        calls.append((target, kwargs))
        return [("first", 0.9, Path("secret-reference.c")),
                ("later", 0.8, Path("another-secret.c"))]

    monkeypatch.setattr("eval.flywheel.siblings.find", fake_find)
    cache = SimilarityCache(cache_path, repo)
    assert [(name, score) for name, score, _ in
            cache(repo, "target", {"first"})] == [("first", 0.9)]
    assert [(name, score) for name, score, _ in
            cache(repo, "target", {"later"})] == [("later", 0.8)]
    assert len(calls) == 1
    rendered = cache_path.read_text()
    assert "secret-reference.c" not in rendered
    assert "first" in rendered and "later" in rendered


def test_pipeline_uses_the_frozen_pool_without_reloading_live_db(
        monkeypatch, tmp_path):
    from solver import pipeline

    captured = {}
    monkeypatch.setattr(pipeline.kb_context, "for_function", lambda *_: "KB")
    monkeypatch.setattr(pipeline, "hints_for_asm", lambda _asm: "HINTS")
    monkeypatch.setattr(
        pipeline.siblings, "verified_sources",
        lambda _conn: (_ for _ in ()).throw(AssertionError("live pool reloaded")))

    def context_block(_repo, _func, **kwargs):
        captured.update(kwargs)
        return "FROZEN"

    monkeypatch.setattr(pipeline.siblings, "context_block", context_block)
    prompt = pipeline.build_prompt(
        tmp_path, object(), "target", "asm", "draft", "reshape", True,
        sibling_sources={"verified": "source"})
    assert captured["sources"] == {"verified": "source"}
    assert "FROZEN" in prompt


def test_pipeline_uses_shaped_pool_without_reloading_legacy_context(
        monkeypatch, tmp_path):
    from solver import pipeline

    library = {"digest": "shape", "nodes": {}}
    monkeypatch.setattr(pipeline.kb_context, "for_function", lambda *_: "KB")
    monkeypatch.setattr(pipeline, "hints_for_asm", lambda _asm: "HINTS")
    monkeypatch.setattr(
        pipeline.siblings, "context_block",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("legacy sibling context used")))
    captured = {}

    def shaped(_repo, _conn, func, supplied, **kwargs):
        captured.update({"func": func, "library": supplied, **kwargs})
        return "SHAPED"

    monkeypatch.setattr(pipeline.shaped_flywheel, "context_block", shaped)
    prompt = pipeline.build_prompt(
        tmp_path, object(), "target", "asm", "draft", "reshape", True,
        sibling_library=library)
    assert captured["library"] is library
    assert captured["top"] == 2
    assert "SHAPED" in prompt


def test_experiment_commands_are_paired_and_pool_fingerprinted(tmp_path):
    snapshot = {"snapshot_id": "abc123"}
    add_experiment_commands(
        snapshot, Path("active.json"), Path("kb.sqlite"), Path("repo"),
        Path("results"), Path("pool.json"),
        model="model", samples=3, temperature=0.6,
        repeats=2, split="dev")
    commands = snapshot["experiment"]["commands"]
    assert len(commands) == 2
    assert "--siblings" not in commands[0]["control"]
    assert "--siblings" in commands[0]["treatment"]
    assert "--frozen-sibling-pool pool.json" in commands[0]["treatment"]
    assert "abc123-control-r1.jsonl" in commands[0]["control"]
    assert "abc123-siblings-r2.jsonl" in commands[1]["treatment"]


def test_experiment_commands_add_an_explicit_shaped_arm():
    snapshot = {"snapshot_id": "abc123"}
    add_experiment_commands(
        snapshot, Path("active.json"), Path("kb.sqlite"), Path("repo"),
        Path("results"), Path("pool.json"), model="model", samples=3,
        temperature=0.6, repeats=1, split="dev",
        shaped_bundle_path=Path("shaped.json"))
    command = snapshot["experiment"]["commands"][0]
    assert "--shaped-sibling-pool shaped.json" in command["shaped_treatment"]
    assert "abc123-shaped-r1.jsonl" in command["shaped_result"]


def test_snapshot_comparison_distinguishes_growth_from_coverage():
    before = {
        "schema_version": 1, "snapshot_id": "before",
        "panel": {"digest": "panel"},
        "pool": {"source_functions": ["small"]},
        "coverage": {"measured": True, "usable": 0,
                     "rows": [{"function": "hard", "sibling": None}]},
    }
    after = copy.deepcopy(before)
    after["snapshot_id"] = "after"
    after["pool"]["source_functions"].append("bridge")
    after["coverage"] = {
        "measured": True, "usable": 1,
        "rows": [{"function": "hard", "sibling": "bridge", "similarity": 0.9}],
    }
    result = compare_snapshots(before, after)
    assert result["status"] == "flywheel_coverage_engaged"
    assert result["pool_growth"] == 1
    assert result["added_sources"] == ["bridge"]
    assert result["usable_coverage_delta"] == 1
    assert result["changed_targets"][0]["after_sibling"] == "bridge"

    no_coverage = copy.deepcopy(after)
    no_coverage["coverage"] = copy.deepcopy(before["coverage"])
    result = compare_snapshots(before, no_coverage)
    assert result["status"] == "pool_grew_without_hard_panel_coverage"


def test_snapshot_comparison_refuses_panel_drift():
    before = {"schema_version": 1, "panel": {"digest": "a"}}
    after = {"schema_version": 1, "panel": {"digest": "b"}}
    with pytest.raises(ValueError, match="panel drift"):
        compare_snapshots(before, after)


def test_snapshot_refuses_thresholds_that_promote_exploration_to_evidence(tmp_path):
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _fixture_db(conn)
    panel, triage, matched_root = _inputs(tmp_path)
    with pytest.raises(ValueError, match="candidate <= usable"):
        build_snapshot(
            conn, tmp_path, panel, triage_path=triage,
            matched_root=matched_root, candidate_threshold=0.8,
            usable_threshold=0.75, retriever=_retriever)


def test_snapshot_summarizes_source_blind_public_provenance(tmp_path):
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _fixture_db(conn)
    panel, triage, matched_root = _inputs(tmp_path)
    registry = tmp_path / "provenance.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "kind": "n64_public_provenance_hypotheses",
        "policy": {
            "source_bodies_stored": False,
            "source_in_solver_prompt": False,
        },
        "corpus_index": {
            "sha256": "indexhash",
            "stats": {"repository_count": 9, "source_files": 5471,
                      "functions": 52338},
        },
        "hypotheses": [{
            "target_function": "targetA",
            "corpus_source": {
                "repo_id": "game", "repo_head": "abc", "path": "src/a.c",
                "source_body_stored": False,
            },
            "ghidra": {"comparison": {"semantic": {
                "provenance_rank_score": 0.91,
            }}},
            "promotion": {
                "target_candidate_compiled": False,
                "target_oracle_tested": False,
                "target_oracle_exact": False,
            },
        }],
    }), encoding="utf-8")

    snapshot, _active = build_snapshot(
        conn, tmp_path, panel, triage_path=triage, matched_root=matched_root,
        retriever=_retriever, provenance_registry=registry)
    provenance = snapshot["public_provenance"]
    assert provenance["hypotheses"] == 1
    assert provenance["corpus_index"]["functions"] == 52338
    assert provenance["oracle_promoted"] == []
    assert provenance["source_bodies_in_prompt"] is False
    rendered = json.dumps(snapshot)
    assert "src/a.c" not in rendered
    assert snapshot["guardrails"]["public_provenance_in_prompt"] is False


def test_public_provenance_is_forbidden_on_heldout_snapshots(tmp_path):
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _fixture_db(conn)
    panel, _triage, _matched_root = _inputs(tmp_path)
    with pytest.raises(ValueError, match="DEV-only"):
        build_snapshot(
            conn, tmp_path, panel, split="heldout",
            provenance_registry=tmp_path / "not-read.json")


def test_arm_comparison_excludes_infrastructure_and_keeps_exact_primary():
    control = {
        "gain": {"draws": 3, "exact": False, "best_score": 91.0},
        "stable": {"draws": 3, "exact": True, "best_score": 100.0},
        "infra": {"draws": 0, "exact": False, "best_score": 0.0},
        "missingTreatment": {"draws": 3, "exact": False, "best_score": 20.0},
    }
    treatment = {
        "gain": {"draws": 3, "exact": True, "best_score": 100.0},
        "stable": {"draws": 3, "exact": True, "best_score": 100.0},
        "infra": {"draws": 3, "exact": False, "best_score": 80.0},
    }
    result = compare_arms(control, treatment)
    assert result["status"] == "exact_gain_observed_needs_replication"
    assert result["paired_functions"] == 2
    assert result["exact_delta"] == 1
    assert result["mean_score_delta"] == 4.5
    assert result["excluded_infrastructure"][0]["function"] == "infra"
    assert result["missing_treatment"] == ["missingTreatment"]


def test_cli_snapshot_can_skip_external_similarity(tmp_path):
    db = tmp_path / "flywheel.sqlite"
    conn = sqlite3.connect(db)
    _schema(conn)
    _fixture_db(conn)
    conn.close()
    panel, triage, matched_root = _inputs(tmp_path)
    output = tmp_path / "snapshot.json"
    active = tmp_path / "active.json"
    pool = tmp_path / "pool.json"
    result = subprocess.run(
        [sys.executable, "-m", "eval.flywheel", "snapshot",
         "--repo", str(tmp_path), "--db", str(db), "--panel", str(panel),
         "--triage", str(triage), "--matched-root", str(matched_root),
         "--skip-similarity", "--out", str(output),
         "--active-set-out", str(active), "--repeats", "1"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert snapshot["coverage"]["measured"] is False
    assert snapshot["experiment"]["repeats"] == 1
    assert active.exists()
    generated_pool = output.with_name(output.stem + "-sibling-pool.json")
    assert generated_pool.exists()
    assert json.loads(generated_pool.read_text())["digest"] == snapshot["pool"]["digest"]
