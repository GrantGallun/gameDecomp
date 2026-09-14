"""The shaped flywheel preserves trust layers and reduces retrieval anchoring."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from solver import shaped_flywheel


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE functions (
            addr INTEGER PRIMARY KEY, name TEXT, size INTEGER,
            insn_count INTEGER, is_leaf INTEGER, tu_id INTEGER);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY, func_addr INTEGER, exact INTEGER,
            source_code TEXT, strategy TEXT);
        CREATE TABLE evidence (
            id INTEGER PRIMARY KEY, kind TEXT, func_addr INTEGER,
            target_addr INTEGER, base TEXT, offset INTEGER, width INTEGER,
            signed INTEGER, class TEXT, is_load INTEGER);

        INSERT INTO functions VALUES
            (1, 'verifiedTwin', 80, 20, 0, 1),
            (2, 'weakAnalogue', 240, 60, 1, 2),
            (3, 'targetMode', 88, 22, 0, 1),
            (4, 'sharedCall', 20, 5, 1, 1);
        INSERT INTO attempts VALUES
            (10, 1, 1, 's32 verifiedTwin(s32 *arg0) { if (*arg0) { sharedCall(); } return arg0->state; }', 'oracle-exact'),
            (11, 2, 1, 's32 weakAnalogue(void) { return 7; }', 'oracle-exact');
        INSERT INTO evidence VALUES
            (1, 'call', 1, 4, NULL, NULL, NULL, NULL, NULL, NULL),
            (2, 'call', 3, 4, NULL, NULL, NULL, NULL, NULL, NULL),
            (3, 'mem_access', 1, NULL, 'param0', 4, 2, 0, 'int', 1),
            (4, 'mem_access', 3, NULL, 'param0', 4, 2, 0, 'int', 1),
            (5, 'mem_access', 2, NULL, 'param1', 32, 4, 1, 'int', 0);
    """)
    return conn


def _sources():
    return {
        "verifiedTwin": (
            "s32 verifiedTwin(s32 *arg0) { if (*arg0) { sharedCall(); } "
            "return arg0->state; }"),
        "weakAnalogue": "s32 weakAnalogue(void) { return 7; }",
    }


def test_library_has_typed_profiles_edges_and_exact_receipts():
    library = shaped_flywheel.build_library(
        _db(), _sources(), compiler="IDO 5.3 -O2")
    twin = library["nodes"]["verifiedTwin"]
    assert twin["trust"]["exact"] is True
    assert twin["trust"]["attempt_id"] == 10
    assert twin["machine"]["direct_calls"] == ["sharedCall"]
    assert "param0@+0x4:2:int:unsigned:read" in twin["machine"]["memory_shapes"]
    assert twin["source_profile"]["control"]["if"] == 1
    assert twin["source_profile"]["member_names"] == ["state"]
    assert library["digest"]


def test_global_addresses_remain_real_cross_function_identities():
    conn = _db()
    conn.executemany(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (20, "mem_access", 1, None, "global:0x80001000", 0, 4, 1,
             "int", 1),
            (21, "mem_access", 2, None, "global:0x80002000", 0, 4, 1,
             "int", 1),
        ],
    )
    library = shaped_flywheel.build_library(conn, _sources())
    twin = library["nodes"]["verifiedTwin"]["machine"]["memory_shapes"]
    analogue = library["nodes"]["weakAnalogue"]["machine"]["memory_shapes"]
    assert any("global:0x80001000" in shape for shape in twin)
    assert not set(twin) & {
        shape for shape in analogue if "global:0x80002000" in shape}


def test_library_rejects_sources_without_exact_receipts():
    with pytest.raises(ValueError, match="lacks an exact receipt"):
        shaped_flywheel.build_library(
            _db(), {**_sources(), "unverified": "void unverified(void) {}"})


def test_library_round_trip_refuses_hand_edits(tmp_path):
    path = tmp_path / "shaped.json"
    library = shaped_flywheel.build_library(_db(), _sources())
    path.write_text(json.dumps(library), encoding="utf-8")
    assert shaped_flywheel.load_library(path)["digest"] == library["digest"]

    edited = copy.deepcopy(library)
    edited["nodes"]["verifiedTwin"]["exact_source"] += " /* hand edit */"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        shaped_flywheel.load_library(path)


def test_machine_shape_reranks_close_assembly_candidates():
    conn = _db()
    library = shaped_flywheel.build_library(conn, _sources())

    def retrieve(_repo, _target, **_kwargs):
        return [
            ("weakAnalogue", 0.61, Path("must-not-read.c")),
            ("verifiedTwin", 0.60, Path("must-not-read.c")),
        ]

    matches = shaped_flywheel.rank(
        Path("."), conn, "targetMode", library, top=2, retriever=retrieve)
    assert [match["candidate"] for match in matches] == [
        "verifiedTwin", "weakAnalogue"]
    assert matches[0]["shared_calls"] == ["sharedCall"]
    assert matches[0]["shared_memory_shapes"]
    assert matches[0]["components"]["assembly_similarity"] == 0.60


def test_adaptive_context_withholds_weak_body_and_includes_strong_body():
    conn = _db()
    library = shaped_flywheel.build_library(conn, _sources())

    def retrieve_weak(_repo, _target, **_kwargs):
        return [("verifiedTwin", 0.60, Path("secret.c"))]

    weak = shaped_flywheel.render_context(shaped_flywheel.rank(
        Path("."), conn, "targetMode", library, retriever=retrieve_weak))
    assert "full body withheld" in weak
    assert "return arg0->state" not in weak
    assert "shared binary calls: sharedCall" in weak

    def retrieve_strong(_repo, _target, **_kwargs):
        return [("verifiedTwin", 0.93, Path("secret.c"))]

    strong = shaped_flywheel.render_context(shaped_flywheel.rank(
        Path("."), conn, "targetMode", library, retriever=retrieve_strong))
    assert "strong-match exact body" in strong
    assert "return arg0->state" in strong


def test_outcome_receipts_exclude_infrastructure_and_preserve_negative_results():
    control = {
        "targetMode": {"draws": 4, "exact": False, "best_score": 50.0},
        "infra": {"draws": 0, "exact": False, "best_score": 0.0},
    }
    treatment = {
        "targetMode": {"draws": 4, "exact": False, "best_score": 45.0,
                       "sibling": "verifiedTwin", "similarity": 0.6},
        "infra": {"draws": 4, "exact": False, "best_score": 80.0,
                  "sibling": "weakAnalogue", "similarity": 0.5},
    }
    receipts = shaped_flywheel.outcome_receipts(
        control, treatment, arm_pair="r1")
    assert len(receipts) == 1
    assert receipts[0]["score_delta"] == -5.0
    assert receipts[0]["exact_delta"] == 0


def test_library_refuses_outcomes_pointing_at_unverified_candidates():
    outcome = {
        "relation": "retrieval_outcome",
        "candidate": "notExact",
        "target": "targetMode",
    }
    with pytest.raises(ValueError, match="candidate is not exact"):
        shaped_flywheel.build_library(
            _db(), _sources(), outcomes=[outcome])
