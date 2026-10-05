"""The bounded diverse archive: what it dedups, what it must never dedup, and its caps.

The tests that matter most are the ones asserting the descriptor does NOT hide a real difference. A
novelty key that collapsed `return 1;` with `return 2;` would make every duplicate check in the
pipeline vacuous while looking perfectly healthy, so each significant feature is pinned separately.
"""
from __future__ import annotations

import inspect

import pytest

from eval import repair_archive as ra


# --- what "the same approach" means ---------------------------------------------

def test_whitespace_only_differences_are_the_same_approach():
    a = "s32 f(void) { return 1; }"
    b = "s32 f(void)\n{\n    return  1 ;\n}\n"
    assert ra.novelty_key(a) == ra.novelty_key(b)


def test_identifier_only_differences_are_the_same_approach():
    a = "s32 f(void) { s32 value; value = 1; return value; }"
    b = "s32 f(void) { s32 total; total = 1; return total; }"
    assert ra.novelty_key(a) == ra.novelty_key(b)


def test_a_changed_constant_is_a_different_approach():
    """The motivating false-collapse: a descriptor that hid this would be worse than useless."""
    assert ra.novelty_key("s32 f(void) { return 1; }") != \
        ra.novelty_key("s32 f(void) { return 2; }")


def test_a_changed_operator_is_a_different_approach():
    assert ra.novelty_key("s32 f(s32 a, s32 b) { return a + b; }") != \
        ra.novelty_key("s32 f(s32 a, s32 b) { return a - b; }")


def test_a_changed_control_flow_keyword_is_a_different_approach():
    assert ra.novelty_key("s32 f(s32 n) { while (n) { n--; } return n; }") != \
        ra.novelty_key("s32 f(s32 n) { for (; n; ) { n--; } return n; }")


def test_a_changed_signedness_is_a_different_approach():
    """`s32` and `u32` are not interchangeable and must survive renaming."""
    assert ra.novelty_key("s32 f(void) { s32 x; return x; }") != \
        ra.novelty_key("s32 f(void) { u32 x; return x; }")


def test_a_changed_string_literal_is_a_different_approach():
    assert ra.novelty_key('void f(void) { g("one"); }') != \
        ra.novelty_key('void f(void) { g("two"); }')


def test_canonical_is_pure_and_stable_across_processes():
    """A process-local hash would make two runs disagree about duplicates."""
    source = "s32 f(void) { return 1; }"
    assert ra.canonical(source) == ra.canonical(source)
    assert ra.novelty_key(source) == ra.novelty_key(source)
    assert len(ra.novelty_key(source)) == 64 and ra.novelty_key(source).isalnum()


def test_canonical_of_empty_input_is_empty_not_an_error():
    assert ra.canonical("") == "" and ra.novelty_key("") == ra.novelty_key("")


# --- structural descriptors -----------------------------------------------------

def test_structural_features_separate_routes_that_score_alike():
    loop = ra.structural_features("s32 f(s32 n) { s32 a; for (a = 0; a < n; a++) { g(a); } return a; }")
    switch = ra.structural_features("s32 f(s32 n) { switch (n) { case 1: return 2; } return 0; }")
    assert loop["control_flow"]["for"] == 1
    assert switch["control_flow"]["switch"] == 1 and switch["cases"] == 1
    assert switch["has_default"] is False
    assert loop["control_flow"] != switch["control_flow"]


def test_structural_features_detect_a_restored_default():
    without = ra.structural_features("s32 f(s32 n) { switch (n) { case 1: return 2; } return 0; }")
    with_default = ra.structural_features(
        "s32 f(s32 n) { switch (n) { case 1: return 2; default: return 0; } }")
    assert without["has_default"] is False and with_default["has_default"] is True


# --- retention rules ------------------------------------------------------------

def test_an_exact_candidate_is_always_stored_even_at_a_terrible_score():
    archive = ra.RepairArchive(capacity=4, min_score=90.0)
    entry = archive.offer(source="s32 f(void) { return 1; }", score=0.1, exact=True,
                          certificate_status="object_sections_exact")
    assert entry is not None and archive.best.id == entry.id


def test_a_duplicate_is_declined_and_does_not_consume_capacity():
    archive = ra.RepairArchive(capacity=2)
    a = archive.offer(source="s32 f(void) { s32 v; return v; }", score=10.0, exact=False)
    b = archive.offer(source="s32 f(void) { s32 w; return w; }", score=99.0, exact=False)
    assert a is not None and b is None
    assert len(archive) == 1 and archive.duplicate_count == 1


def test_capacity_is_respected_and_the_best_candidate_survives_eviction():
    archive = ra.RepairArchive(capacity=3)
    kept = [archive.offer(source=f"s32 f(void) {{ return {i}; }}", score=float(i), exact=False)
            for i in range(6)]
    assert len(archive) == 3
    assert archive.best.score == 5.0, "the leader is never evicted"
    assert archive.evicted, "eviction must be recorded, not silent"


