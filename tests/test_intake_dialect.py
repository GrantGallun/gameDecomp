"""`m2c_dialect`: it FIRES on the spelling it owns and abstains BY NAME on the two it does not.

THE RULE THIS TEST EXISTS FOR. CLAUDE.md's fifth rule -- every generator needs a test asserting it
FIRES on its motivating residual, because declining is the easy half to get right and the easy half to
test, which is why every past failure landed on the other side.

THE RESIDUAL. `eval/results/intake-20260921/name-triage.json`, bucket `m2c-dialect`: 18 states, one
spelling each -- `unaligned` x9, `sp` x6, `bitwise` x3. Every fixture below is the shape the frame's own
candidate carries (`nonmatchings/<function>/base.c`, the bytes `draft_sha256` pins), not an invention:

    alSynSetPan          temp_v0->data.f = (bitwise f32) pan;
    alSynSetVol          temp_v0->data.f = (bitwise f32) volume;
                         sp1C->moredata.f = (bitwise f32) _timeToSamples(synth, t);
    __osContGetInitData  spC.unk0 = (s32) (unaligned s32) sp14->unk0;
    ldiv                 arg0->unk0 = (s32) sp->unk0;

AND WHY THE BUCKET IS NOT ONE FIX. `LOOP-1.md` queued all 18 as "a pure rewrite". `unaligned` is not
one: `__osContGetInitData`'s target is `lwl $at, 0x0($t9)` / `lwr $at, 0x3($t9)` into `sp+0xC`, an
unaligned PAIR that IDO emits because the copied-from type is not word-aligned. Dropping the marker
compiles and emits a plain `lw`, so a state "converted" that way moves AWAY from exact. That is an
alignment fact owned by layout, and `test_the_unaligned_marker_is_refused_by_name` is what stops this
action from being taught to guess it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import intake_runners                                               # noqa: E402
from eval.tool_runners import FAILED, NO_CHANGE, NOT_APPLICABLE, OK           # noqa: E402


def _context(candidate: str, function: str = "f", **extra):
    context = {"candidate": candidate, "function": function, "repo": ".",
               "target": "build/src/f.o"}
    context.update(extra)
    return context


# The alSynSetPan shape: a scalar parameter reinterpreted as f32 at the point of use.
BITWISE = """\
void f(s32 pan, Voice *temp_v0) {
    temp_v0->data.f = (bitwise f32) pan;
}
"""

# The alSynSetVol shape: one lowerable site and one CALL site, which needs a return type the
# candidate does not declare.
BITWISE_PARTIAL = """\
void f(s32 volume, Voice *temp_v0, Synth *synth, s32 t) {
    temp_v0->data.f = (bitwise f32) volume;
    temp_v0->moredata.f = (bitwise f32) _timeToSamples(synth, t);
}
"""

# The __osContGetInitData shape: an unaligned word read, which is an alignment fact.
UNALIGNED = """\
void f(Pak *sp14, Block spC) {
    spC.unk0 = (s32) (unaligned s32) sp14->unk0;
    spC.unk4 = (s32) (unaligned s32) sp14->unk4;
}
"""

# The ldiv shape: m2c naming the stack frame it never declared.
STACK_POINTER = """\
void f(Block *arg0) {
    arg0->unk0 = (s32) sp->unk0;
    arg0->unk4 = (s32) sp->unk4;
}
"""


def test_it_fires_on_the_bitwise_residual():
    """THE MOTIVATING CASE. `lower_bitcasts` owns this and was never reachable as an action."""
    result = intake_runners.m2c_dialect(_context(BITWISE), {})
    assert result["status"] == OK, result["reason"]
    assert result["changed"] is True
    assert result["detail"]["plans"], "the pass has to report the plan it applied"
    plan = result["detail"]["plans"][0]
    assert (plan["source_type"], plan["target_type"], plan["operand"]) == ("s32", "f32", "pan"), plan
    # The spelling is GONE, which is the only thing the front end cares about.
    assert "bitwise" not in result["source"]
    assert "union { s32 from; f32 to; }" in result["source"]
    assert result["detail"]["unresolved_names"] == []


def test_a_partial_lowering_reports_the_site_it_left():
    """`alSynSetVol` lowers one site and keeps the call form; the remainder must be visible.

    This is the composition signal: the call needs a prototype from the triage's `known-function`
    bucket before its source type is known. A receipt that hid the remainder would make this state
    look converted while the front end still rejects it.
    """
    result = intake_runners.m2c_dialect(_context(BITWISE_PARTIAL), {})
    assert result["status"] == OK, result["reason"]
    assert result["changed"] is True
    assert len(result["detail"]["plans"]) == 1, result["detail"]["plans"]
    assert "_timeToSamples" in result["source"], "the call site is untouched, not deleted"
    assert result["detail"]["unresolved_names"] == ["bitwise"], "the survivor is named"


def test_the_unaligned_marker_is_refused_by_name():
    """NOT a spelling fault, so this action must not touch it -- and must say so."""
    result = intake_runners.m2c_dialect(_context(UNALIGNED), {})
    assert result["status"] == NO_CHANGE
    assert result["changed"] is False
    assert result["source"] == UNALIGNED, "the candidate is returned unmodified"
    assert "unaligned" in result["reason"]
    assert "alignment" in result["reason"], result["reason"]
    assert result["detail"]["declined"] == ["unaligned"]
    assert result["detail"]["declined_reason"] == result["reason"]


def test_the_stack_pointer_spelling_is_refused_by_name():
    """`sp` needs a declared frame local; substitution is not the repair."""
    result = intake_runners.m2c_dialect(_context(STACK_POINTER), {})
    assert result["status"] == NO_CHANGE
    assert result["changed"] is False
    assert result["source"] == STACK_POINTER
    assert "sp" in result["detail"]["declined"]
    assert result["detail"]["declined_reason"], "a decline on purpose carries its reason"


def test_a_candidate_with_no_dialect_spelling_says_that_instead():
    """The ordinary no-op reads differently from a decline on purpose."""
    result = intake_runners.m2c_dialect(_context("void f(void) { gX = 0; }\n"), {})
    assert result["status"] == NO_CHANGE
    assert result["changed"] is False
    assert result["reason"] == "the candidate carries no m2c dialect spelling"
    assert "declined" not in result["detail"]


def test_a_missing_input_is_named_rather_than_silently_declined():
    """This registry's contract: a missing input returns not-applicable NAMING it."""
    result = intake_runners.m2c_dialect({"candidate": BITWISE}, {})
    assert result["status"] == NOT_APPLICABLE
    assert "function" in result["reason"], result["reason"]


def test_a_raising_pass_is_reported_as_failed_not_as_no_change(monkeypatch):
    """A crash must not read as an action with nothing to do."""
    from solver import m2c_context

    def boom(source, function):
        raise ValueError("no definition")

    monkeypatch.setattr(m2c_context, "lower_bitcasts", boom)
    result = intake_runners.m2c_dialect(_context(BITWISE), {})
    assert result["status"] == FAILED
    assert "ValueError" in result["reason"]


def test_the_action_is_registered():
    """An action the policy cannot resolve is not an action."""
    registry: dict = {}
    names = intake_runners.register(registry)
    assert "eval.intake_runners.m2c_dialect" in names
    assert registry["eval.intake_runners.m2c_dialect"] is intake_runners.m2c_dialect
