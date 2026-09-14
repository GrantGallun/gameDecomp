"""Checks evidence selection, independent of model output shape."""
import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = (Path(__file__).resolve().parents[1] / "eval" / "experiments" /
         "campaign-gap-audit" / "repair_output_context.py")
_SPEC = importlib.util.spec_from_file_location("repair_output_context", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_feedback = _MODULE.build_feedback


def test_compact_keeps_cause_contract_and_uncertainty_without_mutating():
    report = {
        "status": "observed_failure", "authoritative": False,
        "debt": ["unknown callee side effects"],
        "future_uncertainty": {"note": "missing branch outcome"},
        "call_contracts": {"callee": "assumed arity"},
        "operation_gradient": "cross-case unrelated tree",
        "feedback": [
            {"input": {"name": "first"}, "reasons": ["wrong write"],
             "causal_feedback": "target lhu; candidate lh; matched prefix 2"},
            {"input": {"name": "second"}, "causal_feedback": "other cause"},
        ],
    }
    before = copy.deepcopy(report)
    args = dict(semantic_report=report, source="void f(void) {}",
                target_assembly="lhu v0,0(a0)",
                header_context={"typedef": "unsigned short U16"},
                abi={"arity": 1})
    compact = build_feedback(**args, mode="compact")
    full = build_feedback(**args, mode="full")
    for essential in ("target lhu; candidate lh", "matched prefix 2", "wrong write",
                      "unknown callee side effects", "missing branch outcome",
                      "assumed arity", '"authoritative": false',
                      "unsigned short U16", '"arity": 1', "lhu v0,0(a0)"):
        assert essential in compact
    assert "other cause" not in compact
    assert "cross-case unrelated tree" not in compact
    assert "other cause" in full and "cross-case unrelated tree" in full
    assert report == before
    assert "Return JSON" not in compact and "edits" not in compact


def test_no_causal_counterexample_falls_back_without_discarding_evidence():
    report = {"feedback": [], "operation_gradient": "unresolved evidence"}
    assert "unresolved evidence" in build_feedback(
        semantic_report=report, source="", target_assembly="", mode="compact")


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        build_feedback(semantic_report={}, source="", target_assembly="", mode="typo")
