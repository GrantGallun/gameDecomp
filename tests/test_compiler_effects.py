import copy

import pytest

from solver import compiler_effects as effects, local_web_merge, regalloc_mutations


INLINE = '''void f(int arg) {
    int temporary = arg + 4;
    consume(temporary);
    consume(temporary);
}
'''
MERGE = '''void f(int condition, void *value) {
    void **left;
    void **right;
    if (condition) {
        left = value;
        use(left);
    } else {
        right = value;
        use(right);
    }
}
'''


def feature(family="pure_inline", value=1):
    return {"family": family, "numeric": {"removed_locals": value}}


def row(group, after, *, compiled=True, value=1):
    return {"group": group, "features": feature(value=value),
            "before": {"instructions": 4., "frame_bytes": 16.},
            "after": after, "compiled": compiled}


def test_effect_state_distinguishes_order_from_register_histogram():
    first = "li t0,0x80\nbnez t7,3c\nli v1,0x80\njr ra\nnop\n"
    second = "li v1,0x80\nbnez t7,3c\nli t0,0x80\njr ra\nnop\n"
    a, b = effects.assembly_state(first), effects.assembly_state(second)
    assert {k: v for k, v in a.items() if k.startswith("register:")} == {
        k: v for k, v in b.items() if k.startswith("register:")}
    assert a != b
    assert effects.state_distance(a, b) > 0
    # Branch addresses and symbol spellings are not transferable features.
    assert a == effects.assembly_state(first.replace("3c", "another_label"))


def test_stack_frame_and_slot_effect_are_observable():
    state = effects.assembly_state("addiu sp,sp,-0x20\nsw s0,0x18(sp)\nlw s0,0x18(sp)\n")
    assert state["frame_bytes"] == 32
    assert state["stack:sw:24"] == 1
    assert state["stack:lw:24"] == 1


def test_pure_inline_forecasts_removed_local_and_call_crossing_without_color_claim():
    _, family, child = next(regalloc_mutations.pure_local_inlines(INLINE, "f"))
    found = effects.features(INLINE, child, "f", family, "", None, "")
    assert found["numeric"]["removed_locals"] == 1
    assert found["numeric"]["removed_local_reads"] >= 2
    assert found["numeric"]["removed_local_crossed_calls"] >= 1
    assert found["evidence"]["source_mapping"] == "unknown"
    assert any(h["effect"] == "named_local_removed" for h in found["hypotheses"])
    assert not any("color" in h["effect"] for h in found["hypotheses"])


def test_merge_generator_positive_has_removal_forecast():
    _, family, child = next(local_web_merge.variants(MERGE, "f"))
    result = effects.features(MERGE, child, "f", family, "", None, "")
    assert result["numeric"]["removed_locals"] == 1
    assert any(h["effect"] == "branch_local_identity_merged" for h in result["hypotheses"])


def test_stale_mapping_is_unknown_and_local_renames_do_not_change_features():
    _, family, child = next(regalloc_mutations.pure_local_inlines(INLINE, "f"))
    stale = {"status": "verified", "source_sha256": "wrong", "instructions": [
        {"candidate_line": 3, "normalized_line": 0, "instruction": "addu v0,a0,t0"}]}
    a = effects.features(INLINE, child, "f", family, "", stale, "addu v0,a0,t0")
    b = effects.features(INLINE.replace("temporary", "other"), child, "f", family,
                         "", None, "addu v0,a0,t0")
    assert a["numeric"] == b["numeric"]
    assert a["evidence"]["source_mapping"] == "unknown"


def test_training_rejects_evaluation_groups_and_unknown_compile_is_not_zero_delta():
    with pytest.raises(ValueError, match="development"):
        effects.fit([row("heldout", {"instructions": 3.})], ["development"])
    model = effects.fit([row("a", None, compiled=False), row("b", None, compiled=False)], ["a", "b"])
    prediction = effects.predict(model, feature(), {"instructions": 4.}, {"instructions": 3.})
    assert prediction["status"] == "abstain"
    assert prediction["delta"] == {}
    assert prediction["compile_failures"] == 2


def test_neighbor_model_predicts_concrete_delta_using_distinct_group_support():
    after = {"instructions": 3., "frame_bytes": 12.}
    training = [row("a", after), row("b", after)]
    model = effects.fit(training, ["b", "a"])
    before = {"instructions": 4., "frame_bytes": 16.}
    prediction = effects.predict(model, feature(), before, after)
    assert prediction["status"] == "predicted"
    assert prediction["delta"]["frame_bytes"] == pytest.approx(-4)
    assert prediction["delta"]["instructions"] == pytest.approx(-1)
    assert prediction["estimated_progress"] > 0
    assert prediction["support_groups"] == 2
    assert effects.fit(list(reversed(training)), ["a", "b"]) == model
    single = effects.fit([row("a", after)] * 20, ["a"])
    assert effects.predict(single, feature(), before, after)["status"] == "abstain"


def test_name_score_and_future_metadata_are_not_model_inputs():
    a = row("a", {"instructions": 3., "frame_bytes": 12.})
    b = row("b", a["after"])
    first = effects.fit([a, b], ["a", "b"])
    a["features"]["evidence"] = {"function": "secret", "future_score": 100}
    a["score"] = 100
    assert effects.fit([a, b], ["a", "b"]) == first


def test_rank_preserves_unknowns_and_exploration_and_does_not_mutate_input():
    model = effects.fit([], [])
    proposals = [{"ordinal": i, "features": feature()} for i in range(9)]
    original = copy.deepcopy(proposals)
    ranked = effects.rank(model, proposals, {}, {})
    assert [p["ordinal"] for p in ranked] == list(range(9))
    assert proposals == original
    assert len({p["ordinal"] for p in ranked}) == len(proposals)


def test_nonfinite_training_values_rejected():
    broken = row("a", {"instructions": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        effects.fit([broken], ["a"])


def test_compiler_domains_cannot_supply_each_others_evidence():
    training = [row("a", {"instructions": 3.}), row("b", {"instructions": 3.})]
    for item in training:
        item["features"]["domain"] = "ido-O2"
    model = effects.fit(training, ["a", "b"])
    other = {**feature(), "domain": "ido-O1"}
    assert effects.predict(model, other, {}, {})["status"] == "abstain"


def test_new_and_removed_state_dimensions_contribute_to_error():
    before = effects.assembly_state("lw v0,4(sp)\njr ra\nnop")
    after = effects.assembly_state("lw t0,8(sp)\njr ra\nnop")
    assert effects.state_distance(before, after) >= 2
    assert effects.state_distance(before, after) == effects.state_distance(after, before)
