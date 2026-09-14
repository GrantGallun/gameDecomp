from pathlib import Path

import pytest

from solver import mips_differential as differential


CANDIDATE_TAG = (
    "updateRacePlayerMode16AerialTrick_logic_ref_tu_out_1_1788377198458327152"
)


@pytest.mark.grounded
def test_mode16_control_passes_and_audited_false_positive_fails(repo_path: Path):
    workspace = repo_path / "nonmatchings/updateRacePlayerMode16AerialTrick"
    target_path = workspace / "target_object_dump_normalized.s"
    raw_target_path = workspace / "target.s"
    candidate_path = workspace / f"{CANDIDATE_TAG}_object_dump_normalized.s"
    if (not target_path.exists() or not raw_target_path.exists()
            or not candidate_path.exists()):
        pytest.skip("frozen mode-16 assembly artifacts are absent")
    target = target_path.read_text(errors="replace")
    raw = raw_target_path.read_text(errors="replace")
    start = raw.index("glabel updateRacePlayerMode16AerialTrick")
    end = raw.index("endlabel updateRacePlayerMode16AerialTrick", start)
    raw = raw[start:end]
    candidate = candidate_path.read_text(errors="replace")
    cases = differential.mode16_cases()

    control = differential.run_suite(
        target, raw, cases, call_arities=differential.MODE16_CALL_ARITIES)
    treatment = differential.run_suite(
        target, candidate, cases,
        call_arities=differential.MODE16_CALL_ARITIES)

    assert [row.status for row in control] == ["passed"] * len(cases)
    assert [row.status for row in treatment] == ["failed"] * len(cases)
    assert all("clampRacePlayerVectorXZSpeed" in row.first_divergence
               for row in treatment)
    assert all("player+0x40" in row.first_divergence and
               "player+0x1c" in row.first_divergence
               for row in treatment)


@pytest.mark.grounded
def test_mode16_frozen_panel_covers_every_target_instruction_and_edge(
        repo_path: Path):
    target_path = (repo_path / "nonmatchings/updateRacePlayerMode16AerialTrick/"
                   "target_object_dump_normalized.s")
    if not target_path.exists():
        pytest.skip("frozen mode-16 target assembly is absent")
    target = target_path.read_text(errors="replace")
    program = differential.Program.parse("target", target)
    runs = tuple(
        differential.execute_case(
            program, case, call_arities=differential.MODE16_CALL_ARITIES)
        for case in differential.mode16_cases()
    )

    report = differential.coverage_report(program, runs)

    assert report.complete
    assert len(report.covered_instructions) == 104
    assert len(report.conditional_branches) == 9
    assert len(report.covered_branch_edges) == 18
