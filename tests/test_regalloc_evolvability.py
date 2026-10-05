"""Synthetic search graphs test policy mechanics, not native IDO yield."""
import pytest

from solver import regalloc_mutations, regalloc_search as rs

TARGET = "addu v0,a0,a1\njr ra\nnop\n"
NEAR = "addu v0,a0,t0\njr ra\nnop\n"
FAR = "subu v0,a0,t0\njr ra\nnop\n"


def world(monkeypatch, edges, dumps, exact=()):
    calls = []

    def variants(source, function, diff="", prefer=(), evidence=None):
        assert evidence == {"owner": source}
        return iter((child, family, child) for child, family in edges.get(source, ()))

    def compile_candidate(source, label):
        calls.append(source)
        dump = dumps.get(source)
        return rs.Compiled(dump is not None, source in exact, dump,
                           evidence={"owner": source}, obj=dump.encode() if dump else None)

    monkeypatch.setattr(regalloc_mutations, "variants", variants)
    monkeypatch.setattr(regalloc_mutations, "enabling_variants", lambda *args: [])
    return compile_candidate, calls


@pytest.mark.parametrize("selection", ["mutation_count", "evolvability"])
def test_worse_source_with_moves_outlives_better_dead_end(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("dead", "type"), ("bridge", "shape")], "bridge": [("exact", "repair")]},
        {"base": NEAR, "dead": NEAR, "bridge": FAR, "exact": TARGET}, {"exact"})
    greedy = rs.search("f", "base", compile_candidate, TARGET, budget=10, beam=1, depth=2)
    assert not greedy.exact
    calls.clear()
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, beam=1, depth=2,
                       selection=selection, explore_rate=0)
    assert result.exact and result.best_source == "exact"
    assert result.compiles == len(calls) == 4


def test_best_result_survives_expanding_a_worse_source(monkeypatch):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("best", "type"), ("bridge", "shape")], "bridge": [("tail", "repair")]},
        {"base": FAR, "best": NEAR, "bridge": FAR, "tail": FAR})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, beam=1, depth=2,
                       selection="mutation_count", explore_rate=0)
    assert "tail" in calls and result.best_source == "best" and not result.exact
    assert result.improved


def test_quality_sampling_beats_more_unproductive_mutations(monkeypatch):
    edges = {"base": [("many", "type"), ("useful", "shape")],
             "many": [(f"junk{i}", "noise") for i in range(5)],
             "useful": [("step", "repair")], "step": [("exact", "finish")]}
    dumps = {"base": FAR, "many": NEAR, "useful": NEAR,
             "step": TARGET, "exact": TARGET, **{f"junk{i}": FAR for i in range(5)}}
    compile_candidate, calls = world(monkeypatch, edges, dumps, {"exact"})
    count = rs.search("f", "base", compile_candidate, TARGET, budget=20, beam=1, depth=3,
                      selection="mutation_count", explore_rate=0)
    assert not count.exact
    calls.clear()
    quality = rs.search("f", "base", compile_candidate, TARGET, budget=20, beam=1, depth=3,
                        selection="evolvability", mutation_probes=1, explore_rate=0)
    assert quality.exact and calls.count("step") == 1
    assert quality.compiles == len(calls)
    assert any(d["mode"] == "rank" and d["label"] == "useful" for d in quality.decisions)


@pytest.mark.parametrize("selection", ["mutation_count", "evolvability"])
def test_keyed_same_object_sources_use_own_evidence_before_preview(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("dead", "type"), ("bridge", "shape")], "bridge": [("exact", "repair")]},
        {"base": FAR, "dead": NEAR, "bridge": NEAR, "exact": TARGET}, {"exact"})
    checks = []

    def same_object(a, b):
        checks.append((a.obj, b.obj))
        return a.obj == b.obj

    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, beam=1, depth=2,
                       selection=selection, explore_rate=0,
                       key=lambda s: "shared" if s in {"dead", "bridge"} else s,
                       same_object=same_object)
    assert result.exact and result.keyed == 1 and result.expansion_checks == 1
    assert len(checks) == 1 and calls.count("bridge") == 1
    assert result.compiles == len(calls) == 4


def test_failed_probes_cost_budget_and_are_not_recompiled(monkeypatch):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("parent", "shape")], "parent": [("bad", "broken"), ("tail", "repair")]},
        {"base": FAR, "parent": NEAR, "bad": None, "tail": NEAR})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=4, beam=1, depth=3,
                       selection="evolvability", mutation_probes=2, explore_rate=0)
    assert result.compiles == len(calls) == 4 and calls.count("bad") == 1
    assert not result.exact
    assert any(row.get("probe") and not row["compiled"] for row in result.log)


def test_probe_is_not_recompiled_when_its_parent_is_selected(monkeypatch):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("parent", "shape")], "parent": [("step", "repair")],
         "step": [("tail", "repair")]},
        {"base": FAR, "parent": NEAR, "step": NEAR, "tail": TARGET})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=20, depth=3,
                       selection="evolvability", mutation_probes=1, explore_rate=0)
    assert not result.exact  # A target-looking listing cannot declare a match.
    assert calls == ["base", "parent", "step", "tail"]
    assert result.compiles == 4 and result.best_source == "tail"


