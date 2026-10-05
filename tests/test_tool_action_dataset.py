"""The dataset's labels must be recheckable without a model, and must not leak the future.

Each test here pins one property the spec asks for by name: paired cases where a change in the
observation changes the right action; acceptable sets rather than one fabricated optimum; declared
prerequisites that match the real runners; split isolation by family; and no tool result from after
the decision appearing in the prompt that precedes it.
"""
from __future__ import annotations

import json

import pytest

from eval import tool_action_dataset as tad
from eval import tool_runners as trun
from eval.tool_agent import Context, Step
from eval.tool_registry import ACTIONS


def test_declared_prerequisites_match_the_runners():
    """`Action.needs` is the registry's declaration; a runner states the same thing at call time when
    it returns `not-applicable: the context does not carry X`. A declaration that is not checked
    against the code is a lie, and this module briefly kept its own second copy of the table."""
    for name, action in ACTIONS.items():
        if not action.runner:
            continue
        result = action.resolve()({}, {})
        assert result["status"] == "not-applicable", f"{name} unexpectedly ran on an empty context"
        missing = result["reason"].split("does not carry", 1)[1].strip()
        assert set(part.strip() for part in missing.split(",")) == set(action.needs), \
            f"{name}: runner requires {missing!r}, registry declares {action.needs}"


def _by_exercise(split: str = "train") -> dict:
    return {row["evidence"]["exercise"]: row for row in tad.procedural_records(split)}


def test_a_verified_source_makes_compile_unacceptable_and_an_unverified_one_requires_it():
    """THE PAIRED CASE. The two states differ in exactly one observable field -- whether a verdict
    describes the current source -- and the right action changes with it."""
    rows = _by_exercise()
    fresh, verified = rows["fresh_state"], rows["verified_state"]
    assert [a["action"] for a in fresh["acceptable"]] == ["compile"]
    assert "compile" not in [a["action"] for a in verified["acceptable"]], \
        "a source that already carries a verdict must not be compiled again"

    def prompt(row):
        return row["messages"][1]["content"]

    assert "UNVERIFIED" in prompt(fresh)
    assert "ALREADY COMPILED" in prompt(verified)


def test_a_no_effect_action_is_forbidden_only_until_the_source_moves():
    """The second paired case: same action, same no-change result, different verdict, because an
    intervening transform moved the source. A blanket ban on repeating a tool would fail this."""
    rows = _by_exercise()
    stale, moved = rows["no_effect_unchanged"], rows["retry_after_change"]
    assert "diffrepair" in stale["forbidden"]
    assert "diffrepair" not in [a["action"] for a in stale["acceptable"]]
    assert "diffrepair" not in moved["forbidden"]
    assert "diffrepair" in [a["action"] for a in moved["acceptable"]]


def test_terminal_actions_are_recorded_with_a_reason_and_are_alone_when_they_are_certain():
    rows = _by_exercise()
    for name, reason in (("exact_state", "certified match"), ("budget_exhausted", "budget exhausted"),
                         ("no_progress_left", "justified no-progress")):
        row = rows[name]
        assert row["label_confidence"] == "certain"
        assert [a["action"] for a in row["acceptable"]] == ["stop"], name
        assert row["acceptable"][0]["params"]["reason"] == reason


def test_blocked_infrastructure_offers_stop_without_requiring_it():
    """Spec §2: blocked infrastructure is a legal terminal reason -- and so is trying another route.
    Refusing to stop would teach that a broken tool means the episode is over; requiring stop would
    teach the opposite."""
    rows = _by_exercise()
    row = rows["infrastructure_blocked"]
    actions = [a["action"] for a in row["acceptable"]]
    assert actions[-1] == "stop" and len(actions) > 1
    assert row["acceptable"][-1]["params"]["reason"] == "blocked infrastructure"
    assert row["label_confidence"] == "acceptable-set"


def test_the_completion_is_always_one_of_the_acceptable_actions():
    for row in tad.procedural_records("train"):
        assert row["action"] in row["acceptable"]
        assert row["action"]["action"] not in row.get("forbidden", []), row["id"]
        assert json.loads(row["completion"]) == row["action"], "completion is the action, verbatim"


def test_no_tool_result_from_after_the_decision_reaches_the_prompt():
    """A training example whose input already contains the label is not a decision. The prompt is
    rendered from the steps BEFORE the decision, so the number of rendered step lines must equal the
    number of recorded steps -- a line for the action being trained, or for its result, would be one
    too many."""
    import re

    for row in tad.procedural_records("train"):
        prompt = row["messages"][1]["content"]
        rendered = len(re.findall(r"^  \S+ \{", prompt, flags=re.MULTILINE))
        assert rendered == row["evidence"]["steps_rendered"], \
            f"{row['id']}: prompt shows {rendered} steps, record has " \
            f"{row['evidence']['steps_rendered']}"

    exact_rows = [r for r in tad.procedural_records("train")
                  if r["label_rule"] == "certificate passed"]
    assert exact_rows and "EXACT" in exact_rows[0]["messages"][1]["content"], \
        "the evidence that justifies stopping must be visible in the state it stops on"


