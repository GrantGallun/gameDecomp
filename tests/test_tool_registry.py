"""The action space and the no-model agent loop.

Three properties carry the design and each has a test that FIRES on the motivating failure:

  the space is finite and validatable   a free-form action cannot be replayed or trained on
  a missing input is named, not silent  "declined" and "did nothing" must be distinguishable
  the loop holds no domain knowledge    an ordering buried in the loop is unattributable and
                                        untrainable; it belongs in a policy

The transcript tests matter most: the transcript IS the training set for the tool-calling policy, so
its label has to come from the certificate and its shape has to be stable enough to say which agent
produced it.
"""
from __future__ import annotations

import json

import pytest

from eval import tool_agent as ta
from eval import tool_registry as tr
from eval import tool_runners as trun


# --- the action space -----------------------------------------------------------

def test_the_space_is_finite_and_every_action_is_fully_described():
    space = tr.action_space()
    assert space["actions"], "an empty repertoire would make every policy a no-op"
    for action in space["actions"]:
        assert action["name"] and action["kind"] in tr.KINDS and action["summary"]
        assert isinstance(action["params"], list)
        assert "wired" in action and "needs" in action


def test_the_space_contains_the_tools_this_project_actually_has():
    names = set(tr.ACTIONS)
    for required in ("compile", "diffrepair", "regalloc-search", "invert-mutations", "stop"):
        assert required in names, f"{required} is missing from the repertoire"


def test_an_unknown_action_is_refused_with_the_space_named():
    with pytest.raises(ValueError, match="unknown action"):
        tr.validate("delete-everything", {})


def test_an_unknown_parameter_is_refused_rather_than_ignored():
    """Silently dropping a parameter would let a policy believe it asked for something it did not."""
    with pytest.raises(ValueError, match="unknown parameter"):
        tr.validate("regalloc-search", {"budget": 8, "temperature": 0.7})


def test_parameter_bounds_are_enforced():
    assert tr.validate("regalloc-search", {"budget": 8})["params"]["budget"] == 8
    with pytest.raises(ValueError, match="above the maximum"):
        tr.validate("regalloc-search", {"budget": 10_000_000})
    with pytest.raises(ValueError, match="below the minimum"):
        tr.validate("regalloc-search", {"budget": 0})
    with pytest.raises(ValueError, match="not one of"):
        tr.validate("uopt-trace", {"level": "9"})


def test_defaults_fill_in_and_types_are_checked():
    assert tr.validate("invert-mutations", {})["params"] == {"combinations": True}
    assert tr.validate("uopt-trace", {})["params"] == {"level": "5"}
    with pytest.raises(ValueError, match="expected an integer"):
        tr.validate("regalloc-search", {"budget": "lots"})


def test_policy_output_is_parsed_and_validated_as_a_whole():
    assert tr.validate_json('{"action": "compile"}')["action"] == "compile"
    assert tr.validate_json('{"action": "stop", "params": {"reason": "done"}}')["params"] == \
        {"reason": "done"}
    with pytest.raises(ValueError, match="not JSON"):
        tr.validate_json("I will now repair the struct layout")
    with pytest.raises(ValueError, match="action"):
        tr.validate_json('{"params": {}}')


def test_the_space_is_enumerable_and_reports_unbounded_parameters_honestly():
    """A free integer has no upper bound to expand, and an enumeration that silently dropped it
    would misreport the size of the space."""
    rows = tr.enumerate_actions()
    assert rows, "the motivating case: the space must expand to something"
    actions = {row["action"] for row in rows}
    assert "diffrepair" in actions and "uopt-trace" in actions
    levels = {row["params"].get("level") for row in rows if row["action"] == "uopt-trace"}
    assert levels == {"5", "6"}, "choices must actually be expanded"
    bounded = [row for row in rows if row["action"] == "compile"]
    assert {row["params"]["save"] for row in bounded} == {True, False}


def test_an_unwired_action_fails_loudly_rather_than_returning_nothing():
    """The silent-decline guard: a declared-but-unwired action must not look like a tool with no
    work to do."""
    unwired = tr.unwired_actions()
    for name in unwired:
        with pytest.raises(RuntimeError, match="DECLARED BUT NOT WIRED"):
            tr.ACTIONS[name].resolve()


def test_only_stop_is_terminal_and_compile_never_transforms():
    kinds = {name: action.kind for name, action in tr.ACTIONS.items()}
    assert kinds["stop"] == tr.TERMINAL
    assert kinds["compile"] == tr.OBSERVE and kinds["uopt-trace"] == tr.OBSERVE
    assert kinds["diffrepair"] == tr.TRANSFORM


# --- runners --------------------------------------------------------------------

def test_a_missing_input_is_named_not_silently_skipped():
    result = tr.ACTIONS["diffrepair"].resolve()({"candidate": "int f(void){return 0;}"}, {})
    assert result["status"] == "not-applicable" and result["changed"] is False
    assert "diff" in result["reason"], "the reason must name what was missing"


