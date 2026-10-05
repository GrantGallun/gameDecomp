"""The observation/action boundary. Each test pins a defect the spec named and that was reproduced.

The two that matter most are `test_the_policy_sees_the_CHILD_at_the_next_decision` and
`test_compiler_stderr_reaches_the_prompt`: the first because a transcript with the wrong input cannot
be a training example, the second because a model cannot act on information it was never given.
"""
from __future__ import annotations

import json

import pytest

from eval import tool_agent as ta
from eval import tool_registry as tr
from eval import tool_runners as trun


def _ctx(**kw) -> ta.Context:
    base = dict(function="f", candidate="PARENT")
    base.update(kw)
    return ta.Context(**base)


# --- the state must be synchronised before the decision -------------------------

def test_the_policy_sees_the_CHILD_at_the_next_decision():
    """THE MOTIVATING DEFECT. A read-only probe observed PARENT at both decisions even though the
    intervening transform produced CHILD, because `run_episode` assigned the candidate only after
    `policy.choose`. A label attached to the wrong input is not a training example."""
    seen: list[str] = []

    class Recorder:
        name = "recorder"

        def choose(self, context, history):
            seen.append(context.candidate)
            return ("invert-mutations", {}) if not history else ("stop", {"reason": "done"})

    def transformer(context, params):
        assert context["candidate"] == "PARENT"
        return {"status": "ok", "changed": True, "exact": False, "source": "CHILD"}

    runners = {"eval.tool_runners.invert_mutations": transformer}
    ta.run_episode(_ctx(), Recorder(), budget=3, runners=runners)
    assert seen == ["PARENT", "CHILD"], f"the policy saw {seen}, not the post-transform source"


def test_each_step_records_the_state_it_was_decided_from():
    class One:
        name = "one"

        def choose(self, context, history):
            return ("invert-mutations", {}) if not history else ("stop", {"reason": "done"})

    def transformer(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "CHILD"}

    transcript = ta.run_episode(_ctx(), One(), budget=2,
                                runners={"eval.tool_runners.invert_mutations": transformer})
    first, second = transcript.steps[0], transcript.steps[1]
    assert first.pre_action_sha256 != second.pre_action_sha256, \
        "the two decisions were made from different sources and must say so"
    assert second.pre_action_sha256 == first.candidate_sha256


@pytest.mark.parametrize("exact", [False, True])
def test_last_adopted_source_and_verdict_are_exported_together(exact):
    """Both budget exhaustion and immediate exact stops must export the child."""
    import hashlib

    class One:
        name = "one"

        def choose(self, context, history):
            return "diffrepair", {}

    def transform(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "CHILD"}

    context = _ctx(
        initial_verdict={"compiled": True, "exact": False, "score": 20.0},
        compile_fn=lambda source: {"compiled": True, "exact": exact,
                                   "score": 100.0 if exact else 80.0, "diff": "CHILD_DIFF"})
    exported = []
    transcript = ta.run_episode(
        context, One(), budget=1,
        runners={"eval.tool_runners.diffrepair": transform},
        on_candidate=lambda source: exported.append((source, context.candidate)))
    assert context.candidate == "CHILD"
    assert exported == [("CHILD", "CHILD")]
    assert context.diff == "CHILD_DIFF"
    assert context.initial_verdict["source_sha256"] == hashlib.sha256(b"CHILD").hexdigest()
    assert transcript.steps[-1].candidate_sha256 == context.initial_verdict["source_sha256"]


# --- the actual tool result must reach the prompt -------------------------------

def test_compiler_stderr_reaches_the_prompt():
    """A model cannot act on an error it is never shown."""
    from eval.tool_agent_probe import observation
    text = observation("f", "int f(void){return 0;}", [
        {"action": "compile", "params": {}, "status": "failed", "changed": False, "exact": False,
         "detail": {"stderr": "cfe: Error: line 3: Syntax Error - static inline"}}])
    assert "static inline" in text and "cfe: Error" in text


