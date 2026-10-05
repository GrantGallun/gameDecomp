"""The live scheduler must produce the same revealed tree that replay sees."""
import hashlib

import pytest

from eval.search_replay import Policy, Replay, merge_worlds, run
from eval.search_scheduler import Online


def context(source="start"):
    return {"task": "fixture", "initial_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "target_sha256": "a" * 64, "compiler_sha256": "b" * 64, "generator_sha256": "c" * 64}


def compiler(scores):
    receipts = []

    def compile_candidate(source, label, parent_receipt):
        score = scores[source]
        receipts.append((source, parent_receipt))
        sha = hashlib.sha256(source.encode()).hexdigest()
        return {"compiled": score >= 0, "exact": score == 100, "score": max(0, score),
                "receipt_id": len(receipts), "frontend": {"passed": score >= 0, "source_sha256": sha},
                "verification": {"schema_version": 1, "kind": "mips_object_section_certificate",
                                 "status": "object_sections_exact" if score == 100 else "object_sections_differ",
                                 "target_sha256": "a" * 64, "candidate_sha256": "d" * 64,
                                 "source_sha256": sha, "exact": score == 100, "candidate_source_sha256": sha}}

    return compile_candidate, receipts


def test_plateau_continuation_reaches_exact_with_explicit_parent_receipts():
    compile_candidate, receipts = compiler({"start": 80, "plateau": 80, "dead": 70, "exact": 100})

    def variants(source, diff):
        for child in {"start": ["plateau", "dead"], "plateau": ["exact"]}.get(source, []):
            yield "same-label", "fixture", child

    env = Online("start", compile_candidate, variants, context())
    result = run(env, Policy("greedy", "greedy"), budget=4)
    assert result["exact"]
    assert result["compiles"] == 3
    assert receipts == [("start", None), ("plateau", 1), ("exact", 2)]
    assert run(Replay(env.world), Policy("greedy", "greedy"), budget=4) == result


def test_deduplication_is_per_parent_and_ancestor_not_schedule_order():
    compile_candidate, receipts = compiler({"start": 10, "a": 20, "b": 30, "shared": 40})

    def variants(source, diff):
        for child in {"start": ["start", "a", "a", "b"], "a": ["start", "shared"],
                      "b": ["shared"]}.get(source, []):
            yield "same", "fixture", child

    env = Online("start", compile_candidate, variants, context())
    result = run(env, Policy("breadth", "breadth"), budget=10)
    assert result["stop"] == "exhausted"
    assert [r[0] for r in receipts] == ["start", "a", "b", "shared", "shared"]
    assert env.world["nodes"][3]["parent"] == "root/0"
    assert env.world["nodes"][4]["parent"] == "root/1"
    assert run(Replay(env.world), Policy("breadth", "breadth"), budget=10) == result


def test_compile_exceptions_are_charged_recorded_and_checkpointed():
    checkpoints = []

    def fail(*args):
        raise RuntimeError("compiler crashed")

    env = Online("start", fail, lambda *_: iter(()), context(), checkpoint=lambda w: checkpoints.append(len(w["nodes"])))
    result = run(env, Policy("greedy", "greedy"), budget=3)
    assert (result["compiles"], result["stop"]) == (1, "exhausted")
    assert "compiler crashed" in env.world["nodes"][0]["verdict"]["stderr"]
    assert checkpoints == [1]


def test_zero_budget_makes_no_compiler_call():
    compile_candidate, receipts = compiler({"start": 10})
    env = Online("start", compile_candidate, lambda *_: iter(()), context())
    assert run(env, Policy("base", "breadth"), budget=0)["compiles"] == 0
    assert not receipts


def test_score_100_without_certificate_is_not_exact():
    def compile_candidate(*args):
        return {"compiled": True, "exact": False, "score": 100}

    env = Online("start", compile_candidate, lambda *_: iter(()), context())
    assert not run(env, Policy("base", "breadth"), budget=1)["exact"]


def test_depth_limit_is_identical_online_and_in_replay():
    compile_candidate, _ = compiler({"start": 10, "child": 50})
    env = Online("start", compile_candidate, lambda *_: iter([("edit", "fixture", "child")]), context(), max_depth=0)
    result = run(env, Policy("base", "breadth"), budget=4)
    assert (result["compiles"], result["stop"]) == (1, "exhausted")
    assert run(Replay(env.world), Policy("base", "breadth"), budget=4) == result


def test_merging_worlds_adds_coverage_without_hiding_conflicts():
    compile_candidate, _ = compiler({"start": 10, "a": 20, "exact": 100, "b": 30})

    def variants(source, diff):
        for child in {"start": ["a", "b"], "a": ["exact"]}.get(source, []):
            yield "same", "fixture", child

    worlds = []
    for mode in ("breadth", "greedy"):
        env = Online("start", compile_candidate, variants, context())
        run(env, Policy(mode, mode), budget=3)
        worlds.append(env.world)
    merged = merge_worlds(worlds)
    assert len(merged["nodes"]) == 4
    assert run(Replay(merged), Policy("greedy", "greedy"), budget=3)["exact"]
    worlds[1]["nodes"][0]["verdict"]["score"] = 99
    with pytest.raises(ValueError, match="conflict"):
        merge_worlds(worlds)


def test_merging_different_compiler_contexts_is_forbidden():
    compile_candidate, _ = compiler({"start": 10})
    envs = [Online("start", compile_candidate, lambda *_: iter(()), context()) for _ in range(2)]
    for env in envs:
        run(env, Policy("base", "breadth"), budget=1)
    envs[1].world["context"]["compiler_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="context"):
        merge_worlds([e.world for e in envs])


def test_diff_timestamps_do_not_change_a_parent_mutation_stream():
    observed_diffs, worlds = [], []
    for stamp in ("2026-09-22 10:00:00", "2026-09-22 10:00:01"):
        def compile_candidate(*args):
            return {"compiled": True, "exact": False, "score": 10,
                    "diff": f"--- target.s\t{stamp}\n+++ candidate.s\t{stamp}\n@@ -1 +1 @@\n-old\n+new\n"}

        def variants(source, diff):
            observed_diffs.append(diff)
            return iter(())

        env = Online("start", compile_candidate, variants, context())
        run(env, Policy("base", "breadth"), budget=2)
        worlds.append(env.world)
    assert observed_diffs == ["--- target.s\n+++ candidate.s\n@@ -1 +1 @@\n-old\n+new\n"] * 2
    assert len(merge_worlds(worlds)["nodes"]) == 1


def test_depth_penalty_ignores_small_plateau_gain_but_pursues_large_gain():
    # A new descendant must not reset an exploration penalty to zero forever.
    for gain, expected in [(0.5, ["root", "root/0", "root/1"]),
                           (15, ["root", "root/0", "root/0/0"])]:
        compile_candidate, _ = compiler({"start": 50, "plateau": 50 + gain, "exact": 100})

        def variants(source, diff):
            for child in {"start": ["plateau", "exact"], "plateau": ["exact"]}.get(source, []):
                yield "same", "fixture", child

        env = Online("start", compile_candidate, variants, context())
        policy = Policy("depth-1", "depth", 1)
        result = run(env, policy, budget=3)
        assert result["exact"]
        assert result["trace"] == expected
        assert run(Replay(env.world), policy, budget=3) == result
