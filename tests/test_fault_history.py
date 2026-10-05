"""The fault histogram: a frozen panel's fault SHAPE, and the two ways it can lie.

Every test here exists because the first version of the tool got it wrong in a way that looked like a
result. `final_classes` returned `[]` for a receipt that never measured a class set, so the comparison
reported every class in the frame as ADDED by the later round -- 180 of 200 states "moved", 94
`undeclared-identifier` classes "appeared" -- when in truth that receipt carried the same observation in its
step trace. A missing measurement read as a clean state, which is the same shape as the defects this whole
round has been about.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import fault_history as fh                                          # noqa: E402


def _row(name, classes=None, trace=None, exact=False):
    row = {"function": name, "sequence": {"exact": exact}}
    if classes is not None:
        row["sequence"]["frontend_classes"] = classes
    if trace is not None:
        row["diagnostic_trace"] = trace
    return row


def _frame(rows):
    return {"schema_version": 5, "rows": rows}


# --- what counts as a measurement -------------------------------------------

def test_a_class_set_comes_from_the_field_or_from_the_trace_or_is_absent():
    assert fh.final_classes(_row("a", classes=["x"])) == ["x"]
    # An older receipt has no `frontend_classes` and carries the same observation in its trace.
    assert fh.final_classes(_row("b", trace=[{"after": "start", "classes": ["y"]},
                                             {"after": "final", "classes": ["y", "z"]}])) == ["y", "z"]
    # NEITHER: not measured. `[]` here is the bug this pins.
    assert fh.final_classes(_row("c")) is None


def test_an_unmeasured_state_is_counted_not_treated_as_clean():
    payload = _frame([_row("measured", classes=[]), _row("never_measured")])
    result = fh.histogram(payload)
    assert result["states"] == 2
    assert result["states_measured"] == 1
    assert result["states_with_no_class_set_in_this_receipt"] == 1
    assert result["states_with_no_class"] == 1, "the measured one has an empty set; the other has none"


def test_a_receipt_without_the_field_is_read_from_its_trace_not_as_zero_classes():
    payload = _frame([_row("a", trace=[{"after": "start", "classes": ["undeclared-identifier"]},
                                       {"after": "final", "classes": ["undeclared-identifier"]}])])
    result = fh.histogram(payload)
    assert result["states_per_class"] == {"undeclared-identifier": 1}
    assert result["states_with_no_class"] == 0


# --- the histogram ----------------------------------------------------------

def test_the_set_size_distribution_is_the_falsifiable_number():
    payload = _frame([_row("clean", classes=[]),
                      _row("one", classes=["a"]),
                      _row("two", classes=["a", "b"]),
                      _row("three", classes=["a", "b", "c"])])
    result = fh.histogram(payload)
    assert result["class_set_sizes"] == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert result["states_with_one_class"] == 1
    assert result["states_with_two_classes"] == 1
    assert result["states_with_three_or_more"] == 1


def test_the_masking_fanout_measures_what_a_repair_reveals():
    """THE MOTIVATING CASE. A state whose first step shows one class and whose final candidate shows two
    others: that class was hiding them, and this is the number that says so. It is what turns "I fixed it
    and there are more problems" from a mystery into a measurement."""
    payload = _frame([_row("a", classes=["c2", "c3"],
                           trace=[{"after": "start", "classes": ["c1"]},
                                  {"after": "step", "classes": ["c2"]},
                                  {"after": "final", "classes": ["c2", "c3"]}])])
    result = fh.histogram(payload)
    assert result["masking_fanout"]["c1"] == {"states": 1, "new_classes_revealed": 2, "mean": 2.0}
    assert result["classes_masked_then_revealed"] == {"c2": 1, "c3": 1}
    assert "c1" not in result["classes_masked_then_revealed"], \
        "c1 was visible from the start; it was not revealed"


def test_a_class_that_hides_others_and_survives_is_still_ranked_as_a_blocker():
    """The repair ATTEMPT is what brings the next fault into view. A class that hides four others and is
    still there at the end is exactly the one worth pausing for, and a ranking that only counts cleared
    classes drops it."""
    payload = _frame([_row("a", classes=["c1", "c2", "c3"],
                           trace=[{"after": "start", "classes": ["c1"]},
                                  {"after": "final", "classes": ["c1", "c2", "c3"]}])])
    result = fh.histogram(payload)
    assert result["masking_fanout"] == {}, "c1 was never cleared"
    assert result["masking_fanout_any_attempt"]["c1"] == {"states": 1, "new_classes_revealed": 2,
                                                         "mean": 2.0}


