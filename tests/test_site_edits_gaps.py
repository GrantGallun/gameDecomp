"""The opt-in `gaps=True` lane of solver.site_edits, and the missing-statement model lane, must FIRE on the
residuals they were written for (CLAUDE.md, the fifth rule), and stay off by default.

Fixtures (tests/fixtures/site_edits_operators.json) are real compiles of planted single-line edits from
eval/results/edit-capability-20261002 (development set only): perturbed source, oracle diff, verified compiler
line records, and `answer`, the unperturbed source that compiled byte-exact.
"""
import json
from pathlib import Path

import pytest

from solver import missing_statement_llm, missing_store, next_use_temp, rewrite_library, site_edits

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "site_edits_operators.json").read_text())


def _tokens(text):
    return "".join(text.split())


def _edits(case_id, gaps=True):
    case = FIXTURES[case_id]
    edits, _ = site_edits.propose(case["source"], case["function"], case["diff"], case["attribution"],
                                  mined=True, gaps=gaps)
    return case, edits


def _reaches(case_id, kind_prefix, gaps=True):
    case, edits = _edits(case_id, gaps)
    return [e for e in edits if e.kind.startswith(kind_prefix)
            and _tokens(e.apply(case["source"])) == _tokens(case["answer"])]


@pytest.mark.parametrize("case_id", ["drop_stmt:initControllerPakRaceRecordSaveExitMessage",
                                     "drop_stmt:updateRaceItemProjectileTrailEffect"])
def test_missing_store_fires_on_dropped_store(case_id):
    # `li t7,-0x38 / sh t7,0x1a(a0)` and `lh / addiu -0x30 / sh` with no candidate counterpart.
    assert _reaches(case_id, "shape:missing_store")


def test_missing_store_reads_value_and_compound_update_from_target():
    stores = missing_store.missing(FIXTURES["drop_stmt:updateRaceItemProjectileTrailEffect"]["diff"])
    assert {"op": "sh", "offset": 0x30, "arg": 0, "value": ("add", -0x30)} in stores
    stores = missing_store.missing(FIXTURES["drop_stmt:initControllerPakRaceRecordSaveExitMessage"]["diff"])
    assert {"op": "sh", "offset": 0x1A, "arg": 0, "value": ("set", -0x38)} in stores


def test_missing_store_declines_without_a_missing_store():
    assert missing_store.missing(FIXTURES["arith_op:initRaceIntroBillboard"]["diff"]) == []


def test_next_use_temp_fires_on_call_result_temporary():
    case = FIXTURES["temp_return:osSpTaskYield"]
    children = [child for _label, child in next_use_temp.variants(case["source"], case["function"], case["diff"])]
    assert any(_tokens(c) == _tokens(case["answer"]) for c in children)


def test_next_use_temp_is_gated_on_extra_stack_traffic():
    case = FIXTURES["temp_return:osSpTaskYield"]
    assert not list(next_use_temp.variants(case["source"], case["function"], "@@ -1 +1 @@\n-nop\n+nop\n"))


def test_next_use_temp_declines_when_the_temporary_is_read_twice():
    source = "s32 f(void) {\n    s32 t;\n    t = g();\n    h(t);\n    return t;\n}\n"
    assert not list(next_use_temp.variants(source, "f", "", gated=False))


def test_empty_arm_drop_fires_on_inverted_if():
    assert _reaches("if_invert:updateRacePlayerRecoverySparkle", "shape:empty_arm")


def test_empty_arm_drop_declines_when_both_arms_have_code():
    source = "void f(s32 a) {\n    if (a) {\n        g();\n    } else {\n        h();\n    }\n}\n"
    assert rewrite_library.empty_arm_drops(source, "f") == []


def test_commutative_lane_fires_on_operand_load_order():
    assert _reaches("commute:drawEndingCreditsCharacterLoopingSparkle", "commutative")


def test_widening_hint_ranks_the_widening_declaration_first():
    case, edits = _edits("decl_width:alLoadNew")
    assert site_edits.widening_hint(case["diff"]) == {"s16"}
    decls = [e for e in edits if e.kind == "decl"]
    assert decls[0].label == "temp_v0: s16 -> s32"


def test_gap_lanes_are_off_by_default():
    for case_id in FIXTURES:
        _case, edits = _edits(case_id, gaps=False)
        assert not [e for e in edits if e.kind in ("shape:missing_store", "shape:next_use_temp",
                                                   "shape:empty_arm", "commutative")], case_id


