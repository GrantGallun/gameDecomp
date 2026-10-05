"""Mechanism tests for opt-in research search policies; listings are hand coded."""

import pytest

from eval.research_suite.search import run_search
from solver.regalloc_search import Compiled
from solver import regalloc_mutations


TARGET = "addu v0,a0,a1\njr ra\nnop\n"
NEAR = "addu v0,a0,t0\njr ra\nnop\n"
FAR = "subu v0,a0,t0\njr ra\nnop\n"
VERY_FAR = "subu v0,a0,t0\nsubu t1,t2,t3\njr ra\nnop\n"


def graph_compiler(dumps, exact=()):
    calls = []

    def compile_candidate(source, label, parent_source):
        calls.append((source, label, parent_source))
        return Compiled(True, source in exact, dumps[source])

    return compile_candidate, calls


def graph_generator(edges):
    def generate(source, compiled):
        return [(name, family, name) for name, family in edges.get(source, ())]

    return generate


def test_exploration_crosses_worse_intermediate_to_exact():
    compile_candidate, calls = graph_compiler(
        {"base": NEAR, "worse": FAR, "exact": TARGET}, exact={"exact"}
    )
    generate = graph_generator({"base": [("worse", "shape")], "worse": [("exact", "repair")]})
    beam = run_search("f", "base", TARGET, compile_candidate, arm="beam", generate=generate)
    explored = run_search("f", "base", TARGET, compile_candidate, arm="explore",
                          explore_rate=1.0, seed=7, generate=generate)
    assert not beam["exact"]
    assert explored["exact"] and explored["best_source"] == "exact"
    assert any(row["source"] == "worse" and row["probability"] == 1.0
               for row in explored["decisions"])
    assert explored["compiles"] == len(calls) - beam["compiles"]


def test_archive_preserves_same_object_source_alternative():
    compile_candidate, _ = graph_compiler(
        {"base": FAR, "a": NEAR, "b": NEAR, "exact": TARGET}, exact={"exact"}
    )
    generate = graph_generator({"base": [("a", "same"), ("b", "same")],
                                "b": [("exact", "repair")]})
    beam = run_search("f", "base", TARGET, compile_candidate, arm="beam", beam=1,
                      depth=3, generate=generate)
    archive = run_search("f", "base", TARGET, compile_candidate, arm="archive", beam=1,
                         archive_size=2, depth=3, generate=generate)
    assert not beam["exact"]
    assert archive["exact"] and archive["best_source"] == "exact"
    assert any(row["source"] == "b" for row in archive["decisions"])


def test_archive_only_does_not_let_worse_child_evict_useful_tie():
    compile_candidate, _ = graph_compiler(
        {"base": FAR, "a": NEAR, "b": NEAR, "worse": VERY_FAR,
         "exact": TARGET}, exact={"exact"}
    )
    generate = graph_generator({"base": [("a", "same"), ("b", "same"),
                                         ("worse", "shape")],
                                "b": [("exact", "repair")]})
    result = run_search("f", "base", TARGET, compile_candidate, arm="archive",
                        archive_size=2, depth=3, generate=generate)
    assert result["exact"] and result["best_source"] == "exact"


def test_seeded_choices_and_logged_propensities_are_reproducible():
    compile_candidate, _ = graph_compiler(
        {"base": NEAR, "w1": FAR, "w2": FAR, "x": NEAR}
    )
    generate = graph_generator({"base": [("w1", "a"), ("w2", "b"), ("x", "c")]})
    options = dict(arm="explore", explore_rate=1.0, beam=1, depth=2,
                   seed=13, generate=generate)
    one = run_search("f", "base", TARGET, compile_candidate, **options)
    two = run_search("f", "base", TARGET, compile_candidate, **options)
    assert one["decisions"] == two["decisions"]
    choices = [d for d in one["decisions"] if d["probability"] < 1.0]
    assert choices and choices[0]["probability"] == pytest.approx(0.5)
    sources_by_id = {event["id"]: event["source"] for event in one["events"]}
    assert {sources_by_id[id_] for id_ in choices[0]["eligible_ids"]} == {"w1", "w2"}
    assert list(choices[0]["probabilities"].values()) == [0.5, 0.5]


def test_budget_counts_root_and_failed_compiles_and_stops_generation():
    calls = []

    def compile_candidate(source, label, parent_source):
        calls.append(source)
        if source == "bad":
            raise RuntimeError("compiler failed")
        return Compiled(True, False, NEAR)

    def generate(source, compiled):
        for name in ("bad", "good", "overflow"):
            yield name, "x", name
        while True:
            yield "duplicate", "x", "duplicate"

    result = run_search("f", "base", TARGET, compile_candidate, budget=3,
                        generate=generate)
    assert result["compiles"] == 3 == len(calls)
    assert calls == ["base", "bad", "good"]
    assert result["stop"] == "budget"
    assert any(row["source"] == "bad" and row["error"] == "RuntimeError"
               and not row["exact"] for row in result["events"])


def test_zero_budget_does_not_compile():
    def compile_candidate(source, label, parent_source):
        raise AssertionError("must not compile")

    result = run_search("f", "base", TARGET, compile_candidate, budget=0)
    assert result["compiles"] == 0 and result["stop"] == "budget"
    assert not result["exact"]


def test_identical_listing_is_not_an_exact_verdict():
    compile_candidate, _ = graph_compiler({"base": TARGET})
    result = run_search("f", "base", TARGET, compile_candidate, depth=0)
    assert not result["exact"]
    assert result["best_source"] == "base"