def test_no_lookahead_compiles_past_depth_limit(monkeypatch):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("parent", "shape")], "parent": [("exact", "repair")]},
        {"base": FAR, "parent": NEAR, "exact": TARGET}, {"exact"})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=20, depth=1,
                       selection="evolvability")
    assert not result.exact and calls == ["base", "parent"]


def test_bounded_preview_reports_unknown_tail_instead_of_exhaustion(monkeypatch):
    compile_candidate, calls = world(monkeypatch, {}, {"base": FAR, "child": NEAR})

    def variants(source, *args, **kwargs):
        if source == "base":
            yield "child", "shape", "child"
        else:
            while True:
                yield "duplicate", "same", "child"

    monkeypatch.setattr(regalloc_mutations, "variants", variants)
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, depth=2,
                       selection="mutation_count", mutation_preview=4)
    assert calls == ["base", "child"]
    assert result.preview_capped > 0 and not result.exact


@pytest.mark.parametrize("selection", ["mutation_count", "evolvability"])
def test_unknown_preview_tail_can_still_reach_exact(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("bridge", "shape")],
         "bridge": [("bridge", "duplicate"), ("bridge", "duplicate"), ("exact", "repair")]},
        {"base": FAR, "bridge": NEAR, "exact": TARGET}, {"exact"})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, depth=2,
                       selection=selection, mutation_preview=2, explore_rate=0)
    assert result.exact and calls == ["base", "bridge", "exact"]
    assert result.preview_capped == 1


def test_actual_expansion_can_reach_every_previewed_move(monkeypatch):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("bridge", "shape")],
         "bridge": [("bridge", "duplicate")] * 49 + [("exact", "repair")]},
        {"base": FAR, "bridge": NEAR, "exact": TARGET}, {"exact"})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=4, depth=2,
                       selection="mutation_count", mutation_preview=64, explore_rate=0)
    assert result.exact and calls == ["base", "bridge", "exact"]
    assert result.generation_capped == result.preview_capped == 0


def test_seeded_selection_logs_normalized_probabilities(monkeypatch):
    compile_candidate, _ = world(monkeypatch,
        {"base": [("a", "shape"), ("b", "type")],
         "a": [("aa", "repair")], "b": [("bb", "repair")]},
        {"base": FAR, "a": NEAR, "b": NEAR, "aa": FAR, "bb": FAR})
    kwargs = dict(budget=20, depth=2, beam=1, selection="mutation_count",
                  explore_rate=0.25, selection_seed=7)
    one = rs.search("f", "base", compile_candidate, TARGET, **kwargs)
    two = rs.search("f", "base", compile_candidate, TARGET, **kwargs)
    assert one.decisions == two.decisions
    decision = one.decisions[0]
    assert sorted(decision["probabilities"].values()) == [0.125, 0.875]
    assert sum(decision["probabilities"].values()) == 1


@pytest.mark.parametrize("options", [{"selection": "typo"}, {"mutation_preview": 0},
    {"mutation_probes": -1}, {"explore_rate": float("nan")}])
def test_invalid_policy_options_refuse_before_compiling(options):
    calls = []
    with pytest.raises(ValueError):
        rs.search("f", "base", lambda *args: calls.append(args), TARGET, **options)
    assert calls == []


@pytest.mark.parametrize("selection", ["gradient", "mutation_count", "evolvability"])
def test_rejected_key_audit_is_charged_before_restart(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch,
        {"base": [("bridge", "shape")], "bridge": [("exact", "repair")]},
        {"base": FAR, "bridge": NEAR, "exact": TARGET}, {"exact"})
    result = rs.search("f", "base", compile_candidate, TARGET, budget=4, depth=2,
                       selection=selection, key=lambda s: "collision", key_cost=0,
                       same_object=lambda a, b: a.obj == b.obj, audit_rate=1)
    assert result.exact and result.key_violations == 1
    assert result.compiles == len(calls) == 4


@pytest.mark.parametrize("selection", ["mutation_count", "evolvability"])
def test_enabled_keyed_root_still_reaches_exact(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch, {"stored": [("exact", "repair")]},
        {"base": NEAR, "stored": NEAR, "exact": TARGET}, {"exact"})
    monkeypatch.setattr(regalloc_mutations, "enabling_variants", lambda source, function:
                        [("stored", "enabler", "stored")] if source == "base" else [])
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, depth=2,
                       selection=selection, enable=True,
                       key=lambda s: "shared" if s != "exact" else s,
                       same_object=lambda a, b: a.obj == b.obj)
    assert result.exact and calls == ["base", "stored", "exact"]
    assert result.expansion_checks == result.keyed == 1


@pytest.mark.parametrize("selection", ["gradient", "mutation_count", "evolvability"])
def test_failed_recheck_never_expands_with_invalid_compile_evidence(monkeypatch, selection):
    compile_candidate, calls = world(monkeypatch, {"stored": [("exact", "repair")]},
        {"base": NEAR, "stored": None, "exact": TARGET}, {"exact"})
    monkeypatch.setattr(regalloc_mutations, "enabling_variants", lambda source, function:
                        [("stored", "enabler", "stored")] if source == "base" else [])
    result = rs.search("f", "base", compile_candidate, TARGET, budget=10, depth=2,
                       selection=selection, enable=True, key=lambda s: "shared",
                       same_object=lambda a, b: None)
    assert not result.exact and calls == ["base", "stored"]
    assert result.key_disabled and result.expansion_conclusive == 0
