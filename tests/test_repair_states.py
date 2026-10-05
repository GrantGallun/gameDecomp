"""Repair states, multiple certified children, and the weighting over them.

Each test here corresponds to a bullet in the brief's verification list. The one that matters most is
`test_grouping_by_function_would_be_refused`: the guard has to FIRE on the motivating residual (a
grouping that is really "same function name"), because a loader that grouped by id would look
perfectly healthy from outside and would attach children to parents they were never verified against.
"""
from __future__ import annotations

import pytest

from eval import repair_states as rs


def record(*, task_id="t1", function="f", source="s32 f(void) { return 1; }",
           parent="s32 f(void) { return 0; }", answer=None, exact=True, split="train",
           target="T1", feedback="cfe: Error: line 3", build="cmd1", asm="asm1",
           certificate=None) -> dict:
    """One dataset record in the shape the frozen manifest actually carries."""
    return {
        "task_id": task_id,
        "function": function,
        "split": split,
        "target": {"code_sha256": target, "asm_sha256": target + "-asm"},
        "input": {"assembly": asm,
                  "candidate": parent,
                  "feedback": {"kind": "compiler", "text": feedback}},
        "provenance": {"compiler_recipe": {"command_sha256": build}},
        "child": {"exact": exact, "source_c": answer if answer is not None else source,
                  "certificate": certificate or {"status": "object_sections_exact", "exact": True},
                  "outcome": "verified-repair"},
    }


# --- 2. multiple verified children per state ------------------------------------

def test_two_distinct_certified_children_of_one_state_are_both_kept():
    a = record(task_id="t1", source="s32 f(void) { s32 x; x = 1; return x; }")
    b = record(task_id="t2", source="s32 f(void) { return 1; }")
    states = rs.group_by_state([a, b])
    assert len(states) == 1, "the same conditioning tuple is one state"
    children = rs.verified_children([a, b])
    assert len(children) == 2
    assert {c["task_id"] for c in children} == {"t1", "t2"}


def test_a_child_of_a_different_parent_is_not_attached_to_this_state():
    """Same function, different parent candidate: a different state, and the children stay apart."""
    a = record(task_id="t1", function="f", parent="s32 f(void) { return 0; }")
    b = record(task_id="t2", function="f", parent="s32 f(void) { return 9; }")
    states = rs.group_by_state([a, b])
    assert len(states) == 2, "two parents are two repair states"
    assert all(len(rs.verified_children(m)) == 1 for m in states.values())


def test_differing_target_feedback_or_build_also_separates_states():
    base = record(task_id="t1")
    for field, value in (("target", "T2"), ("feedback", "different error"), ("build", "cmd2"),
                         ("asm", "asm2")):
        other = record(task_id="t2", **{field: value})
        assert len(rs.group_by_state([base, other])) == 2, f"{field} must separate states"


def test_an_unverified_child_is_not_a_positive():
    good = record(task_id="t1")
    bad = record(task_id="t2", exact=False, source="s32 f(void) { return 2; }")
    children = rs.verified_children([good, bad])
    assert [c["task_id"] for c in children] == ["t1"], "compiling is not passing the certificate"


def test_a_child_whose_certificate_rejected_it_is_not_a_positive():
    """The certificate is the only verdict: `.text` equality with a changed callee is not a repair."""
    rejected = record(task_id="t1", exact=False,
                      certificate={"status": "object_sections_differ", "exact": False})
    assert rs.verified_children([rejected]) == []


def test_a_variant_is_joined_on_the_record_and_cannot_cross_to_another_state():
    a = record(task_id="t1", function="f")
    b = record(task_id="t2", function="f", parent="s32 f(void) { return 9; }")
    # Structurally different, not a respelling: a comment-only variant would now collapse to the
    # recorded child and this test would pass for the wrong reason.
    variants = [{"task_id": "t2", "function": "f", "rewrite": "hoist-default",
                 "source": "s32 f(void) { if (1) { return 1; } return 0; }"}]
    states = rs.group_by_state([a, b])
    for members in states.values():
        children = rs.verified_children(members, variants)
        if members[0]["task_id"] == "t1":
            assert len(children) == 1, "a variant verified against t2 may not join t1"
        else:
            assert len(children) == 2


# --- 3. weighting ---------------------------------------------------------------