def test_the_observation_the_record_stores_is_the_one_the_renderer_produces():
    """One renderer, so a record cannot contain a prompt shape the live policy would never see."""
    from eval.tool_agent_probe import observation
    from eval.tool_agent_probe import step_view

    rows = tad.procedural_records("train")
    row = rows[0]
    steps = [Step(index=0, action="compile", kind="observe", params={}, status="failed",
                  changed=False, exact=False,
                  detail={"stderr": "cfe: Error: line 3", "certificate_status": "no-match"},
                  candidate_sha256=tad._sha(row["id"]))]
    rendered = observation("f", "C", [step_view(s) for s in steps], budget=2)
    assert "cfe: Error" in rendered
    assert rendered.startswith("function: f")


def test_splits_keep_families_together_and_pin_the_used_functions_to_dev():
    names = ["osGetThreadPri", "osSetThreadPri", "funcA", "funcB", "initX", "menuY", "audioZ"]
    splits = tad.freeze_splits(names, dev={"osGetThreadPri"}, test_percent=0)
    assert splits["dev"] == ["osGetThreadPri", "osSetThreadPri"], \
        "a sibling must follow its family into dev"
    assert set(splits["train"]) | set(splits["dev"]) | set(splits["test"]) == set(names)
    assert not (set(splits["train"]) & set(splits["test"]))


def test_the_family_rule_ignores_leading_underscores():
    assert tad.family_of("__osAlloc") == tad.family_of("osFree") == "os"
    assert tad.family_of("__ll_mul") == "ll"


def test_a_correction_record_names_the_wrong_choice_and_a_verified_alternative():
    """The correction round (spec §5): a state the LEARNED policy actually visited, where its own
    choice was not acceptable. The record must keep the wrong choice as evidence -- so the receipt
    says what the policy did -- and train on an acceptable one, never on 'not that'."""
    from eval.tool_action_dataset import correction_records

    verified = {"index": 0, "kind": "observe", "action": "compile", "params": {},
                "status": "ok", "changed": False, "exact": False,
                "detail": {"certificate_status": "no-match", "compiled": True},
                "candidate_sha256": "x", "pre_action_sha256": "x", "cancelled": False}
    context = {"candidate": "C", "diff": None, "source_path": "base.c", "target_asm_path": "t.s",
               "target_dump": None, "compile_fn": True}
    steps = [dict(verified)]
    steps[0]["candidate_sha256"] = tad._sha("C")
    snapshot = {"candidate": "C", "steps": steps, "budget_remaining": 3,
                "context_keys": context, "target_dump": None}
    specs = [{"function": "f", "snapshot": snapshot,
              "chosen": {"action": "diffrepair", "params": {}}, "outcomes": []}]
    rows = correction_records(specs, split="train")
    assert len(rows) == 1, "a state where the policy was right must not produce a correction"
    row = rows[0]
    assert row["kind"] == "correction" and row["label_source"] == "correction"
    assert row["evidence"]["chosen_by_policy"]["action"] == "diffrepair"
    assert row["action"]["action"] in [a["action"] for a in row["acceptable"]]
    assert "diffrepair" not in [a["action"] for a in row["acceptable"]], \
        "the correction must not include the action the policy got wrong"


# --- receipts --------------------------------------------------------------------

def test_a_runner_that_claims_exactness_without_the_certificate_is_not_believed():
    """Spec §7: a fabricated exact flag must not bypass certificate validation. The claim is recorded
    so the receipt shows what was asserted, and the episode continues."""
    from eval.tool_agent import run_episode

    class Claims:
        name = "claims"

        def choose(self, context, history):
            return ("diffrepair", {})

    def liar(context, params):
        return {"status": "ok", "changed": False, "exact": True,
                "source": context["candidate"]}

    transcript = run_episode(Context(function="f", candidate="C"), Claims(), budget=1,
                             runners={"eval.tool_runners.diffrepair": liar})
    assert transcript.exact is False, "exactness comes from the object certificate, not a tool"
    assert transcript.steps[0].exact is False
    assert transcript.steps[0].detail.get("exact_claimed") is True


def test_a_runner_that_raises_is_recorded_and_does_not_kill_the_episode():
    from eval.tool_agent import run_episode

    class Calls:
        name = "calls"
        seen = 0

        def choose(self, context, history):
            self.seen += 1
            return ("uopt-trace", {}) if self.seen == 1 else ("stop", {"reason": "gave up"})

    def explodes(context, params):
        raise AttributeError("'NoneType' object has no attribute 'rewrite'")

    transcript = run_episode(Context(function="f", candidate="C"), Calls(), budget=2,
                             runners={"eval.tool_runners.uopt_trace": explodes})
    assert transcript.steps[0].status == "runner-error"
    assert "AttributeError" in transcript.steps[0].detail["reason"]
    assert transcript.steps[1].action == "stop", "the policy still gets to decide"
