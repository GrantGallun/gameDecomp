from copy import deepcopy
import json

import pytest

from eval.repair_graph import build_graph, validate_graph
from eval.search_replay import Policy, digest, run
from eval.search_scheduler import Online
from test_search_scheduler import compiler, context


def world():
    callback, _ = compiler({"start": 10, "child": 30})
    env = Online("start", callback, lambda *_: iter([("edit", "fixture", "child")]), context())
    run(env, Policy("depth", "depth"), 2)
    return env.world


def test_repeated_worlds_preserve_provenance_without_duplicate_support():
    w = world()
    graph = build_graph([w, deepcopy(w)])
    assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert len(edge["observations"]) == 1
    assert edge["observations"][0]["parent_receipt_id"] == 1
    assert edge["observations"][0]["receipt_id"] == 2


def test_different_target_compiler_or_assistance_contexts_never_merge():
    original = world()
    worlds = [original]
    for field, value in (("target_sha256", "e"*64), ("compiler_sha256", "f"*64), ("assistance", "header-assisted")):
        other = deepcopy(original)
        other["context"][field] = value
        if field == "target_sha256":
            for node in other["nodes"]:
                node["verdict"]["verification"][field] = value
        worlds.append(other)
    assert len(build_graph(worlds)["nodes"]) == 8


def test_contradictory_verdict_for_identical_concrete_state_is_rejected():
    original, other = world(), world()
    other["nodes"][1]["verdict"]["score"] += 1
    with pytest.raises(ValueError, match="contradictory"):
        build_graph([original, other])


def test_same_fault_counts_do_not_merge_different_source_states():
    w = world()
    w["nodes"][1]["verdict"]["score"] = w["nodes"][0]["verdict"]["score"]
    assert len(build_graph([w])["nodes"]) == 2


def test_missing_or_false_parent_receipts_fail_closed():
    w = world()
    w["nodes"][1]["parent_receipt_id"] = 99
    with pytest.raises(ValueError, match="receipt"):
        build_graph([w])


def test_graph_does_not_mutate_original_worlds():
    w = world()
    graph = build_graph([w])
    next(iter(graph["nodes"].values()))["source"] = "changed"
    assert w["nodes"][0]["source"] == "start"


@pytest.mark.parametrize("change", ["source", "exact", "edge", "reference"])
def test_rehashed_graph_requires_valid_original_evidence(change):
    graph = build_graph([world()])
    node = next(iter(graph["nodes"].values()))
    if change == "source":
        node["source"] = "uncompiled"
    elif change == "exact":
        node["verdict"].update(exact=True, verification={})
    elif change == "edge":
        graph["edges"][0]["child"] = "0" * 64
    else:
        node["observations"][0]["receipt_id"] = 99
    graph["sha256"] = digest({k:v for k,v in graph.items() if k != "sha256"})
    with pytest.raises(ValueError):
        validate_graph(graph)


@pytest.mark.parametrize("field", ["frontend", "candidate", "target"])
def test_nonexact_receipt_bindings_are_validated(field):
    w = world()
    verdict = w["nodes"][0]["verdict"]
    if field == "frontend":
        verdict["frontend"]["source_sha256"] = "0" * 64
    else:
        verdict["verification"]["candidate_source_sha256" if field == "candidate" else "target_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="binding"):
        build_graph([w])


def test_graph_reload_is_independent_of_json_object_key_order():
    first, second = world(), world()
    second["context"]["compiler_sha256"] = "0" * 64
    graph = build_graph([first,second])
    assert validate_graph(json.loads(json.dumps(graph,sort_keys=True))) == graph
    assert build_graph([second,first]) == graph


def test_one_receipt_cannot_claim_two_attempts_in_the_same_world():
    w = world()
    w["nodes"][1]["verdict"]["receipt_id"] = 1
    with pytest.raises(ValueError,match="receipt"):
        build_graph([w])