def test_an_exact_candidate_is_never_evicted_while_an_inexact_one_exists():
    archive = ra.RepairArchive(capacity=2)
    exact = archive.offer(source="s32 f(void) { return 1; }", score=1.0, exact=True)
    archive.offer(source="s32 f(void) { return 2; }", score=50.0, exact=False)
    archive.offer(source="s32 f(void) { return 3; }", score=60.0, exact=False)
    assert any(e.id == exact.id for e in archive.entries), "an exact entry must outlive inexact ones"


def test_a_low_scoring_candidate_is_declined_when_a_threshold_is_set():
    archive = ra.RepairArchive(capacity=4, min_score=50.0)
    assert archive.offer(source="s32 f(void) { return 1; }", score=10.0, exact=False) is None
    assert archive.declined_low_score == 1
    assert archive.offer(source="s32 f(void) { return 1; }", score=60.0, exact=False) is not None


def test_capacity_of_zero_is_refused():
    with pytest.raises(ValueError, match="at least 1"):
        ra.RepairArchive(capacity=0)


# --- exploration ----------------------------------------------------------------

def test_the_motivating_case_a_worse_but_structurally_different_candidate_is_offered():
    """THE FIRE TEST. The whole reason the archive exists: a lower-scoring candidate on a different
    route has to survive and be offered as somewhere to explore, not be discarded for the leader."""
    archive = ra.RepairArchive(capacity=4, exploration_budget=2)
    leader = archive.offer(source="s32 f(s32 n) { s32 a; for (a = 0; a < n; a++) { g(a); } return a; }",
                           score=90.0, exact=False, strategy="refine")
    other = archive.offer(source="s32 f(s32 n) { switch (n) { case 1: return g(n); } return 0; }",
                          score=20.0, exact=False, strategy="redirect")
    assert other is not None, "a structurally different candidate must not be dropped"
    seeds = archive.exploration_seeds()
    assert seeds[0].id == leader.id, "the best is offered first"
    assert other.id in {s.id for s in seeds}, "the different route is offered for exploration"


def test_the_exploration_budget_caps_seeds_and_floors_at_zero():
    archive = ra.RepairArchive(capacity=8, exploration_budget=1)
    for i in range(5):
        archive.offer(source=f"s32 f(s32 n) {{ {'for' if i % 2 else 'while'} (n) {{ n--; }}{'; ' * i} return n; }}",
                      score=float(i), exact=False)
    seeds_before = archive.exploration_seeds()
    assert len(seeds_before) <= 2, "one best plus one budgeted seed"
    assert archive.spend(5) == 0
    assert archive.spend() == 0, "the budget floors at zero"
    assert [s.id for s in archive.exploration_seeds()][0] == archive.best.id, \
        "an exhausted budget still offers the best candidate"


def test_a_zero_budget_offers_only_the_best():
    archive = ra.RepairArchive(capacity=4, exploration_budget=0)
    archive.offer(source="s32 f(void) { return 1; }", score=1.0, exact=False)
    archive.offer(source="s32 f(void) { return 2; }", score=2.0, exact=False)
    assert len(archive.exploration_seeds()) == 1


def test_offering_is_deterministic():
    def build():
        archive = ra.RepairArchive(capacity=3, exploration_budget=2)
        for i in range(6):
            archive.offer(source=f"s32 f(s32 n) {{ {'for' if i % 2 else 'while'} (n) {{ n--; }}{'; ' * i} return n; }}",
                          score=float(i), exact=False)
        return archive.as_dict(), [e.id for e in archive.exploration_seeds()]
    assert build() == build()


# --- what this module must never do ---------------------------------------------

def test_the_module_compiles_nothing_and_touches_no_store():
    """`exact` is the caller's certificate verdict. A module that could infer exactness would
    eventually be trusted to, so the absence of any compile path is asserted, not assumed."""
    source = inspect.getsource(ra)
    for forbidden in ("subprocess", "import sqlite3", "certify", "compile_unit", "byte_certificate"):
        assert forbidden not in source, f"repair_archive must not reference {forbidden}"
    module_names = set()
    for name in dir(ra):
        obj = getattr(ra, name)
        module_names.add(getattr(obj, "__module__", ""))
    assert not any(m.startswith(("solver", "eval.repair_dataset_synth")) for m in module_names)


def test_the_receipt_states_that_exactness_is_a_claim():
    archive = ra.RepairArchive(capacity=2)
    archive.offer(source="s32 f(void) { return 1; }", score=1.0, exact=True,
                  certificate_status="object_sections_exact")
    receipt = archive.as_dict()
    assert "novelty is not quality" in receipt["note"]
    assert receipt["entries"][0]["certificate_status"] == "object_sections_exact"


def test_as_dict_is_json_serialisable():
    import json
    archive = ra.RepairArchive(capacity=2, exploration_budget=1)
    archive.offer(source="s32 f(void) { return 1; }", score=1.0, exact=False)
    json.dumps(archive.as_dict())