def test_a_useful_tool_result_survives_rendering():
    from eval.tool_agent_probe import observation
    text = observation("f", "C", [
        {"action": "diffrepair", "params": {}, "status": "ok", "changed": True, "exact": False,
         "detail": {"diff": "-lbu v1,0x24(a0)\n+lbu v1,0(a0)"}},
        {"action": "regalloc-search", "params": {"budget": 64}, "status": "ok", "changed": False,
         "exact": False, "detail": {"best_label": "no-improvement", "compiles": 37}}])
    assert "0x24" in text, "the instruction diff is the residual and must be visible"
    assert "no-improvement" in text and "37" in text


def test_a_missing_prerequisite_names_itself_in_the_prompt():
    from eval.tool_agent_probe import observation
    result = trun.regalloc_search({"candidate": "C"}, {})
    assert result["status"] == "not-applicable" and "target_dump" in result["reason"]
    text = observation("f", "C", [{"action": "regalloc-search", "params": {}, "status":
                                   result["status"], "changed": False, "exact": False,
                                   "detail": result}])
    assert "target_dump" in text


def test_truncation_is_stated_rather_than_silent():
    from eval.tool_agent_probe import observation
    text = observation("f", "A" * 5000, [], source_chars=100)
    assert "TRUNCATED" in text and "first 100 chars" in text
    long_detail = observation("f", "C", [{"action": "compile", "params": {}, "status": "ok",
                                          "changed": False, "exact": False,
                                          "detail": {"diff": "D" * 5000}}], detail_chars=50)
    assert "TRUNCATED, 5000 chars total" in long_detail


def test_the_renderer_shows_arguments_and_remaining_budget():
    from eval.tool_agent_probe import observation
    text = observation("f", "C", [{"action": "regalloc-search", "params": {"budget": 64},
                                   "status": "ok", "changed": False, "exact": False}],
                       budget=3, tried={"diffrepair"})
    assert '"budget": 64' in text and "remaining budget: 3" in text and "diffrepair" in text


# --- the renderer must be tested THROUGH the policy, not around it ---------------

def test_the_model_policy_prompt_carries_the_tool_result():
    """THE SECOND TIME THIS BUG SHIPPED. `observation()` emitted `detail` correctly and every test
    above passed, while `ModelPolicy._render` still projected history to
    `action/status/changed/exact` -- so the live model never saw the stderr, the diff or the
    arguments, and a head-to-head run against that prompt measured the prompt rather than the
    policy. The next test in this file must call the policy, because calling the renderer directly
    is exactly what missed it."""
    from eval.tool_agent_compare import ModelPolicy

    policy = ModelPolicy(model=None, tokenizer=None, system="s", prefill="{")
    steps = [
        ta.Step(index=0, action="compile", kind="observe", params={"candidate": "C"},
                status="failed", changed=False, exact=False,
                detail={"stderr": "cfe: Error: line 9: undefined symbol osGetThreadPri"}),
        ta.Step(index=1, action="regalloc-search", kind="transform",
                params={"budget": 64, "beam": 8}, status="not-applicable", changed=False, exact=False,
                detail={"reason": "regalloc-search needs target_dump; this context has none"}),
    ]
    context = _ctx(budget_remaining=4)
    text = policy._render(context, steps)
    assert "cfe: Error" in text, "the compiler's own error text must reach the model"
    assert '"budget": 64' in text, "the arguments actually used must reach the model"
    assert "target_dump" in text, "a missing prerequisite must name itself"
    assert "remaining budget: 4" in text, "the policy must be told what it has left"