def test_duplicate_only_infinite_generator_has_finite_cap():
    compile_candidate, calls = graph_compiler({"base": NEAR})

    def generate(source, compiled):
        while True:
            yield "duplicate", "same", "base"

    result = run_search("f", "base", TARGET, compile_candidate,
                        budget=2, generate=generate)
    assert result["stop"] == "generation_cap"
    assert result["compiles"] == len(calls) == 1


def test_production_arm_uses_existing_solver_and_parent_callback(monkeypatch):
    monkeypatch.setattr(regalloc_mutations, "variants",
                        lambda source, function, diff="", prefer=(), evidence=None:
                        [("next", "repair", "exact")])
    compile_candidate, calls = graph_compiler(
        {"base": NEAR, "exact": TARGET}, exact={"exact"}
    )
    result = run_search("f", "base", TARGET, compile_candidate,
                        arm="production", budget=2, key=lambda s: s,
                        same_object=lambda a, b: a.obj == b.obj)
    assert result["exact"] and result["best_source"] == "exact"
    assert result["compiles"] == 2 == len(calls)
    assert calls[1] == ("exact", "next", "base")


def test_production_rejects_inconsistent_uncompiled_exact_verdict():
    def compile_candidate(source, label, parent_source):
        return Compiled(False, True, TARGET)

    result = run_search("f", "base", TARGET, compile_candidate,
                        arm="production", budget=1, key=lambda s: s,
                        same_object=lambda a, b: None)
    assert not result["exact"]
    assert not result["events"][0]["exact"]


def test_declined_exploration_is_logged_with_eligible_alternative():
    compile_candidate, _ = graph_compiler({"base": NEAR, "worse": FAR})
    result = run_search("f", "base", TARGET, compile_candidate, arm="explore",
                        explore_rate=0.5, seed=0,
                        generate=graph_generator({"base": [("worse", "shape")]}))
    assert not result["exact"]
    decision = result["decisions"][0]
    assert decision["id"] is None
    assert decision["probabilities"] == {"s1": 0.5}
    assert decision["none_probability"] == 0.5


def test_production_keeps_enabled_keyed_root_and_rechecks_it_before_expanding(monkeypatch):
    """AerialTrick-shaped control: only an enabled same-object root reaches exact."""
    from solver import regalloc_search
    seen_options = {}
    original = regalloc_search.search

    def observe(*args, **kwargs):
        seen_options.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(regalloc_search, 'search', observe)
    monkeypatch.setattr(regalloc_mutations, 'enabling_variants',
                        lambda source, function: [('enable', 'enabler', 'stored')] if source == 'base' else [])
    monkeypatch.setattr(regalloc_mutations, 'variants',
                        lambda source, *args, **kwargs: [('finish', 'repair', 'exact')] if source == 'stored' else [])
    calls, keys, checks = [], [], []

    def compile_candidate(source, label, parent):
        calls.append((source, parent))
        dump = TARGET if source == 'exact' else NEAR
        return Compiled(True, source == 'exact', dump, obj=dump.encode())

    def key(source):
        keys.append(source)
        return 'winner' if source == 'exact' else 'same-object'

    def same_object(prior, actual):
        checks.append((prior.obj, actual.obj))
        return prior.obj == actual.obj

    result = run_search('f', 'base', TARGET, compile_candidate, arm='production',
                        budget=10, seed=7, key=key, same_object=same_object)
    assert result['exact'] and result['best_source'] == 'exact'
    assert calls == [('base', None), ('stored', 'base'), ('exact', 'stored')]
    assert keys == ['base', 'stored', 'exact'] and len(checks) == 1
    assert result['compiles'] == 3 and result['key_calls'] == 3
    assert result['budget_spent'] == pytest.approx(3.42)
    assert result['resolutions'][0]['same_as_source'] == 'base'
    assert result['resolutions'][0]['source'] == 'stored'
    assert result['resolutions'][0]['exact'] is False
    assert result['production_summary']['expansion_conclusive'] == 1
    assert seen_options['enable'] is True and seen_options['diverse'] is False
    assert seen_options['audit_rate'] == 0.02 and seen_options['audit_seed'] == 7


def test_production_diverse_retains_the_family_that_reaches_exact(monkeypatch):
    edges = {'base': [('a1', 'type', 'a1'), ('a2', 'type', 'a2'), ('move', 'move', 'moved')],
             'moved': [('finish', 'repair', 'exact')]}
    monkeypatch.setattr(regalloc_mutations, 'variants', lambda source, *args, **kwargs: edges.get(source, []))
    monkeypatch.setattr(regalloc_mutations, 'enabling_variants', lambda *args: [])

    def compile_candidate(source, label, parent):
        return Compiled(True, source == 'exact', FAR if source == 'base' else NEAR)

    kwargs = dict(budget=20, beam=2, depth=2, key=lambda s: s, same_object=lambda a, b: None)
    plain = run_search('f', 'base', TARGET, compile_candidate, arm='production', **kwargs)
    diverse = run_search('f', 'base', TARGET, compile_candidate, arm='production_diverse', **kwargs)
    assert not plain['exact'] and diverse['exact']
    assert diverse['policy']['diverse'] is True
    assert diverse['policy']['enable'] is True and diverse['policy']['keyed'] is True


def test_production_refuses_missing_key_authorities_instead_of_silently_weakening_baseline():
    compiler, calls = graph_compiler({'base': NEAR})
    with pytest.raises(ValueError, match='key.*same_object'):
        run_search('f', 'base', TARGET, compiler, arm='production', budget=3)
    assert calls == []