def test_cosmetic_duplicates_do_not_inflate_diversity_or_weight():
    one = record(task_id="t1", source="s32 f(void) { return 1; }")
    spelled = record(task_id="t2", source="s32 f  ( void )\n{\n    return  1 ;\n}")
    other = record(task_id="t3", function="g", source="s32 g(void) { return 2; }")
    assert len(rs.verified_children([one, spelled])) == 1, "whitespace is not a second approach"
    weights = rs.balanced_weights([one, spelled, other])
    assert sum(weights["t1"].values()) == pytest.approx(0.5)
    assert sum(weights["t3"].values()) == pytest.approx(0.5)
    assert "t2" not in weights, "the cosmetic duplicate carries no weight of its own"


def test_identifier_only_changes_are_cosmetic_but_structural_ones_are_not():
    a = record(task_id="t1", source="s32 f(void) { s32 value; value = 1; return value; }")
    renamed = record(task_id="t2", source="s32 f(void) { s32 total; total = 1; return total; }")
    restructured = record(task_id="t3", source="s32 f(void) { if (1) { return 1; } return 0; }")
    assert len(rs.verified_children([a, renamed])) == 1
    assert len(rs.verified_children([a, renamed, restructured])) == 2


def test_many_children_do_not_disproportionately_weight_one_function():
    # Distinct targets, because two records with the same target AND the same parent ARE one state --
    # that is the implementation being correct, not a convenience for the test.
    many = [record(task_id=f"t{i}", function="f", target=f"F{i}",
                   source=f"s32 f(void) {{ return {i}; }}") for i in range(5)]
    single = [record(task_id="u1", function="g", target="G",
                     source="s32 g(void) { return 42; }")]
    weights = rs.balanced_weights(many + single)
    assert sum(sum(c.values()) for c in weights.values()) == pytest.approx(1.0)
    f_weight = sum(sum(weights[r["task_id"]].values()) for r in many)
    g_weight = sum(weights["u1"].values())
    assert f_weight == pytest.approx(0.5) and g_weight == pytest.approx(0.5)


def test_five_children_of_ONE_state_split_that_state_not_the_dataset():
    """The other half of the same rule: within a state the weight is split, not multiplied."""
    one_state = [record(task_id=f"t{i}", function="f",
                        source=f"s32 f(void) {{ return {i}; }}") for i in range(5)]
    weights = rs.balanced_weights(one_state)
    assert sum(sum(c.values()) for c in weights.values()) == pytest.approx(1.0)
    assert all(v == pytest.approx(0.2) for c in weights.values() for v in c.values())


def test_weight_sums_to_one_per_state_and_the_summary_reports_the_balance():
    records = [record(task_id="t1", function="f", target="F", source="s32 f(void) { return 1; }"),
               record(task_id="t2", function="g", target="G", source="s32 g(void) { return 2; }"),
               record(task_id="t3", function="h", target="H", source="s32 h(void) { return 3; }")]
    weights = rs.balanced_weights(records)
    assert sum(sum(c.values()) for c in weights.values()) == pytest.approx(1.0)
    summary = rs.weight_summary(records)
    assert summary["functions"] == 3 and summary["states"] == 3
    assert summary["total_weight"] == pytest.approx(1.0)
    assert summary["weight_per_function_min"] == pytest.approx(1 / 3)


def test_the_summary_counts_collapsed_cosmetic_duplicates():
    records = [record(task_id="t1", source="s32 f(void) { return 1; }"),
               record(task_id="t2", source="s32 f(void) {\n    return 1;\n}")]
    summary = rs.weight_summary(records)
    assert summary["distinct_children_total"] == 1
    assert summary["cosmetic_duplicates_collapsed"] == 1


# --- 2 (guard). the grouping must not silently be "same function" ---------------

def test_grouping_by_function_would_be_refused():
    """THE MOTIVATING RESIDUAL. A loader that keyed on `function` would produce exactly this and
    look healthy from outside, silently attaching a child to a parent it was never verified on."""
    a = record(task_id="t1", function="f", parent="s32 f(void) { return 0; }")
    b = record(task_id="t2", function="f", parent="s32 f(void) { return 9; }")
    with pytest.raises(ValueError, match="may not be attached to a parent"):
        rs.assert_states_are_not_functions([a, b], {"collapsed-by-function": [a, b]})


def test_the_guard_stays_silent_on_a_correct_grouping():
    a = record(task_id="t1")
    b = record(task_id="t2", source="s32 f(void) { return 2; }")
    states = rs.group_by_state([a, b])
    rs.assert_states_are_not_functions([a, b], states)      # must not raise