def test_compile_reports_the_certificate_not_a_judgement_of_its_own():
    seen = {}

    def compile_fn(source):
        seen["source"] = source
        return {"compiled": True, "exact": True, "certificate_status": "object_sections_exact"}

    result = tr.ACTIONS["compile"].resolve()({"candidate": "C", "compile_fn": compile_fn}, {})
    assert result["exact"] is True and result["certificate_status"] == "object_sections_exact"
    assert seen["source"] == "C"


def test_invert_mutations_reports_candidates_rather_than_choosing_one():
    """Choosing would mean the runner deciding exactness by proxy."""
    candidate = "s32 f(void) {\n    s16 a;\n    s16 b;\n\n    return a - b;\n}"
    result = tr.ACTIONS["invert-mutations"].resolve()({"candidate": candidate}, {"combinations": False})
    assert result["status"] == "ok" and result["count"] >= 1
    assert all("source" in item for item in result["candidates"])
    assert result["exact"] is False, "a runner never claims exactness"


# --- the loop and the scripted policy -------------------------------------------

def _ctx(**kw) -> ta.Context:
    base = dict(function="f", candidate="int f(void) { return 0; }")
    base.update(kw)
    return ta.Context(**base)


def test_the_scripted_policy_is_deterministic_and_runs_each_action_at_most_once():
    """The contract is "compile only while the current source is UNVERIFIED", so a compile step
    counts only when it says which source it verified -- which the loop always records. The bare
    step this test used to pass could not establish that, and the policy was right to recompile:
    a verdict with no source identity is exactly the stale-diagnostic case."""
    policy = ta.ScriptedPolicy()
    context = _ctx()
    unverified = [ta.Step(index=0, action="compile", kind="observe", params={}, status="ok",
                          changed=False, exact=False)]
    assert policy.choose(context, unverified)[0] == "compile"

    verified = [ta.Step(index=0, action="compile", kind="observe", params={}, status="ok",
                        changed=False, exact=False,
                        detail={"certificate_status": "object_sections_differ", "compiled": True},
                        candidate_sha256=ta._sha(context.candidate))]
    first = policy.choose(context, verified)
    second = policy.choose(context, verified)
    assert first == second, "the scripted policy must be deterministic"
    assert first[0] != "compile", "the current source already carries a verdict"


def test_an_episode_records_a_transcript_labelled_by_the_certificate():
    calls = []

    def compile_fn(source):
        calls.append(source)
        return {"compiled": True, "exact": source == "FIXED",
                "certificate_status": "object_sections_exact" if source == "FIXED" else
                                       "object_sections_differ"}

    def fake_inverter(context, params):
        return {"status": "ok", "changed": True, "exact": False, "source": "FIXED"}

    runners = {"eval.tool_runners.compile_candidate": trun.compile_candidate,
               "eval.tool_runners.invert_mutations": fake_inverter}
    transcript = ta.run_episode(_ctx(compile_fn=compile_fn), ta.ScriptedPolicy(),
                                budget=6, runners=runners)
    assert transcript.exact is True
    assert transcript.stop_reason == "certified match"
    assert transcript.policy == "scripted" and transcript.agent_version == ta.AGENT_VERSION
    assert [s.action for s in transcript.steps][:2] == ["compile", "invert-mutations"]
    assert transcript.steps[1].exact is True, "the step records the CERTIFICATE's verdict"
    assert calls, "the loop must actually compile the produced candidate"


def test_the_transcript_is_json_serialisable_and_names_its_generator():
    transcript = ta.run_episode(_ctx(), ta.ScriptedPolicy(), budget=1, runners={})
    payload = json.loads(transcript.to_jsonl())
    assert payload["agent_version"] == ta.AGENT_VERSION
    assert payload["policy"] == "scripted"
    assert isinstance(payload["steps"], list) and payload["steps"][0]["action"] == "compile"
    assert "source" not in payload["steps"][0]["detail"], "a transcript must not carry whole sources"


def test_the_budget_bounds_actions_and_the_episode_stops_when_it_is_spent():
    transcript = ta.run_episode(_ctx(), ta.ScriptedPolicy(), budget=2, runners={})
    assert len(transcript.steps) <= 2
    assert transcript.exact is False


def test_an_invalid_proposal_ends_the_episode_with_the_reason_recorded():
    class BadPolicy:
        name = "bad"

        def choose(self, context, history):
            return "delete-the-repo", {}

    transcript = ta.run_episode(_ctx(), BadPolicy(), budget=3, runners={})
    assert transcript.steps[-1].status == "invalid"
    assert "invalid action" in transcript.stop_reason


def test_an_unwired_action_is_recorded_as_unwired_not_as_a_quiet_no_op():
    class OnlyHoles:
        name = "holes"

        def choose(self, context, history):
            return ("redraft", {}) if not history else ("stop", {"reason": "done"})

    transcript = ta.run_episode(_ctx(), OnlyHoles(), budget=2,
                                runners={"eval.tool_runners.redraft": trun.redraft})
    assert transcript.steps[0].status == "not-applicable", \
        "redraft has no target_asm_path here, and must say so rather than look inert"
    assert "target_asm_path" in transcript.steps[0].detail["reason"]
