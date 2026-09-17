"""The harness must refuse to confirm a rule on the strength of its own motivating case.

That is the whole reason `patterns/derive.py` exists: a rule that only re-predicts what was observed
is a restatement, and the store-order rule's held-out failure in round 2 showed the difference is not
academic. These tests drive the harness with a FAKE compiler, so the gate logic is exercised without
needing IDO -- the real oracle is used by the CLI, not by these.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import derive                                           # noqa: E402


@dataclass
class FakeAttempt:
    compiled: bool
    exact: bool


@dataclass
class FakeRule:
    id: str = "fake"
    derivation_case: str | None = "derived"
    closes: set = field(default_factory=set)
    claims_all: bool = True

    def applies(self, case):
        return self.claims_all

    def predict(self, case):
        return (derive.Variant(label="v", source=case.source),)


def _cases(*names):
    return [derive.Case(function=n, source="x", diff="", receipt=1) for n in names]


def _compiler(closes):
    def compile_fn(function, source, label):
        return FakeAttempt(compiled=True, exact=function in closes)
    return compile_fn


def test_a_rule_that_closes_only_its_derivation_case_is_not_confirmed():
    rule = FakeRule(closes={"derived"})
    verdict = derive.evaluate(rule, _cases("derived", "other"), compile_fn=_compiler(rule.closes))
    assert verdict.exact == ["derived"]
    assert verdict.beyond_derivation == []
    assert not verdict.is_confirmed


def test_require_confirmation_refuses_a_restatement():
    rule = FakeRule(closes={"derived"})
    verdict = derive.evaluate(rule, _cases("derived", "other"), compile_fn=_compiler(rule.closes))
    with pytest.raises(derive.Unconfirmed, match="restatement"):
        derive.require_confirmation(verdict)


def test_a_rule_that_closes_a_held_out_case_is_confirmed():
    rule = FakeRule(closes={"derived", "heldout"})
    verdict = derive.evaluate(rule, _cases("derived", "heldout"), compile_fn=_compiler(rule.closes))
    assert verdict.beyond_derivation == ["heldout"]
    assert verdict.is_confirmed
    derive.require_confirmation(verdict)          # must not raise


def test_closing_a_held_out_case_alone_still_confirms():
    """The derivation case is the one it came from, not a case it must also close."""
    rule = FakeRule(derivation_case="derived", closes={"heldout"})
    verdict = derive.evaluate(rule, _cases("derived", "heldout"), compile_fn=_compiler(rule.closes))
    assert verdict.is_confirmed


def test_claiming_a_residual_and_predicting_nothing_is_not_a_decline():
    """The silent-decline shape: a rule that claims the residual and produces no candidate looks
    identical to one that never applied, unless the harness counts them apart."""
    rule = FakeRule()
    rule.predict = lambda case: ()
    verdict = derive.evaluate(rule, _cases("a", "b"), compile_fn=_compiler(set()))
    assert verdict.claimed == ["a", "b"]
    assert verdict.declined == []
    assert verdict.predicted == 0
    assert all(row["status"] == "claimed-nothing" for row in verdict.per_case)


def test_a_rule_that_never_applies_declines_and_confirms_nothing():
    rule = FakeRule(claims_all=False)
    verdict = derive.evaluate(rule, _cases("a"), compile_fn=_compiler(set()))
    assert verdict.declined == ["a"]
    assert not verdict.is_confirmed
    with pytest.raises(derive.Unconfirmed, match="no case closed"):
        derive.require_confirmation(verdict)


def test_the_registry_exposes_the_store_rule_with_its_scope_declared():
    from patterns import rules
    rule = rules.RULES["ordering-store-reorder"]
    assert rule.derivation_case == "Fstop"
    # It must refuse a residual whose moved instruction is a LOAD -- that is the held-out refutation,
    # encoded as a claim the rule makes about itself.
    load_case = derive.Case(function="updateRacePlayerMode16AerialTrick", source="", diff=(
        "--- t\n+++ c\n@@ -35,6 +35,6 @@\n lw t5,0x1c(s0)\n-lw v0,0x44(s0)\n lw t6,0x40(s0)\n"
        "+lw v0,0x44(s0)\n lw t8,0x20(s0)\n"))
    assert not rule.applies(load_case)


def test_the_store_rule_predicts_on_the_fstop_shape():
    from patterns import rules
    rule = rules.RULES["ordering-store-reorder"]
    source = ("void f(void *a0) {\n"
              "    a0->f14 = 0;\n"
              "    a0->f54 = 0;\n"
              "    a0->f60 = 0;\n"
              "}\n")
    diff = ("--- t\n+++ c\n@@ -1,4 +1,4 @@\n"
            "+sw    zero,0x14(a0)\n"
            " sw    zero,0x54(a0)\n"
            " sw    zero,0x60(a0)\n"
            "-sw    zero,0x14(a0)\n"
            " jr    ra\n")
    variants = rule.predict(derive.Case(function="f", source=source, diff=diff))
    assert variants, "the rule declined on its own motivating shape"
    reordered = variants[0].source.splitlines()
    # target order is 0x54, 0x60, 0x14 -> statements f54, f60, f14
    assert [l.strip().split("->")[1].split(" ")[0] for l in reordered[1:4]] \
        == ["f54", "f60", "f14"]
