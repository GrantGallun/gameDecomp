"""The intervention path: propose -> confirm -> retrieve -> measure.

These tests exist because the whole path was DEAD and nothing said so. `confirm()` was named in a
docstring and implemented nowhere, so every note stayed `proposed`, `candidate-frozen` could never
freeze a child, and the paired evaluation filtered on `confirmed` and always ran with zero notes
enabled. The pilot then reported a 0-vs-0 delta and "the intervention was never applied" as though that
were restraint. A test that asserts the loop can reach a DIFFERENT arm is what makes that unrepeatable:

  * a proposal is not enabled (proposed != confirmed),
  * the declared applicability test enables one, and refuses a note whose scope matches nothing,
  * retrieval is per state, not a global prompt change,
  * and an enabled note actually reaches the policy that is being measured.
"""
from __future__ import annotations

from eval import rsi_interventions as ri
from eval.tool_agent import Context


CLUSTER = {"cluster_id": "b7bd868662d0", "error_class": "cfe: Error: Syntax Error",
           "residual_kind": "no-diff", "size_bucket": "large", "functions": ["f_a", "f_b"]}
FINDING = {"id": "exp-001", "experiment_id": "exp-001", "title": "Bitwise AND versus Bitwise OR",
           "rationale": "measured on three parameter values", "metric": "andi_ops",
           "relation": "less", "measurements_sha256": "abc"}


def test_a_proposal_is_not_enabled():
    """The rule the module states: a hypothesis does not get to change behaviour."""
    note = ri.intervention_from_finding(FINDING, kind="memory", text="measured: and is cheaper",
                                        applicability={"error_class": CLUSTER["error_class"],
                                                       "residual_kind": CLUSTER["residual_kind"]})
    assert isinstance(note, ri.Note) and note.status == "proposed"
    assert ri.notes_for([note], {"error_class": CLUSTER["error_class"],
                                 "residual_kind": CLUSTER["residual_kind"]}) == [], \
        "a proposed note must never be retrieved"


def test_the_declared_test_enables_a_note_and_records_what_it_establishes():
    note = ri.intervention_from_finding(FINDING, kind="memory", text="measured: and is cheaper",
                                        applicability={"error_class": CLUSTER["error_class"],
                                                       "residual_kind": CLUSTER["residual_kind"]})
    outcome = ri.confirm_applicability(note, CLUSTER)
    assert outcome["confirmed"] is True and note.status == "confirmed"
    confirmation = note.provenance["confirmation"]
    assert confirmation["test"] == "applicability_matches_motivating_cluster"
    assert "not effect" in confirmation["establishes"], \
        "the receipt must not let an applicability check read as an effectiveness claim"
    assert ri.notes_for([note], {"error_class": CLUSTER["error_class"],
                                 "residual_kind": CLUSTER["residual_kind"]}) == [note]


def test_a_note_scoped_to_something_else_is_refused_and_stays_proposed():
    """Enabling it would add a prompt difference that can never fire, and the receipt would then show
    an 'enabled' intervention that no state ever selects."""
    note = ri.intervention_from_finding(FINDING, kind="memory", text="unrelated",
                                        applicability={"error_class": "compiled",
                                                       "residual_kind": "structural"})
    outcome = ri.confirm_applicability(note, CLUSTER)
    assert outcome["confirmed"] is False and "does not match" in outcome["reason"]
    assert note.status == "proposed"
    assert ri.notes_for([note], {"error_class": "compiled", "residual_kind": "structural"}) == []


def test_an_empty_applicability_block_matches_nothing():
    note = ri.intervention_from_finding(FINDING, kind="memory", text="global advice")
    assert ri.confirm_applicability(note, CLUSTER)["confirmed"] is False
    assert ri.matches({}, {"error_class": "anything"}) is False, \
        "a note that applies everywhere is a global behaviour change, not a finding"


class _Recorder:
    """Stands in for the model policy: records what `notes` it was handed at the moment of choosing."""

    name = "recorder"

    def __init__(self):
        self.notes = []
        self.seen = []

    def choose(self, context, history):
        self.seen.append(list(self.notes))
        return "stop", {"reason": "recording"}


def test_retrieval_is_per_state_not_a_global_prompt_change():
    matching = ri.intervention_from_finding(FINDING, kind="memory", text="matches",
                                            applicability={"error_class": "no-error-recorded",
                                                           "residual_kind": "no-diff"})
    matching.status = "confirmed"
    unrelated = ri.intervention_from_finding(FINDING, kind="memory", text="never matches here",
                                             applicability={"error_class": "cfe: Error: Syntax Error"})
    unrelated.status = "confirmed"

    recorder = _Recorder()
    policy = ri.NoteRetrievalPolicy(recorder, [matching, unrelated])
    policy.choose(Context(function="f", candidate="C"), [])          # no history, no diff

    assert [note["text"] for note in recorder.seen[0]] == ["matches"], \
        "only the note whose applicability matches THIS state may reach the policy"
    assert policy.history[-1]["retrieved"] == [matching.id]


def test_an_empty_note_set_retrieves_nothing_so_a_null_cannot_be_faked():
    """The baseline arm must be the intervention arm with nothing to retrieve -- not a different
    pipeline. If this ever returns a note, the 0-vs-0 comparison stops meaning what it claims."""
    recorder = _Recorder()
    policy = ri.NoteRetrievalPolicy(recorder, [])
    policy.choose(Context(function="f", candidate="C"), [])
    assert recorder.seen == [[]] and policy.history[-1]["retrieved"] == []


def test_compositions_are_bounded_to_registered_actions():
    with __import__("pytest").raises(ValueError):
        ri.assert_bounded(ri.Composition(id="c1", when={"residual_kind": "no-diff"},
                                         prefer=["not-a-real-action"], reason="invented"))
    with __import__("pytest").raises(ValueError):
        ri.assert_bounded(ri.Composition(id="c2", when={"residual_kind": "no-diff"},
                                         prefer=["stop", "diffrepair"], reason="early stop"))
    ri.assert_bounded(ri.Composition(id="c3", when={"residual_kind": "no-diff"},
                                     prefer=["diffrepair", "stop"], reason="reorder then stop"))
