"""One small, deterministic end-to-end transition through the real loop.

WHAT THIS IS FOR. The brief asks for "one small, deterministic end-to-end transition before model work".
The pieces have each been tested in isolation: the registry resolves a runner, the runner reads the
`(namespace, params)` contract, the loop adopts or rejects a child, and the oracle's verdict is what
decides. This file drives all of them TOGETHER, with a stub compiler and no toolchain, so the CONTRACT
between them is what is under test rather than any one implementation.

It runs the real `run_episode`, resolves the action through the real `eval.tool_registry.ACTIONS` table,
calls the real registered runner for that action, and asserts the four things the rest of the system relies
on:

    the step records the source it actually produced and the verdict of THAT source
    a rejected child never becomes the context's candidate or diff
    the adopted child does
    the transcript is serialisable, so it can be logged and trained on

An action is used for real, not stubbed, on the one function whose input this contract can supply without
a compiler: `resolve_placeholders` rewrites `?` placeholders in the source text and needs nothing else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.tool_agent import Context, run_episode                            # noqa: E402
from eval.tool_registry import ACTIONS                                      # noqa: E402

ACTION = "resolve-placeholders"
# THE REAL PLACEHOLDER FORM. m2c writes an undeclared stack slot as a bare `? name;` line; `arg0[?]` is an
# array index and the resolver correctly declines it (`no-change`, `placeholders: []`). The first version
# of this test used the wrong form and asserted a transition that never happened -- which is the same
# "declines silently" shape this project keeps finding, caught here by the assertion rather than by a rate.
DRAFT = "s32 probe(void) {\n    ? sp1C;\n    return sp1C;\n}\n"
BETTER = "s32 probe(void) {\n    s32 sp1C;\n    return sp1C;\n}\n"


def _verdicts(mapping):
    def compile_fn(source: str) -> dict:
        return dict(mapping.get(source, {"compiled": True, "exact": False, "score": 0.0,
                                         "diff": "", "stderr": "", "certificate_status": None}))
    return compile_fn


class Once:
    """Take the one action, then stop. The interface is the loop's, not this file's."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[dict] = []

    def choose(self, context, steps) -> tuple[str, dict]:
        self.calls += 1
        self.seen.append({"candidate": context.candidate, "diff": context.diff})
        return (ACTION, {}) if self.calls == 1 else ("stop", {"reason": "one action"})


def _runners():
    entry = ACTIONS[ACTION]
    runner = entry.resolve()
    assert callable(runner), f"{ACTION} did not resolve to a callable runner"
    return {entry.runner: runner}


def test_the_registry_resolves_the_action_the_loop_names():
    entry = ACTIONS[ACTION]
    assert entry.runner and callable(entry.resolve())


def test_one_transition_end_to_end_reaches_the_oracle_verdict():
    """The action is registered, the runner rewrites the draft, the oracle's verdict is attached to the
    step that produced it, and the adopted source becomes the context."""
    context = Context(function="probe", candidate=DRAFT, compile_fn=_verdicts({BETTER: {
        "compiled": True, "exact": False, "score": 91.0, "diff": "+return arg0[0];",
        "stderr": "", "certificate_status": "object_sections_differ"}}),
        target_asm_path="target.s", repo=".", target="build/src/probe.o",
        initial_verdict={"compiled": False, "exact": False, "score": 0.0, "diff": "",
                         "stderr": "cfe: Error: Syntax Error"})
    policy = Once()
    transcript = run_episode(context, policy, budget=2, runners=_runners())

    transform = [step for step in transcript.steps if step.action == ACTION]
    assert len(transform) == 1, [step.action for step in transcript.steps]
    step = transform[0]
    assert step.status == "ok" and step.changed is True
    assert step.detail.get("score") == 91.0, "the verdict of the candidate the step produced"
    assert context.candidate == BETTER, "the adopted candidate must become the context"
    assert transcript.stop_reason
    # THE SECOND DECISION SEES THE ADOPTED STATE, not a stale one.
    assert policy.seen[1]["candidate"] == BETTER


def test_a_rejected_child_never_becomes_the_context():
    """The boundary the audit broke: the child's verdict must not travel under the parent's identity, and
    the next decision must see the parent's own state.

    The child has to be worse than the INCUMBENT for this to be a rejection at all: a child that compiles
    beats a parent that does not, whatever the score. So the parent here already compiles at 80 and the
    child compiles at 10.
    """
    context = Context(function="probe", candidate=DRAFT, compile_fn=_verdicts({BETTER: {
        "compiled": True, "exact": False, "score": 10.0, "diff": "worse", "stderr": "",
        "certificate_status": "object_sections_differ"}}),
        target_asm_path="target.s", repo=".", target="build/src/probe.o", diff="parent-diff",
        initial_verdict={"compiled": True, "exact": False, "score": 80.0, "diff": "parent-diff",
                         "stderr": ""})
    policy = Once()
    transcript = run_episode(context, policy, budget=2, runners=_runners())
    step = [s for s in transcript.steps if s.action == ACTION][0]
    assert context.candidate == DRAFT, "a worse child was adopted"
    assert context.diff == "parent-diff", "the rejected child's diff became the context's diff"
    assert policy.seen[1]["candidate"] == DRAFT
    assert step.detail.get("attempted", {}).get("score") == 10.0, \
        "the rejected child's own verdict is kept under its own identity"
    assert step.detail["attempted"]["adopted"] is False
    assert step.attempted_sha256 and step.attempted_sha256 != step.candidate_sha256


def test_the_transcript_serialises_because_it_is_the_training_record():
    context = Context(function="probe", candidate=DRAFT, compile_fn=_verdicts({BETTER: {
        "compiled": True, "exact": False, "score": 91.0, "diff": "d", "stderr": "",
        "certificate_status": None}}),
        target_asm_path="target.s", repo=".", target="build/src/probe.o",
        initial_verdict={"compiled": False, "exact": False, "score": 0.0, "diff": "", "stderr": "x"})
    transcript = run_episode(context, Once(), budget=2, runners=_runners())
    payload = json.dumps([step.as_dict() for step in transcript.steps])
    assert ACTION in payload and len(payload) > 50