def test_the_no_effect_set_is_scoped_to_the_unchanged_source():
    """A blanket 'never call a tool twice' rule is wrong: the same tool is useful after another
    transform moved the source. Only no-effect calls since the last change are reported."""
    from eval.tool_agent_probe import tried_since_change

    stale = ta.Step(index=0, action="diffrepair", kind="transform", params={}, status="no-change",
                    changed=False, exact=False)
    moved = ta.Step(index=1, action="redraft", kind="transform", params={}, status="ok",
                    changed=True, exact=False)
    assert tried_since_change([stale]) == {"diffrepair"}
    assert tried_since_change([stale, moved]) == set(), \
        "after the source moved, an earlier no-change must stop being reported as tried"


def test_the_auto_compile_verdict_reaches_the_prompt():
    """The controller compiles a transform's output and used to KEEP only `exact` and
    `certificate_status`, discarding the fresh diff, the compiler's stderr and the score one frame
    after producing them. Under the decided contract the controller owns compilation, so if it
    throws the verdict away the model has no way to learn what the transform did."""
    from eval.tool_agent_compare import ModelPolicy

    class Redrafts:
        name = "redrafts"

        def choose(self, context, history):
            return ("redraft", {}) if not history else ("stop", {"reason": "done"})

    def transform(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "CHILD"}

    def compile_fn(source):
        return {"compiled": True, "exact": False, "certificate_status": "no-match",
                "score": 41.5, "diff": "-lbu v1,0x24(a0)\n+lbu v1,0(a0)",
                "stderr": "warning: implicit declaration of function osGetThreadPri"}

    context = _ctx(diff="OLD DIFF FOR A DIFFERENT SOURCE", compile_fn=compile_fn)
    transcript = ta.run_episode(context, Redrafts(), budget=3,
                                runners={"eval.tool_runners.redraft": transform})
    text = ModelPolicy(model=None, tokenizer=None, system="s", prefill="{")._render(
        context, transcript.steps)
    assert "0x24" in text, "the fresh residual must reach the model"
    assert "implicit declaration" in text, "the compiler's own stderr must reach the model"
    assert "41.5" in text, "the score must reach the model"
    assert "ALREADY COMPILED" in text, "the model must be told the controller already compiled this"


def test_an_uncompiled_source_is_reported_as_unverified():
    """The other half of the contract: when no verdict describes the current source, say so instead
    of leaving the model to infer it from an empty history."""
    from eval.tool_agent_probe import observation
    fresh = observation("f", "C", [])
    assert "UNVERIFIED" in fresh
    stale = observation("f", "C", [{"action": "compile", "params": {}, "status": "ok",
                                    "changed": False, "exact": False,
                                    "candidate_sha256": "0" * 64,
                                    "detail": {"certificate_status": "no-match"}}])
    assert "UNVERIFIED" in stale and "DIFFERENT source" in stale


def test_diffrepair_detail_survives_the_step_record():
    """`diffrepair` reports WHICH field moved under a nested `detail` key. Copying the result
    wholesale left it at `step.detail['detail']`, a shape the renderer never reads, so the one
    runner whose entire purpose is reporting the repair had its report invisible."""
    class Repairs:
        name = "repairs"

        def choose(self, context, history):
            return ("diffrepair", {}) if not history else ("stop", {"reason": "done"})

    def runner(context, params):
        return {"status": "ok", "changed": False, "exact": False,
                "detail": {"reason": "no offset appeared in both the diff and the struct"}}

    transcript = ta.run_episode(_ctx(), Repairs(), budget=2,
                                runners={"eval.tool_runners.diffrepair": runner})
    detail = transcript.steps[0].detail
    assert "detail" not in detail, "the nested key must be flattened, not nested again"
    assert "no offset appeared" in detail["reason"]


