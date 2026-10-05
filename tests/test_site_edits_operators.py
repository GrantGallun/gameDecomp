"""The opt-in operator family of solver.site_edits must FIRE on the residual it was written for.

Fixture (tests/fixtures/site_edits_operators.json) is a real compile of a planted single-line edit from
eval/results/edit-capability-20261002: perturbed source, its oracle diff, and the compiler's verified line
records. `answer` is the unperturbed source, which compiled byte-exact. The argument- and statement-swap
fixtures are kept as evidence that the DEFAULT families already reach those answers, which is why no swap
family was added for them.
"""
import json
from pathlib import Path

from solver import c89, site_edits

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "site_edits_operators.json").read_text())


def _edits(case_id, operators=True):
    case = FIXTURES[case_id]
    edits, receipt = site_edits.propose(case["source"], case["function"], case["diff"], case["attribution"],
                                        operators=operators, mined=True)
    return case, edits, receipt


def test_operator_flip_fires_on_wrong_arithmetic_operator():
    # (arg0->unk10 - 3) where the target computes + 3.
    case, edits, _ = _edits("arith_op:initRaceIntroBillboard")
    assert [e for e in edits if e.kind == "operator" and e.apply(case["source"]) == case["answer"]]


def test_operator_family_is_off_by_default():
    _case, edits, _ = _edits("arith_op:initRaceIntroBillboard", operators=False)
    assert not [e for e in edits if e.kind == "operator"]


def _tokens(text):
    return "".join(text.split())


def test_default_families_already_reach_argument_and_statement_swaps():
    # Why no argswap/stmtswap family exists: the default proposals already contain the answer
    # (the mined rule respells `f(a, b)` as `f( b , a )`, so compare token streams).
    for case_id in ("arg_swap:updateRaceUiTrickPrizePayoutWaitForConfirm",
                    "stmt_swap:initRaceSplitscreenSelectCornerSprites"):
        case, edits, _ = _edits(case_id, operators=False)
        assert any(_tokens(e.apply(case["source"])) == _tokens(case["answer"]) for e in edits), case_id


def _line_edits(text):
    source = text + "\n"
    return site_edits._operator_edits(source, c89._mask(source), 1, 0, len(source))


def test_operator_flip_declines_on_unary_address_member_and_shift():
    for text in ("    x = -0x38;", "    f(&sp1C, 0);", "    y = arg0->unk4;", "    z = a << 2;",
                 "    if (a && b) {", "    a -= 3;", "    p = *q;"):
        assert not _line_edits(text), text


def test_operator_flip_offers_both_neighbours_of_a_relational():
    assert {e.label for e in _line_edits("    if (a->b < 0x21) {")} == {"< -> <=", "< -> >"}
