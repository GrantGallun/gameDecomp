"""Synthetic MIPS mechanism tests for the opt-in research panels."""

import hashlib

import pytest

from eval.research_suite.counterexamples import compare_panels


TARGET = "lw t0,0(a0)\nsw t0,4(a0)\njr ra\nnop"
MASKED = "lw t0,0(a0)\nandi t0,t0,1\nsw t0,4(a0)\njr ra\nnop"


def case(name, value):
    return {"name": name, "seed": 7, "player_writes": [[0, 4, value]]}


def candidates():
    return [
        {"id": "masked", "source": "masked C", "assembly": MASKED},
        {"id": "exact", "source": "exact C", "assembly": TARGET},
    ]


def test_extra_case_falsifies_a_base_pass_and_refreshes_every_candidate():
    result = compare_panels("copy", TARGET, candidates(), [case("one", 1)], [case("two", 2)])

    assert result["fixed"]["ranking"] == ["masked", "exact"]
    assert result["accumulating"]["ranking"] == ["exact", "masked"]
    assert result["fixed"]["candidates"]["masked"]["passed"] == 1
    assert result["accumulating"]["candidates"]["masked"]["failed"] == 1
    assert result["accumulating"]["candidates"]["exact"]["checked"] == 2
    assert result["base_pass_extra_checked"] == 2
    assert result["base_pass_extra_falsified"] == 1
    assert result["fixed"]["version"] != result["accumulating"]["version"]
    assert {row["panel_version"] for row in result["accumulating"]["candidates"].values()} == {
        result["accumulating"]["version"]}
    # A later candidate batch can replay the retained inputs without guessing
    # them back from observation names.
    replay = compare_panels('copy', TARGET, candidates(), result['retained_cases'], [])
    assert replay['fixed']['ranking'] == result['accumulating']['ranking']
    assert replay['fixed']['version'] == result['accumulating']['version']


def test_case_set_version_and_source_identity_change_with_content():
    first = compare_panels("copy", TARGET, candidates(), [case("one", 1)], [])
    newer = compare_panels("copy", TARGET, candidates(), [case("one", 1)], [case("two", 2)])
    renamed = candidates()
    renamed[0]["source"] = "other source with same assembly"
    source_change = compare_panels("copy", TARGET, renamed, [case("one", 1)], [])

    assert first["accumulating"]["version"] != newer["accumulating"]["version"]
    assert first["fixed"]["version"] == newer["fixed"]["version"]
    assert first["accumulating"]["candidates"]["masked"]["source_sha256"] != (
        source_change["accumulating"]["candidates"]["masked"]["source_sha256"])
    assert first["accumulating"]["candidates"]["masked"]["assembly_sha256"] == (
        source_change["accumulating"]["candidates"]["masked"]["assembly_sha256"])


def test_supplied_stale_source_digest_is_rejected():
    rows = candidates()
    rows[0]["source_sha256"] = hashlib.sha256(b"older C").hexdigest()
    with pytest.raises(ValueError, match="source_sha256"):
        compare_panels("copy", TARGET, rows, [case("one", 1)], [])


@pytest.mark.parametrize("target", [
    "lw t0,0(zero)\njr ra\nnop",  # unmapped read faults
    "move s0,a0\njr ra\nnop",  # callee-saved ABI debt
    "unsupported_op t0,t0\njr ra\nnop",  # unsupported instruction
])
def test_unadmitted_target_state_does_not_become_candidate_failure(target):
    result = compare_panels("copy", target, candidates(), [case("one", 1)], [])
    assert result["admitted_base"] == 0
    assert result["rejected_cases"][0]["status"] == "target_inconclusive"
    assert result["fixed"]["candidates"]["masked"]["checked"] == 0
    assert result["fixed"]["candidates"]["masked"]["failed"] == 0


def test_empty_inputs_and_budgets_are_explicit():
    empty = compare_panels("copy", TARGET, [], [], [])
    assert empty["fixed"]["ranking"] == []
    assert empty["accumulating"]["ranking"] == []
    assert empty["admitted_base"] == empty["admitted_extra"] == 0

    with pytest.raises(ValueError, match="extra case budget"):
        compare_panels("copy", TARGET, [], [], [case(str(i), i) for i in range(9)])
    with pytest.raises(ValueError, match="base case budget"):
        compare_panels("copy", TARGET, [], [case(str(i), i) for i in range(65)], [])


def test_extra_only_panel_does_not_claim_base_pass_exposure():
    result = compare_panels("copy", TARGET, candidates(), [], [case("two", 2)])
    assert result["admitted_extra"] == 1
    assert result["base_pass_extra_checked"] == 0
    assert result["base_pass_extra_falsified"] == 0


def test_candidate_unsupported_execution_is_inconclusive_not_failure():
    rows = [{"id": "unknown", "source": "unknown C",
             "assembly": "unsupported_op t0,t0\njr ra\nnop"}]
    result = compare_panels("copy", TARGET, rows, [case("one", 1)], [])
    receipt = result["fixed"]["candidates"]["unknown"]
    assert receipt["inconclusive"] == 1
    assert receipt["checked"] == 0
    assert receipt["failed"] == 0


def test_candidate_and_case_identity_budgets_are_enforced():
    with pytest.raises(ValueError, match="candidate budget"):
        compare_panels("copy", TARGET, [{"id": str(i), "source": "C", "assembly": TARGET}
                                         for i in range(33)], [], [])
    with pytest.raises(ValueError, match="duplicate candidate id"):
        compare_panels("copy", TARGET, candidates() * 2, [], [])
    with pytest.raises(ValueError, match="duplicate case name"):
        compare_panels("copy", TARGET, [], [case("same", 1)], [case("same", 2)])