def test_a_class_present_from_the_start_is_not_reported_as_revealed():
    payload = _frame([_row("a", classes=["c1"],
                           trace=[{"after": "start", "classes": ["c1"]},
                                  {"after": "final", "classes": ["c1"]}])])
    result = fh.histogram(payload)
    assert result["classes_masked_then_revealed"] == {}
    assert result["masking_fanout"] == {}


def test_error_instances_are_reported_only_when_the_receipt_carried_them():
    """A receipt written before `class_counts` existed must report an empty mapping, not zeros."""
    old = fh.histogram(_frame([_row("a", classes=["c1"],
                                    trace=[{"after": "final", "classes": ["c1"]}])]))
    assert old["error_instances_per_class_across_the_trace"] == {}
    assert old["coverage"]["trace_entries_carrying_class_counts"] == 0
    new = fh.histogram(_frame([_row("a", classes=["c1"],
                                    trace=[{"after": "final", "classes": ["c1"],
                                            "class_counts": {"c1": 7}}])]))
    assert new["error_instances_per_class_across_the_trace"] == {"c1": 7}


# --- comparing two rounds ---------------------------------------------------

def test_two_different_panels_are_refused_rather_than_diffed():
    """Membership drift is how a 22.5% rate and a 10.0% rate turned out to be the same rate on different
    frames. A per-class delta across two frames is not a delta."""
    result = fh.compare(_frame([_row("a", classes=["x"]), _row("b", classes=["x"])]),
                        _frame([_row("a", classes=["x"]), _row("c", classes=["x"])]))
    assert result["comparable"] is False
    assert "same panel" in result["reason"]
    assert result["only_before"] == ["b"] and result["only_after"] == ["c"]


def test_a_moved_state_reports_removed_and_added_separately():
    result = fh.compare(_frame([_row("a", classes=["lost", "kept"])]),
                        _frame([_row("a", classes=["kept", "gained"])]))
    assert result["comparable"] is True
    assert result["states_whose_class_set_moved"] == 1
    assert result["classes_removed_in_total"] == {"lost": 1}
    assert result["classes_added_in_total"] == {"gained": 1}
    moved = result["moved"][0]
    assert moved["classes_removed"] == ["lost"] and moved["classes_added"] == ["gained"]
    assert moved["before_size"] == 2 and moved["after_size"] == 2


def test_a_state_one_side_never_measured_is_not_counted_as_having_lost_everything():
    result = fh.compare(_frame([_row("a", classes=["x"]), _row("b")]),
                        _frame([_row("a", classes=["x"]), _row("b", classes=["x"])]))
    assert result["states_this_comparison_could_not_measure"] == 1
    assert result["classes_added_in_total"] == {}, "an unmeasured state adds no classes"


def test_an_unchanged_state_is_counted_as_unchanged_not_as_moved():
    result = fh.compare(_frame([_row("a", classes=["x", "y"])]),
                        _frame([_row("a", classes=["y", "x"])]))
    assert result["states_whose_class_set_moved"] == 0
    assert result["states_unchanged"] == 1


# --- basins -----------------------------------------------------------------

def test_leaving_the_declaration_basin_for_another_is_progress():
    """Measured shape of this project's residuals: 91.8% of setbacks stay inside a basin, and escaping one
    has a 0.375 success rate against 0.036. So a class set that changes basin is progress even when the
    score barely moves, and a shrinking count inside the same basin usually is not."""
    changed = fh.compare(_frame([_row("a", classes=["undeclared-identifier"])]),
                         _frame([_row("a", classes=["other-syntax"])]))
    assert changed["moved"][0]["basin_shift"] == {"left": ["declaration"], "entered": ["syntax"],
                                                  "changed_basin": True}
    same = fh.compare(_frame([_row("a", classes=["undeclared-identifier"])]),
                      _frame([_row("a", classes=["undeclared-member"])]))
    assert same["moved"][0]["basin_shift"]["changed_basin"] is False