def test_a_transform_that_makes_the_candidate_worse_is_rejected_and_recorded():
    """THE INTAKE ACTIONS MADE THIS LOAD-BEARING. Measured on real functions, `header-context` produced
    a certified exact match on one non-compiling draft AND made two already-compiling ones worse
    (score 100 -> 0.0). The loop adopted `result["source"]` unconditionally, which was survivable only
    while every action was a refinement that could plausibly help. The rejection must be visible in the
    step: a reader has to be able to tell "the action ran and was refused" from "the action never ran",
    which is the same distinction the whole registry is built around."""
    seen: list[str] = []

    class One:
        name = "one"

        def choose(self, context, history):
            return ("diffrepair", {})

    def worse(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "WORSE", "diff": "d"}

    def compile_fn(source):
        if source == "WORSE":
            return {"compiled": False, "exact": False, "score": 0.0, "stderr": "cfe: Error"}
        return {"compiled": True, "exact": False, "score": 90.0}

    context = _ctx(diff="d", compile_fn=compile_fn,
                   initial_verdict={"compiled": True, "exact": False, "score": 90.0})
    transcript = ta.run_episode(context, One(), budget=1,
                                runners={"eval.tool_runners.diffrepair": worse},
                                on_candidate=seen.append)
    step = transcript.steps[-1]
    assert step.detail.get("adopted") is False
    assert "worse than the incumbent" in step.detail["rejected_because"]
    assert seen == [], "the incumbent candidate must be kept, not the worse one"
    assert context.candidate == "PARENT", "the context still points at the incumbent"


def test_a_transform_that_improves_the_candidate_is_adopted():
    """The other half: the gate must not turn into a refusal to change anything."""
    seen: list[str] = []

    class One:
        name = "one"

        def choose(self, context, history):
            return ("diffrepair", {})

    def better(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "BETTER"}

    def compile_fn(source):
        return {"compiled": True, "exact": False, "score": 95.0 if source == "BETTER" else 90.0}

    context = _ctx(diff="d", compile_fn=compile_fn,
                   initial_verdict={"compiled": True, "exact": False, "score": 90.0})
    transcript = ta.run_episode(context, One(), budget=1,
                                runners={"eval.tool_runners.diffrepair": better},
                                on_candidate=seen.append)
    assert transcript.steps[-1].detail.get("adopted") is True
    assert seen == ["BETTER"]


# --- receipts --------------------------------------------------------------------

def test_a_terminal_action_is_recorded_not_merely_obeyed():
    class Stops:
        name = "stops"

        def choose(self, context, history):
            return "stop", {"reason": "nothing further can help"}

    transcript = ta.run_episode(_ctx(), Stops(), budget=3, runners={})
    assert transcript.steps and transcript.steps[0].action == "stop"
    assert transcript.steps[0].status == "terminal"
    assert "nothing further" in transcript.stop_reason


def test_an_invalid_proposal_is_retained_with_its_reason():
    class Bad:
        name = "bad"

        def choose(self, context, history):
            return "burn-it-down", {}

    transcript = ta.run_episode(_ctx(), Bad(), budget=2, runners={})
    assert transcript.steps[-1].status == "invalid" and "unknown action" in \
        transcript.steps[-1].detail["error"]


# --- runner signatures against the REAL APIs ------------------------------------

def test_regalloc_search_is_wired_to_the_real_two_argument_callback():
    """The real `solver.regalloc_search.search` calls `compile_candidate(source, label)` and returns
    an `Outcome` dataclass. The first version assumed a dict and a one-argument callback."""
    from solver import regalloc_search as rs
    import inspect
    params = list(inspect.signature(rs.search).parameters)
    assert params[:4] == ["function", "source", "compile_candidate", "target_dump"]
    assert hasattr(rs, "Compiled") and hasattr(rs, "Outcome")
    fields = set(getattr(rs.Outcome, "__dataclass_fields__", {}))
    assert {"exact", "best_source", "best_label", "compiles"} <= fields


def test_a_candidate_without_a_target_dump_is_declined_not_guessed():
    result = trun.regalloc_search({"candidate": "C", "compile_fn": lambda s: {}}, {})
    assert result["status"] == "not-applicable" and "target_dump" in result["reason"]


