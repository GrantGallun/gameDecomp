"""Tests for the optional, resource-capped Ghidra sidecar."""

import json
from pathlib import Path

import pytest

from tools.ghidra_sidecar import (
    Budget,
    _assert_forbidden_names_absent,
    _build_headless_args,
    _validated_cached_project,
)


def test_budget_defaults_are_deliberately_small():
    budget = Budget()
    budget.validate()
    assert budget.heap_mb == 512
    assert budget.cpu == 1
    assert budget.analysis_seconds == 120
    assert budget.tree_rss_mb == 768


@pytest.mark.parametrize(
    "budget",
    [
        Budget(heap_mb=2048),
        Budget(cpu=4),
        Budget(analysis_seconds=600),
        Budget(wall_seconds=900),
        Budget(tree_rss_mb=2048),
    ],
)
def test_budget_rejects_accidental_unbounded_runs(budget):
    with pytest.raises(ValueError):
        budget.validate()


def test_first_run_imports_but_cached_run_disables_analysis(tmp_path):
    budget = Budget()
    project = tmp_path / "projects"
    input_elf = tmp_path / "sbk1-stripped.elf"
    output = tmp_path / "evidence.json"

    initial = _build_headless_args(
        project, "sbk1", input_elf, "0x80002DA0", output, budget, False)
    cached = _build_headless_args(
        project, "sbk1", input_elf, "0x80002DA0", output, budget, True)

    assert "-import" in initial
    assert "-analysisTimeoutPerFile" in initial
    assert "-noanalysis" not in initial
    assert "-process" in cached
    assert "-noanalysis" in cached
    assert "-readOnly" in cached
    assert "-analysisTimeoutPerFile" not in cached


def test_forbidden_symbol_check_is_byte_based(tmp_path):
    clean = tmp_path / "clean.elf"
    clean.write_bytes(b"binary evidence only")
    _assert_forbidden_names_absent(clean, ["secretFunction"])

    contaminated = tmp_path / "contaminated.elf"
    contaminated.write_bytes(b"prefix secretFunction suffix")
    with pytest.raises(ValueError, match="secretFunction"):
        _assert_forbidden_names_absent(contaminated, ["secretFunction"])


def test_project_cache_is_bound_to_input_digest(tmp_path):
    projects = tmp_path / "projects"
    outputs = tmp_path / "output"
    projects.mkdir()
    outputs.mkdir()
    (projects / "sbk1.gpr").write_text("project")
    (projects / "sbk1.input.sha256").write_text("abc123\n")

    assert _validated_cached_project(projects, "sbk1", outputs, "abc123")
    with pytest.raises(RuntimeError, match="not def456"):
        _validated_cached_project(projects, "sbk1", outputs, "def456")


def test_legacy_cache_requires_a_proving_import_receipt(tmp_path):
    projects = tmp_path / "projects"
    outputs = tmp_path / "output"
    projects.mkdir()
    outputs.mkdir()
    (projects / "sbk1.gpr").write_text("project")

    with pytest.raises(RuntimeError, match="no input-digest marker"):
        _validated_cached_project(projects, "sbk1", outputs, "abc123")

    (outputs / "first.receipt.json").write_text(json.dumps({
        "project": str(projects / "sbk1"),
        "input_sha256": "abc123",
        "used_cached_project": False,
        "return_code": 0,
        "output_exists": True,
    }))
    assert _validated_cached_project(projects, "sbk1", outputs, "abc123")
    assert (projects / "sbk1.input.sha256").read_text().strip() == "abc123"