def test_the_receipt_is_written_and_names_its_frame(tmp_path):
    frame = tmp_path / "frame.json"
    frame.write_text(json.dumps(_frame([_row("a", classes=["x"])])), encoding="utf-8"
                     )
    out = tmp_path / "history.json"
    assert fh.main(["--frame", str(frame), "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "fault-history" and payload["histogram"]["states"] == 1
    assert payload["frame"] == str(frame)


# --- the blocker graph ------------------------------------------------------

def _chained(name, trace, classes):
    return _row(name, classes=classes, trace=trace)


def test_the_graph_records_which_blocker_reveals_which():
    """THE MOTIVATING CASE. `a` is present at step 0; the step that clears it is the step `b` first appears
    in. A fan-out count says "one new class"; the edge says WHICH, which is what an order needs."""
    payload = _frame([_chained("s", [{"after": "start", "classes": ["a"]},
                                     {"after": "step1", "classes": ["b"]},
                                     {"after": "step2", "classes": ["b", "c"]}], ["b", "c"])])
    graph = fh.blocker_graph(payload)
    assert {"clears": "a", "reveals": "b", "states": 1} in graph["edges"]
    assert graph["unlocks"]["a"]["unlocks"] == ["b"]
    assert graph["depends_on"]["b"] == {"a": 1}
    assert graph["visible_at_step_0"] == {"a": 1}
    # `b` and `c` are NOT entry points: they were not visible at step 0.
    assert "b" not in graph["visible_at_step_0"]


def test_a_class_present_from_the_start_gets_its_own_edge_not_a_reveal():
    payload = _frame([_chained("s", [{"after": "start", "classes": ["a", "b"]},
                                     {"after": "step1", "classes": ["b"]}], ["b"])])
    graph = fh.blocker_graph(payload)
    assert graph["edges"] == [], "nothing new appeared, so nothing was revealed"
    assert graph["visible_at_step_0"] == {"a": 1, "b": 1}


def test_the_repair_order_puts_entry_points_first_and_ranks_by_what_they_unlock():
    payload = _frame([
        _chained("s1", [{"after": "start", "classes": ["wall"]},
                        {"after": "step1", "classes": ["one", "two"]}], ["one", "two"]),
        _chained("s2", [{"after": "start", "classes": ["leaf"]},
                        {"after": "step1", "classes": []}], []),
    ])
    graph = fh.blocker_graph(payload)
    assert graph["repair_order"][0]["depth"] == 0
    assert graph["repair_order"][0]["classes"][0]["class"] == "wall", \
        "the class that unlocks two others ranks above the one that unlocks none"
    assert graph["repair_order"][0]["classes"][0]["unlocked_per_clear"] == 2.0
    leaf = [entry for entry in graph["repair_order"][0]["classes"] if entry["class"] == "leaf"][0]
    assert leaf["unlocked_per_clear"] is None and leaf["unlocks_classes"] == 0


def test_a_leaf_class_is_not_reported_as_a_blocker():
    """`parameter-declarator` is cleared in 20 states of the real panel and unlocks nothing. A ranking by
    how often a class is SEEN would put it high; a ranking by what it gates puts it at the bottom, and the
    second is the one that decides what to work on."""
    payload = _frame([_chained("s", [{"after": "start", "classes": ["leaf"]},
                                     {"after": "step1", "classes": []}], [])])
    graph = fh.blocker_graph(payload)
    assert graph["unlocks"] == {}, "a class that reveals nothing has no unlock entry at all"
    assert graph["repair_order"][0]["classes"][0]["class"] == "leaf"
    assert graph["repair_order"][0]["classes"][0]["unlock_weight"] == 0


def test_a_two_cycle_is_reported_rather_than_smoothed_into_an_order():
    """Two classes can each reveal the other in different states. The graph is then not a DAG and a
    topological order does not exist; the order comes from first-appearance depth and says so."""
    payload = _frame([_chained("s1", [{"after": "start", "classes": ["a"]},
                                      {"after": "step1", "classes": ["b"]}], ["b"]),
                      _chained("s2", [{"after": "start", "classes": ["b"]},
                                      {"after": "step1", "classes": ["a"]}], ["a"])])
    graph = fh.blocker_graph(payload)
    assert graph["two_cycles"] == [["a", "b"]]
    assert "not a DAG" in graph["caveat"]
