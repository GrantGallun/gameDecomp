"""Replay must not leak future outcomes or reward unrecorded continuations."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from eval.search_replay import Policy, Replay, load_world, run, save_world, select_policy, validate_world


def node(identity, parent=None, ordinal=0, score=10, *, compiled=True, exact=False):
    source = f"int candidate_{identity.replace('/', '_')};"
    sha = hashlib.sha256(source.encode()).hexdigest()
    return {"id": identity, "parent": parent, "ordinal": ordinal,
            "source": source, "source_sha256": sha, "label": "repeated-label", "family": "test",
            "verdict": {"compiled": compiled, "exact": exact, "score": score, "seconds": 0.25,
                        "frontend": {"passed": compiled, "source_sha256": sha},
                        "verification": {"schema_version": 1, "kind": "mips_object_section_certificate",
                                         "status": "object_sections_exact" if exact else "object_sections_differ",
                                         "target_sha256": "a" * 64, "candidate_sha256": "d" * 64,
                                         "source_sha256": sha, "exact": exact, "candidate_source_sha256": sha}}}


def world(nodes=None, closed=()):
    nodes = nodes or [node("root")]
    return {"context": {"task": "fixture", "initial_sha256": nodes[0]["source_sha256"],
                        "target_sha256": "a" * 64, "compiler_sha256": "b" * 64,
                        "generator_sha256": "c" * 64},
            "max_depth": 4, "nodes": nodes, "closed": list(closed)}


def test_policy_only_sees_immutable_revealed_observations():
    w = world([node("root"), node("root/0", "root", score=99),
               node("root/1", "root", 1, score=100, exact=True)])
    seen = []

    class Inspect:
        def choose(self, observations):
            seen.append(observations)
            assert isinstance(observations, tuple)
            assert not hasattr(observations[0], "source")
            assert not hasattr(observations[0], "children")
            with pytest.raises(FrozenInstanceError):
                observations[0].score = 100
            return "root"

    result = run(Replay(w), Inspect(), budget=2)
    assert [o.id for o in seen[0]] == ["root"]
    assert result["best_score"] == 99
    assert not result["exact"]
    assert result["compiles"] == 2


def test_absent_history_is_unsupported_not_exhaustion():
    missing = run(Replay(world()), Policy("breadth", "breadth"), budget=3)
    exhausted = run(Replay(world(closed=["root"])), Policy("breadth", "breadth"), budget=3)
    assert (missing["stop"], missing["complete"]) == ("unsupported", False)
    assert (exhausted["stop"], exhausted["complete"]) == ("exhausted", True)
    assert missing["compiles"] == exhausted["compiles"] == 1


def test_baseline_failures_and_budget_are_charged():
    w = world([node("root"), node("root/0", "root", score=0, compiled=False),
               node("root/1", "root", 1, score=100, exact=True)])
    policy = Policy("breadth", "breadth")
    assert run(Replay(w), policy, budget=0)["compiles"] == 0
    two = run(Replay(w), policy, budget=2)
    assert (two["compiles"], two["exact"], two["stop"]) == (2, False, "budget")
    three = run(Replay(w), policy, budget=3)
    assert (three["compiles"], three["exact"], three["best_id"]) == (3, True, "root/1")


def test_duplicate_action_labels_do_not_confuse_lineage():
    w = world([node("root"), node("root/0", "root", score=20),
               node("root/1", "root", 1, score=15),
               node("root/0/0", "root/0", score=100, exact=True)])
    result = run(Replay(w), Policy("greedy", "greedy"), budget=4)
    assert result["trace"] == ["root", "root/0", "root/0/0"]
    assert result["exact"]


def test_policy_selection_rejects_missing_coverage_and_preserves_incumbent_tie():
    w = world([node("root"), node("root/0", "root", score=20),
               node("root/1", "root", 1, score=30)])
    base = Policy("base", "breadth")
    report = select_policy([w], [Policy("unknown", "greedy"), Policy("tie", "breadth")], base, budget=3)
    assert report["selected"] == "base"
    assert not next(r for r in report["policies"] if r["name"] == "unknown")["eligible"]


def test_exact_count_outranks_partial_score_and_policy_cost():
    w = world([node("root"), node("root/0", "root", score=20),
               node("root/1", "root", 1, score=99),
               node("root/0/0", "root/0", score=100, exact=True)])
    report = select_policy([w], [Policy("greedy", "greedy")], Policy("base", "breadth"), budget=3)
    assert report["selected"] == "greedy"


@pytest.mark.parametrize("damage", ["parent", "gap", "duplicate", "source", "exact", "score", "initial"])
def test_invalid_world_is_rejected(damage):
    w = world([node("root"), node("root/0", "root", score=20)])
    if damage == "parent":
        w["nodes"][1]["parent"] = "missing"
    elif damage == "gap":
        w["nodes"][1].update(id="root/1", ordinal=1)
    elif damage == "duplicate":
        w["nodes"].append(deepcopy(w["nodes"][1]))
    elif damage == "source":
        w["nodes"][1]["source"] += "tamper"
    elif damage == "exact":
        w["nodes"][1]["verdict"]["exact"] = True
    elif damage == "score":
        w["nodes"][1]["verdict"]["score"] = float("nan")
    else:
        w["context"]["initial_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_world(w)


def test_serialization_binds_observations_and_source(tmp_path):
    path = tmp_path / "world.json"
    save_world(world(), path)
    assert load_world(path) == world()
    data = json.loads(path.read_text())
    data["world"]["nodes"][0]["verdict"]["score"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="checksum"):
        load_world(path)


def test_empty_calibration_cannot_claim_selected_policy():
    with pytest.raises(ValueError):
        select_policy([], [], Policy("base", "breadth"), budget=3)


@pytest.mark.parametrize("damage", ["minimal", "kind", "schema", "status", "target", "object", "frontend"])
def test_exact_requires_supported_object_certificate_bound_to_world(damage):
    w = world([node("root", score=100, exact=True)])
    v = w["nodes"][0]["verdict"]
    cert = v["verification"]
    if damage == "minimal":
        v["verification"] = {"exact": True, "candidate_source_sha256": w["nodes"][0]["source_sha256"]}
    elif damage == "kind":
        cert["kind"] = "normalized_score"
    elif damage == "schema":
        cert["schema_version"] = 999
    elif damage == "status":
        cert["status"] = "unverified"
    elif damage == "target":
        cert["target_sha256"] = "e" * 64
    elif damage == "object":
        del cert["candidate_sha256"]
    else:
        v["frontend"]["source_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="certificate"):
        validate_world(w)
