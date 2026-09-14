"""The methodology bank must have one unambiguous conclusion per ID."""

from patterns import hypotheses


def _h(hid="x", status="UNTESTED", evidence=""):
    return hypotheses.Hypothesis(hid, "claim", "source", status, evidence)


def test_committed_bank_passes_integrity_audit():
    assert hypotheses.audit(hypotheses.load()) == []


def test_duplicate_ids_are_rejected_even_when_claim_text_differs():
    issues = hypotheses.audit([_h("same"), _h("same")])
    assert issues == ["duplicate id: same"]


def test_tested_conclusion_requires_evidence():
    assert hypotheses.audit([_h(status="CONFIRMED")]) == [
        "x: CONFIRMED without evidence"]


def test_unknown_status_is_rejected():
    assert hypotheses.audit([_h(status="MAYBE")]) == [
        "x: invalid status MAYBE", "x: MAYBE without evidence"]