def test_an_unchanged_redraft_is_not_reported_as_a_change(tmp_path):
    """`changed: True` unconditionally would teach a policy that an action did something when the
    draft it produced was byte-identical to the candidate already under consideration.

    THE SECOND VERSION OF THIS TEST CAUGHT NOTHING, which is why it is written against the canonical
    producer now. It patched `subprocess.run`, so it asserted that the runner returned whatever the m2c
    binary printed -- and the m2c binary prints a BARE draft while the candidate under consideration
    comes from `workspace.m2c_draft`, which sanitizes the assembly and prepends `#include "common.h"`.
    The two could never be equal, so `changed` was true on 40 of 40 states of a real frame
    (`eval/results/intake-20260921/class-control.json`) purely because the wrapper had been deleted.
    A test that supplies the shell-out cannot see a producer mismatch.
    """
    from solver import workspace

    repo = tmp_path
    (repo / "nonmatchings" / "f").mkdir(parents=True)
    (repo / "nonmatchings" / "f" / "target.s").write_text("glabel f\nendlabel f\n", encoding="utf-8")
    source = 'int f(void) { return 0; }\n'
    original = workspace.m2c_draft
    try:
        workspace.m2c_draft = lambda ws: source
        result = trun.redraft({"target_asm_path": "target.s", "repo": str(repo),
                               "function": "f", "candidate": source}, {})
        assert result["status"] == "no-change" and result["changed"] is False
        assert "reproduced" in result["reason"]
        result2 = trun.redraft({"target_asm_path": "target.s", "repo": str(repo),
                                "function": "f", "candidate": "different"}, {})
        assert result2["changed"] is True
    finally:
        workspace.m2c_draft = original


def test_redraft_declines_when_the_workspace_has_no_target_assembly(tmp_path):
    """A MISSING INPUT, named. The first version shelled out to m2c with a path that need not exist and
    reported whatever the shell said, which is not the same thing as a tool that was handed its input."""
    result = trun.redraft({"target_asm_path": "target.s", "repo": str(tmp_path),
                           "function": "absent", "candidate": "int f(void){return 0;}\n"}, {})
    assert result["status"] == "not-applicable" and result["changed"] is False
    assert "target.s" in result["reason"]


def test_redraft_reports_a_producer_that_returns_nothing(tmp_path):
    """`no-change` would be wrong: the producer failed, which is not the same as agreeing with the
    candidate, and a policy reading `no-change` would move on instead of escalating."""
    from solver import workspace

    (tmp_path / "nonmatchings" / "f").mkdir(parents=True)
    (tmp_path / "nonmatchings" / "f" / "target.s").write_text("glabel f\nendlabel f\n", encoding="utf-8")
    original = workspace.m2c_draft
    try:
        workspace.m2c_draft = lambda ws: ""
        result = trun.redraft({"target_asm_path": "target.s", "repo": str(tmp_path),
                               "function": "f", "candidate": "int f(void){return 0;}\n"}, {})
        assert result["status"] == "failed" and result["changed"] is False
    finally:
        workspace.m2c_draft = original


def test_the_verdict_reader_uses_the_real_attempt_fields():
    """`Attempt` exposes `compiler_stderr`, not `stderr`, and carries no fault profile. Guessing
    fields meant the compiler's error text never reached the prompt."""
    from eval.tool_agent_run import _attempt_to_verdict

    class FakeAttempt:
        compiled = False
        score = 0.0
        exact = False
        diff = ""
        compiler_stderr = "cfe: Error: line 1: Syntax Error"
        raw_output = ""
        receipt_id = 7
        verification = {"status": "object_sections_differ"}
        frontend = {"passed": True}
        source_attribution = None

    verdict = _attempt_to_verdict(FakeAttempt())
    assert verdict["stderr"] == "cfe: Error: line 1: Syntax Error"
    assert verdict["receipt_id"] == 7 and verdict["frontend"] == {"passed": True}
    assert "faults" not in verdict, "no fault class is invented from a dataclass that has none"
    assert _attempt_to_verdict(object())["stderr"] == ""
