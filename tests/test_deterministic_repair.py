"""Deterministic inverse repair: the catalogue, its pairing with the mutations, and its limits.

The tests that matter here are the round-trips. An inverse that fires but does not recover the
original is worse than one that declines, and the only way to know which it is is to mutate a source
and invert it.
"""
from __future__ import annotations

import inspect

import pytest

from eval import deterministic_repair as dr
from eval import repair_mutations as rm


# --- the catalogue cannot drift away from the curriculum it inverts ---------------

def test_every_inverse_names_a_registered_mutation():
    """A catalogue entry with no matching mutation is inverting something that is not in the
    curriculum, and the pairing is the only thing keeping the two in step. A mutation may have more
    than one inverse spelling, so the mapping is explicit rather than the name being reused."""
    for name, mutation in dr.INVERSE_MUTATION.items():
        assert mutation in rm.MUTATIONS, f"{name!r} claims to invert {mutation!r}, which does not exist"


def test_every_firing_mutation_has_an_inverse_or_a_stated_reason():
    covered = set(dr.INVERSE_MUTATION.values())
    for name in rm.MUTATIONS:
        assert name in covered or name in dr.NOT_INVERTIBLE, (
            f"{name!r} is neither inverted nor documented as not invertible; the catalogue would be "
            f"silently partial")


def test_the_not_invertible_entry_states_a_reason():
    assert dr.NOT_INVERTIBLE["drop-switch-default"].strip()


# --- round-trips ------------------------------------------------------------------

def test_narrow_locals_round_trips_exactly():
    source = "s32 f(void) {\n    s32 a;\n    s32 b;\n\n    return a + b;\n}"
    mutated = rm.MUTATIONS["narrow-locals"].apply(source)
    assert mutated is not None and "s16 a;" in mutated
    assert dr.undo_widen_locals(mutated) == source


def test_subtract_to_narrow_round_trips_exactly():
    source = "s32 f(s32 x, s32 y) {\n    return x - y;\n}"
    mutated = rm.MUTATIONS["subtract-to-narrow"].apply(source)
    assert mutated is not None and "(s16)" in mutated
    # The BARE inverse is the byte-exact one. The parenthesised spelling is kept as well because the
    # mutation may have wrapped an expression that was already parenthesised, and only the candidate
    # is available to decide -- so both are offered and the certificate chooses.
    assert dr.undo_subtract_to_narrow_bare(mutated) == source
    assert dr.undo_subtract_to_narrow(mutated) == source.replace("x - y", "(x - y)")
    assert ("subtract-to-narrow-unparenthesised" in [n for n, _ in dr.repairs(mutated)])


def test_while_form_round_trips_exactly():
    source = ("s32 f(s32 n) {\n    s32 i;\n\n"
              "    for (i = 0; i < n; i++) {\n        g(i);\n    }\n    return i;\n}")
    mutated = rm.MUTATIONS["while-form"].apply(source)
    assert mutated is not None and "while (" in mutated
    assert dr.undo_while_form(mutated) == source


def test_split_initialiser_does_NOT_round_trip_and_that_is_a_DEFECT():
    """The mutation changes the ASSIGNMENT TARGET, so its inverse cannot recover the source.

    `_MUL_DECL`'s group(1) is the DECLARED variable and group(2) is the assignment target, and the
    emitted code uses group(1) for both. So `t = p * k;` becomes `var = tmp_var;` where `tmp_var`
    holds `p * k` -- the original assignment is replaced and its target left uninitialised. The
    docstring claims an intermediate local "changes which values live in which registers", which is
    semantics-preserving. It is not.

    THIS TEST PINS THE DEFECT, so it must FAIL LOUDLY when someone corrects the mutation -- because
    correcting it changes the dataset and invalidates the frozen manifest and every number measured
    on it. See `eval/results/multichild-20260920/RESULTS.md`.
    """
    source = "s32 f(s32 arg0, s32 k) {\n    s32 var0;\n    s32 var1;\n\n    var0 = arg0 * 9;\n    return var0 + var1;\n}"
    mutated = rm.MUTATIONS["split-initialiser"].apply(source)
    assert mutated is not None, "the mutation must fire on its own target shape"
    assert "var0 = arg0 * 9;" not in mutated, (
        "if this line survives, the mutation was FIXED: re-freeze the dataset, re-run the "
        "evaluation, and update RESULTS.md rather than deleting this test")
    recovered = dr.undo_split_initialiser(mutated)
    assert recovered != source, (
        "the inverse now recovers the source, which means the mutation was fixed; re-freeze")


# --- properties the evaluator depends on ----------------------------------------

def test_repairs_is_deterministic():
    candidate = "s32 f(void) {\n    s16 a;\n    s16 b;\n\n    return a - b;\n}"
    assert dr.repairs(candidate) == dr.repairs(candidate)


def test_repairs_declines_rather_than_guessing_when_nothing_matches():
    assert dr.repairs("s32 f(void) { return 0; }") == []


def test_repairs_returns_single_inverses_before_combinations():
    candidate = "s32 f(void) {\n    s16 a;\n\n    return a - a;\n}"
    names = [name for name, _ in dr.repairs(candidate)]
    assert names, "the motivating candidate must produce at least one inverse"
    assert "+" not in names[0], "singles come first so the cheapest repair is tried first"


def test_the_module_never_reads_the_recorded_answer():
    """The repair may use only what the solver was handed. A reference to the answer here would
    make the whole result meaningless, and it would be invisible in a passing test."""
    source = inspect.getsource(dr.repairs)
    assert "generator_source" not in source and "child" not in source
    assert "def main" in inspect.getsource(dr)      # the diagnostic lives in main, not in repairs


# --- the evaluator reports the two purchases separately --------------------------

def test_summarize_separates_deterministic_from_model_matches():
    from eval.evaluate_source_repair import ArmResult, summarize

    rows = [
        {"draws": 2, "compiled": True, "exact": True, "best_score": 100.0,
         "deterministic": True, "model_exact": True, "adapter_active": True},
        {"draws": 2, "compiled": True, "exact": True, "best_score": 100.0,
         "deterministic": True, "model_exact": False, "adapter_active": True},
        {"draws": 2, "compiled": False, "exact": False, "best_score": 0.0,
         "deterministic": False, "model_exact": False, "adapter_active": True},
    ]
    arm = ArmResult(arm="adapter", rows=rows, seconds=1.0, draws=6,
                    prompt_tokens=10, output_tokens=10)
    summary = summarize(arm)
    assert summary["tasks_exact"] == 2
    assert summary["tasks_exact_deterministic"] == 2
    assert summary["tasks_exact_by_model"] == 1
    assert summary["tasks_exact_by_both"] == 1
    assert summary["model_calls"] == 6, "the model budget is unchanged by the pre-pass"
