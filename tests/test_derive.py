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
    with pytest.raises(derive.Unconfirmed, match="no case reached"):
        derive.require_confirmation(verdict)


def test_an_admission_rule_is_confirmed_on_compiling_not_on_exactness():
    """The two criteria are different achievements and must not be conflated. A rule whose criterion
    is `compiled` is confirmed when a held-out case builds, and its verdict says so."""
    rule = FakeRule(closes=set())
    rule.criterion = "compiled"
    rule.id = "admission-fake"

    def compile_fn(function, source, label):
        # compiles everywhere, exact NOWHERE -- the point is that admission confirms without matches
        return FakeAttempt(compiled=True, exact=False)

    verdict = derive.evaluate(rule, _cases("derived", "heldout"), compile_fn=compile_fn)
    assert verdict.criterion == "compiled"
    assert verdict.exact == []                       # nothing matched
    assert verdict.met == ["derived", "heldout"]     # everything compiled
    assert verdict.is_confirmed
    assert verdict.summary()["note"] == "confirmed on ADMISSION (compiles), not on matches"


def test_an_exact_criterion_rule_does_not_get_confirmed_by_compiling():
    """The inverse guard: a solver rule must not be promoted because its candidates build."""
    rule = FakeRule()                                # criterion defaults to exact
    verdict = derive.evaluate(rule, _cases("derived", "heldout"),
                              compile_fn=lambda f, s, l: FakeAttempt(compiled=True, exact=False))
    assert verdict.compiled == 2
    assert not verdict.is_confirmed
    with pytest.raises(derive.Unconfirmed):
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


# --- the rule whose refutation is the point (2026-09-17) ---

SIBLING_DIFF = """--- target_object_dump_normalized.s
+++ drawRaceSplitscreenSelectOption2Frame_object_dump_normalized.s
@@ -54,8 +54,8 @@
   slti    at,s0,0x10
   bnez    at,3c
   addiu    s2,s2,2
-move    s2,zero
   move    s3,zero
+move    s2,zero
   li    s0,0x80
"""


def test_the_permutation_rule_claims_the_sibling_shape_and_its_own_case():
    """It must CLAIM the class -- the whole point is that fire-then-fail is a result.

    A rule that declined here would say nothing; `evaluate` counts claimed-nothing separately for
    exactly this reason.
    """
    from patterns import rules
    rule = rules.RULES["same-shape-difference-is-a-permutation"]
    case = derive.Case(function="drawRaceSplitscreenSelectOption2Frame", source="", diff=SIBLING_DIFF)
    assert rule.applies(case)
    assert rule.derivation_case == "drawRaceSplitscreenSelectOption2Frame"


def test_the_permutation_rule_predicts_the_owning_pass_on_a_real_body():
    """`predict` must reach `statement_order_rewrites`, ungated -- the pass that owns `ordering`."""
    from patterns import rules
    rule = rules.RULES["same-shape-difference-is-a-permutation"]
    source = ("void f(void) {\n"
              "    s32 tileIndex;\n"
              "    s32 i;\n"
              "    s32 offset;\n"
              "    tileIndex = 0;\n"
              "    for (i = 0; i < 16; i++, tileIndex++) {}\n"
              "    tileIndex = 0;\n"
              "    i = 0x80;\n"
              "    offset = 0;\n"
              "}\n")
    variants = rule.predict(derive.Case(function="f", source=source, diff=SIBLING_DIFF))
    assert variants, "the rule claimed the residual and predicted nothing"
    assert all(v.label.startswith("order:") for v in variants)


def test_the_permutation_rule_is_refused_without_a_held_out_confirmation():
    """The recorded refutation, asserted so the rule cannot be quietly promoted.

    Measured 2026-09-17 on the five 99.936 siblings: 5 claimed, 15 predicted, 15 compiled, 0 exact.
    The harness must refuse it, and the refusal reason must be "nothing reached", not "restatement".
    """
    from patterns import rules
    rule = rules.RULES["same-shape-difference-is-a-permutation"]
    cases = [derive.Case(function=f"f{i}", source="x", diff=SIBLING_DIFF) for i in range(3)]

    class FakeAttempt:
        compiled = True
        exact = False

    verdict = derive.evaluate(rule, cases, compile_fn=lambda *a: FakeAttempt())
    assert verdict.claimed == ["f0", "f1", "f2"]
    assert verdict.predicted == 0, "the fake body has no adjacent independent statements to permute"
    assert verdict.met == [] and not verdict.is_confirmed
    with pytest.raises(derive.Unconfirmed):
        derive.require_confirmation(verdict)