# --- 5. trajectory outcomes without inventing credit ----------------------------

def test_a_drop_in_similarity_is_not_a_failed_trajectory():
    """An intermediate edit can lower similarity and still enable the eventual match."""
    assert rs.classify(exact=False, compiled=True, has_exact_descendant=True) == \
        rs.CERTIFIED_TRAJECTORY
    assert rs.classify(exact=False, compiled=True) == rs.UNRESOLVED
    assert rs.classify(exact=False, compiled=True, duplicate_of_sibling=True) == rs.FAILED_REDUNDANT


def test_a_compiler_refusal_is_the_only_automatic_failure():
    assert rs.classify(exact=False, compiled=False) == rs.FAILED_REDUNDANT
    assert rs.classify(exact=True, compiled=True) == rs.CERTIFIED_ALTERNATIVE


def test_an_ancestor_of_a_match_is_a_route_not_a_solution():
    nodes = [{"id": "a", "exact": False, "compiled": True, "score": 40.0},
             {"id": "b", "exact": False, "compiled": True, "score": 10.0},
             {"id": "c", "exact": True, "compiled": True, "score": 100.0}]
    labels = rs.trajectory_labels(nodes, [("a", "b"), ("b", "c")])
    assert labels["a"]["label"] == rs.CERTIFIED_TRAJECTORY
    assert labels["b"]["label"] == rs.CERTIFIED_TRAJECTORY
    assert labels["c"]["label"] == rs.CERTIFIED_ALTERNATIVE
    assert "not a solution" in labels["a"]["note"]


def test_a_branch_with_no_exact_descendant_is_unresolved_not_rejected():
    nodes = [{"id": "a", "exact": False, "compiled": True, "score": 30.0},
             {"id": "b", "exact": False, "compiled": True, "score": 5.0}]
    labels = rs.trajectory_labels(nodes, [("a", "b")])
    assert {v["label"] for v in labels.values()} == {rs.UNRESOLVED}


def test_a_cycle_terminates_and_invents_nothing():
    nodes = [{"id": "a", "exact": False, "compiled": True},
             {"id": "b", "exact": False, "compiled": True}]
    labels = rs.trajectory_labels(nodes, [("a", "b"), ("b", "a")])
    assert {v["label"] for v in labels.values()} == {rs.UNRESOLVED}
    assert all(v["exact_descendant"] is False for v in labels.values())


# --- split isolation and lineage ------------------------------------------------

def test_children_keep_the_split_they_were_verified_in():
    train = record(task_id="t1", split="train")
    held = record(task_id="t2", split="test", function="g")
    states = rs.group_by_state([r for r in (train, held) if r["split"] == "train"])
    assert len(states) == 1
    assert all(m["split"] == "train" for members in states.values() for m in members)


def test_a_variant_for_a_held_out_task_cannot_enter_a_train_weighting():
    """Evaluation discoveries must not enter training.

    `group_by_state` deliberately does NOT filter by split -- it groups whatever it is given, and the
    caller filters. So the guarantee is checked where it actually lives: a train-filtered list that
    contains no train task yields no state, and a variant naming a held-out task joins nothing.
    """
    held = record(task_id="held", split="test")
    variants = [{"task_id": "held", "function": "f", "rewrite": "typedef-spelling",
                 "source": "s32 f(void) { return 1; } /* variant */"}]
    train_only = [r for r in (held,) if r["split"] == "train"]
    assert rs.group_by_state(train_only) == {}
    assert rs.balanced_weights(train_only, variants) == {}


def test_a_variant_cannot_join_a_state_whose_record_is_absent():
    """The join is on the record, so a variant with no matching task contributes nothing at all."""
    train = record(task_id="t1", split="train")
    orphan = [{"task_id": "someone-elses-task", "function": "f", "rewrite": "hoist-default",
               "source": "s32 f(void) { return 7; }"}]
    children = rs.verified_children([train], orphan)
    assert len(children) == 1 and children[0]["task_id"] == "t1"


def test_state_key_is_stable_and_covers_every_component():
    a, b = record(task_id="t1"), record(task_id="t2")
    assert rs.state_key(a) == rs.state_key(b)
    assert rs.state_id(a) == rs.state_id(b)
    keys = set(rs.state_key(a))
    assert keys == {"target", "parent", "feedback", "build", "prompt"}
    assert len(set(rs.state_key(a).values())) > 1, "the components must not all collapse to one hash"
