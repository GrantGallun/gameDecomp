"""Public provenance receipts must remain source-blind and non-promotional."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from tools import n64_provenance


def _evidence(path: Path, entry: str, feature: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 2,
        "program": {"language": "MIPS:BE:32:default"},
        "function": {"entry": entry},
        "bsim_signature": {
            "completed": True, "error": "", "settings": 77,
            "features": [feature],
        },
        "normalized_ir": {
            "completed": True, "error": "",
            "operations": [{
                "opcode": "RETURN", "output": None,
                "inputs": ["(const, 0x0, 4)"],
            }],
            "basic_blocks": [{"in": [], "out": []}],
        },
        "instructions": [{"mnemonic": "jr"}],
    }), encoding="utf-8")


def _index(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE repositories(
            id TEXT PRIMARY KEY, url TEXT NOT NULL, head TEXT NOT NULL,
            shallow INTEGER NOT NULL, evaluation_policy TEXT NOT NULL,
            source_files INTEGER NOT NULL, function_count INTEGER NOT NULL);
        CREATE TABLE functions(
            id INTEGER PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL,
            line INTEGER NOT NULL, name TEXT NOT NULL, token_count INTEGER NOT NULL,
            normalized_sha256 TEXT NOT NULL, sketch BLOB NOT NULL,
            constants_json TEXT NOT NULL, calls_json TEXT NOT NULL);
        INSERT INTO metadata VALUES('schema_version', '1');
        INSERT INTO repositories VALUES(
            'game', 'https://example.invalid/game', 'abc123', 0, 'dev_only', 1, 1);
        INSERT INTO functions VALUES(
            1, 'game', 'src/shared.c', 12, 'Shared', 9, 'bodyhash', X'', '[]', '[]');
    """)
    conn.commit()
    conn.close()


def test_receipt_records_hashes_and_cannot_promote_itself(tmp_path):
    index = tmp_path / "corpus.sqlite"
    _index(index)
    target = tmp_path / "target.json"
    candidate = tmp_path / "candidate.json"
    _evidence(target, "80001000", "same")
    _evidence(candidate, "00010000", "same")
    artifact = tmp_path / "candidate.o"
    artifact.write_bytes(b"compiled object")

    hypothesis = n64_provenance.build_hypothesis(
        index=index, target_evidence=target, candidate_evidence=candidate,
        target_function="Shared", repo_id="game", source_path="src/shared.c",
        source_function="Shared", compiled_artifact=artifact,
        discovery_similarity=0.9)
    rendered = json.dumps(hypothesis)
    assert "compiled object" not in rendered
    assert hypothesis["corpus_source"]["source_body_stored"] is False
    assert hypothesis["ghidra"]["comparison"]["semantic"][
        "provenance_rank_score"] == pytest.approx(1.0)
    assert hypothesis["promotion"]["target_candidate_compiled"] is False
    assert hypothesis["promotion"]["target_oracle_exact"] is False
    assert hypothesis["promotion"]["status"] == \
        "provenance_supported_not_promoted"


def test_registry_replaces_same_receipt_and_declares_policy(tmp_path):
    index = tmp_path / "corpus.sqlite"
    _index(index)
    hypothesis = {"hypothesis_id": "one", "target_function": "Shared"}
    first = n64_provenance.update_registry(
        tmp_path / "missing.json", hypothesis, index=index)
    registry = tmp_path / "registry.json"
    n64_provenance.write_registry(registry, first)
    second = n64_provenance.update_registry(registry, hypothesis, index=index)
    assert len(second["hypotheses"]) == 1
    assert second["policy"]["source_bodies_stored"] is False
    assert second["policy"]["source_in_solver_prompt"] is False


def test_relocated_oracle_can_promote_only_with_all_boolean_gates(tmp_path):
    index = tmp_path / "corpus.sqlite"
    _index(index)
    target = tmp_path / "target.json"
    candidate = tmp_path / "candidate.json"
    _evidence(target, "80001000", "same")
    _evidence(candidate, "00010000", "same")
    artifact = tmp_path / "candidate.o"
    artifact.write_bytes(b"compiled object")
    oracle = tmp_path / "oracle.json"
    oracle.write_text(json.dumps({
        "schema_version": 1,
        "kind": "mips_relocated_function_oracle_receipt",
        "compiled": True,
        "oracle_tested": True,
        "exact": True,
        "status": "relocated_text_and_data_exact",
        "candidate": {"sha256": n64_provenance._digest(artifact)},
        "relocated_text": {"equal": True},
        "data_sections": [{"equal": True}],
    }), encoding="utf-8")
    hypothesis = n64_provenance.build_hypothesis(
        index=index, target_evidence=target, candidate_evidence=candidate,
        target_function="Shared", repo_id="game", source_path="src/shared.c",
        source_function="Shared", compiled_artifact=artifact,
        oracle_receipt=oracle)
    assert hypothesis["promotion"]["target_oracle_exact"] is True
    assert hypothesis["promotion"]["status"] == \
        "oracle_promoted_relocated_exact"

    bad = json.loads(oracle.read_text())
    bad["compiled"] = False
    oracle.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="compiled gate"):
        n64_provenance.build_hypothesis(
            index=index, target_evidence=target, candidate_evidence=candidate,
            target_function="Shared", repo_id="game",
            source_path="src/shared.c", source_function="Shared",
            compiled_artifact=artifact, oracle_receipt=oracle)


def test_history_loader_requires_a_bounded_target_record(tmp_path):
    history = tmp_path / "history.json"
    history.write_text(json.dumps({
        "schema_version": 1,
        "kind": "n64_shared_function_history",
        "records": {"Shared": {
            "public_history": [{"commit": "public"}],
            "target_history": [{"commit": "target"}],
            "interpretation": "Shared lineage is proven; copying is not.",
        }},
    }), encoding="utf-8")
    result = n64_provenance.load_history(history, "Shared")
    assert result["public_history"][0]["commit"] == "public"
    assert "copying is not" in result["interpretation"]
    with pytest.raises(ValueError, match="no record"):
        n64_provenance.load_history(history, "Missing")
