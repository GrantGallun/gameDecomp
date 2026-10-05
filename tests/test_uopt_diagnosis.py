"""Tests for assembly-to-range attribution and register-difference diagnosis.

Fixtures (tests/fixtures/uopt) are real: the patched uopt's traces and ugen's tree dump for two
functions, before and after the edits that made them object-exact on 2026-09-14, with the
target's and each candidate's normalized object dumps. Each diagnosis class used to guide the
search FIRES on the residual it was named after, and an exact candidate diagnoses clean.
"""

from pathlib import Path

import pytest

from solver import uopt_attribution as A
from solver import uopt_diagnosis as D

FIXTURES = Path(__file__).parent / "fixtures/uopt"


def _case(name, stage):
    read = lambda suffix: (FIXTURES / f"{name}.{suffix}").read_text()
    return (read("target.s"), read(f"{stage}.s"), read(f"{stage}.level5.txt"), read(f"{stage}.level6.txt"),
            read(f"{stage}.ugen.txt"))


def test_roster_icons_before_is_a_selection_decision_on_the_u8_local():
    report = D.diagnose(*_case("updateCharacterSelectRosterIcons", "before"), "updateCharacterSelectRosterIcons")
    assert "declined" not in report and report["non_register"] == 0
    first = report["first"]
    assert first["class"] == "selection" and first["kind"] == "M" and first["offset"] == -3
    assert (first["actual"], first["desired"]) == ("v0", "v1")
    assert D.preferred_families(report)[0] == "truth_test"


def test_slide_in_before_is_a_value_the_target_keeps_in_a_ugen_temporary():
    report = D.diagnose(*_case("updateRaceUiScorePopupSlideIn", "before"), "updateRaceUiScorePopupSlideIn")
    first = report["first"]
    assert first["class"] == "ugen_temp" and first["actual"] == "v1" and first["desired"] == "t8"
    assert D.preferred_families(report)[0] == "typed_reread"


@pytest.mark.parametrize("name", ["updateCharacterSelectRosterIcons", "updateRaceUiScorePopupSlideIn"])
def test_exact_candidates_diagnose_clean(name):
    target, candidate, *_ = _case(name, "exact")
    assert target == candidate
    report = D.diagnose(*_case(name, "exact"), name)
    assert report["wrong_ranges"] == 0 and report["first"] is None and not report["unattributed"]
    assert D.preferred_families(report) == ()


def test_attribution_names_ranges_for_uopt_registers_only():
    _target, candidate, level5, level6, ugen = _case("updateRaceUiScorePopupSlideIn", "before")
    attribution = A.attribute(candidate, level5, level6, ugen, "updateRaceUiScorePopupSlideIn")
    owners = {(reg, lr) for rows in attribution.operands.values() for _p, reg, lr in rows}
    assert ("v1", None) not in owners                       # v1 is a coloured range here
    assert all(lr is None for reg, lr in owners if reg in ("t6", "t7", "t8", "sp", "ra", "at"))


def test_attribution_declines_a_missing_procedure():
    _target, candidate, level5, level6, ugen = _case("updateRaceUiScorePopupSlideIn", "before")
    with pytest.raises(A.Declined):
        A.attribute(candidate, level5, level6, ugen, "someOtherFunction")
    report = D.diagnose(_target, candidate, level5, level6, ugen, "someOtherFunction")
    assert "declined" in report


def test_segment_alignment_skips_elided_jumps_and_accepts_duplicated_epilogues():
    assert A.align(["call", "jump", "ret"], ["call", "ret"]) == [(0, 0, "pair"), (1, 1, "skip"), (2, 1, "pair")]
    assert A.align(["cond", "jump", "ret"], ["cond", "ret", "ret"]) is not None      # jump became an epilogue
    assert A.align(["cond", "ret"], ["cond", "cond", "ret"]) is None                 # never invents a branch


def test_padding_after_the_return_is_stripped():
    asm = "addiu sp,sp,8\njr ra\nnop\nnop\nnop"
    assert A.strip_padding(asm) == "addiu sp,sp,8\njr ra\nnop"


def test_colour_register_mapping_matches_the_uopt_pool():
    assert [A.REGISTER_NAMES[A.colour_register(c)] for c in (1, 2, 3, 6, 7, 12, 13, 14, 21)] == \
        ["v0", "v1", "a0", "a3", "t0", "t5", "t6", "s0", "s7"]
    assert A.REGISTER_NAMES[A.colour_register(22)] == "s8"


def test_traced_compile_declines_without_toolchain_or_recipe(tmp_path):
    workspace, repo = tmp_path / "ws", tmp_path / "repo"
    workspace.mkdir()
    repo.mkdir()
    assert D.traced_compile(workspace, repo, "void f(void) {}\n", tmp_path / "missing-cc", "f") is None
    fake_cc = tmp_path / "cc"
    fake_cc.write_text("")
    assert D.traced_compile(workspace, repo, "void f(void) {}\n", fake_cc, "f") is None     # no recipe anywhere