RULE_FOR = {
    "drop_stmt:initControllerPakRaceRecordSaveExitMessage": "target-only-store-is-a-missing-statement",
    "drop_stmt:updateRaceItemProjectileTrailEffect": "target-only-store-is-a-missing-statement",
    "temp_return:osSpTaskYield": "ido53-o1-result-temporary-has-a-stack-home",
    "decl_width:alLoadNew": "candidate-only-extension-widens-a-declaration",
    "commute:drawEndingCreditsCharacterLoopingSparkle": "ido53-commutative-operand-materialisation-order",
    "stmt_swap:initRaceSplitscreenSelectCornerSprites": "ido53-adjacent-store-order-is-preserved",
}


@pytest.mark.parametrize("case_id", sorted(RULE_FOR))
def test_residual_rules_names_the_rule_behind_the_residual(case_id):
    from solver import principles
    named = [pid for pid, _reason in principles.residual_rules(FIXTURES[case_id]["diff"])]
    assert RULE_FOR[case_id] in named, named


def test_residual_rules_cite_confirmed_catalog_entries_only():
    from patterns.catalog import CATALOG
    from solver import principles
    for case in FIXTURES.values():
        for pid, _reason in principles.residual_rules(case["diff"]):
            assert not CATALOG[pid].is_hypothesis, pid


def test_residual_rules_never_claim_the_empty_arm_rule():
    # Its branch signature is shared with other causes (catalog: ido53-empty-arm-layout-conditions).
    from solver import principles
    named = [pid for pid, _r in principles.residual_rules(FIXTURES["if_invert:updateRacePlayerRecoverySparkle"]["diff"])]
    assert "ido53-empty-arm-layout-conditions" not in named


def test_probe_receipt_supports_the_catalog_conditions():
    # The catalog's stated conditions must match the recorded probe verdicts (eval/rule_probes.py).
    receipt = json.loads((Path(__file__).parents[1] / "eval/results/rule-probes-20261002/probes.json").read_text())
    verdict = {(f, r["context"], r["opt"]): r["verdict"] for f, rows in receipt["families"].items() for r in rows}
    assert all(verdict[("return_temp", c, "O2")] == "same" for c in
               ("call value", "arithmetic value", "field load", "call then more code"))
    assert all(verdict[("return_temp", c, "O1")] == "differ" for c in
               ("call value", "arithmetic value", "field load", "call then more code"))
    assert verdict[("commute_operands", "field + param", "O2")] == "same"
    assert verdict[("commute_operands", "two field loads, s32", "O2")] == "differ"
    assert verdict[("mirror_comparison", "field vs field", "O2")] == "same"
    assert verdict[("mirror_comparison", "field vs field", "O1")] == "differ"
    assert all(v == "differ" for (f, _c, _o), v in verdict.items() if f == "store_order")
    assert verdict[("empty_then_arm", "global test, call body", "O2")] == "same"
    assert verdict[("empty_then_arm", "nested if without return, code after", "O2")] == "differ"


def _stub(answer):
    def generate(*_args, **_kwargs):
        return json.dumps(answer), {}
    return generate


def test_missing_statement_llm_inserts_the_proposed_statement():
    case = FIXTURES["drop_stmt:initControllerPakRaceRecordSaveExitMessage"]
    answer_line = "arg0->unk1A = -0x38;"
    _offset, body = missing_statement_llm._definition(case["answer"], case["function"])
    after = next(i for i, l in enumerate(body.split("\n"), 1) if answer_line in l) - 1
    children = [child for _l, child in missing_statement_llm.variants(
        case["source"], case["function"], case["diff"], guard=lambda p: None, endpoint="stub",
        temperatures=(0.2,), generate=_stub({"statements": [{"after_line": after, "c": answer_line}]}))]
    assert any(_tokens(c) == _tokens(case["answer"]) for c in children)


def test_missing_statement_llm_requires_a_contamination_check():
    case = FIXTURES["drop_stmt:initControllerPakRaceRecordSaveExitMessage"]
    with pytest.raises(ValueError):
        list(missing_statement_llm.variants(case["source"], case["function"], case["diff"], endpoint="stub",
                                            generate=_stub({"statements": []})))


def test_missing_statement_llm_declines_without_target_only_rows():
    case = FIXTURES["arg_swap:updateRaceUiTrickPrizePayoutWaitForConfirm"]
    assert missing_statement_llm.prompt(case["source"], case["function"], case["diff"]) is None
