"""The bounded branching search: it must branch, keep the incumbent, and stop at its allowance.

THE THREE PROPERTIES THAT MAKE IT WORTH RUNNING, each of which a greedy loop lacks:

  * a branch that LOWERS the score is still explored, because the next step may be the one that needs it;
  * the best INCUMBENT survives even when no branch beats it, so the comparison against the fixed policy
    is against the policy's own candidate rather than against the draft;
  * the compile allowance is a hard stop that is REPORTED -- "not found within N compiles" is never
    allowed to read as "no sequence exists".

No compiler, no repo and no toolchain is used: the search takes a context whose `compile_fn` is a stub,
which is exactly the interface the real one has.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import bounded_search as search                                   # noqa: E402


class _Context:
    """The fields the runners and the search read, and a compile stub keyed by source text."""

    def __init__(self, verdicts: dict, candidate: str = "DRAFT"):
        self.candidate = candidate
        self.diff = None
        self.repo = "."
        self.target = "build/src/x.o"
        self.workspace = "."
        self.compiles: list[str] = []
        self.verdicts = verdicts

    def compile_fn(self, source: str) -> dict:
        self.compiles.append(source)
        return dict(self.verdicts.get(source, {"compiled": True, "exact": False, "score": 1.0}))


def _runner(mapping):
    """A runner whose behaviour is a lookup: source -> new source or None for "declined"."""
    def run(namespace, _params):
        new = mapping.get(namespace["candidate"])
        if new is None:
            return {"status": "ok", "changed": False, "exact": False}
        return {"status": "ok", "changed": True, "exact": False, "source": new}
    return run


def test_a_branch_that_lowers_the_score_is_explored_and_can_reach_exact():
    """The motivating shape. Step one loses score, step two reaches exact; a greedy loop stops at step
    one and reports nothing."""
    runners = {search.CATALOG[0]: _runner({"DRAFT": "WORSE"}),
               search.CATALOG[1]: _runner({"WORSE": "EXACT"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "WORSE": {"compiled": True, "exact": False, "score": 10.0},
                "EXACT": {"compiled": True, "exact": True, "score": 100.0},
                "POLICY": {"compiled": True, "exact": False, "score": 90.0}}
    context = _Context(verdicts)
    found = search.Search(context=context, runners=runners, depth=2, beam=1, compiles=10).run(
        "DRAFT", verdicts["DRAFT"],
        incumbent={"source": "POLICY", "verdict": verdicts["POLICY"], "path": [],
                   "sha256": search.sha256_text("POLICY")})
    assert found["exact"] is True
    assert found["solution_path"] == ["resolve_placeholders", "negative_offset"]
    assert found["depth_found"] == 2
    outcome = search.classify(found, single_action_solution=False, policy_sha256=search.sha256_text("POLICY"))
    assert outcome["outcome"] == "composition-learning-opportunity"


def test_a_single_action_that_reaches_exact_is_a_policy_opportunity_not_a_composition():
    runners = {search.CATALOG[0]: _runner({"DRAFT": "EXACT"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "EXACT": {"compiled": True, "exact": True, "score": 100.0},
                "POLICY": {"compiled": True, "exact": False, "score": 50.0}}
    found = search.Search(context=_Context(verdicts), runners=runners, depth=2, beam=2,
                          compiles=8).run("DRAFT", verdicts["DRAFT"])
    outcome = search.classify(found, single_action_solution=True, policy_sha256="other")
    assert outcome["outcome"] == "policy-learning-opportunity"
    assert "single action" in outcome["why"]


def test_the_incumbent_survives_a_search_that_finds_nothing_better():
    """The comparison is against the POLICY's candidate, not against the draft."""
    runners = {search.CATALOG[0]: _runner({"DRAFT": "WORSE"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "WORSE": {"compiled": True, "exact": False, "score": 1.0},
                "POLICY": {"compiled": True, "exact": False, "score": 91.0}}
    found = search.Search(context=_Context(verdicts), runners=runners, depth=2, beam=2,
                          compiles=8).run("DRAFT", verdicts["DRAFT"],
                                          incumbent={"source": "POLICY",
                                                     "verdict": verdicts["POLICY"], "path": [],
                                                     "sha256": search.sha256_text("POLICY")})
    assert found["exact"] is False
    assert found["final_sha256"] == search.sha256_text("POLICY")
    outcome = search.classify(found, single_action_solution=False,
                             policy_sha256=search.sha256_text("POLICY"))
    assert outcome["outcome"] == "unresolved-within-budget"
    assert outcome["sub_outcome"] == "no-improvement"
    assert "already optimal" in outcome["note"]


def test_a_branch_that_beats_the_policy_without_reaching_exact_is_reported_as_such():
    runners = {search.CATALOG[0]: _runner({"DRAFT": "BETTER"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "BETTER": {"compiled": True, "exact": False, "score": 95.0},
                "POLICY": {"compiled": True, "exact": False, "score": 91.0}}
    found = search.Search(context=_Context(verdicts), runners=runners, depth=1, beam=1,
                          compiles=4).run("DRAFT", verdicts["DRAFT"],
                                          incumbent={"source": "POLICY",
                                                     "verdict": verdicts["POLICY"], "path": [],
                                                     "sha256": search.sha256_text("POLICY")})
    outcome = search.classify(found, single_action_solution=False,
                             policy_sha256=search.sha256_text("POLICY"))
    assert outcome["sub_outcome"] == "improved-over-the-fixed-order"
    assert found["final_score"] == 95.0


def test_the_compile_allowance_is_a_hard_stop_and_is_reported():
    """Every action leads somewhere, so the search would run forever without the cap. The cap must stop
    it AND say that it stopped it, so the null is 'not found within N', never 'impossible'.

    One compile out of the allowance is RESERVED to certify whatever the last action produces: an internal
    search that consumes the whole remainder and returns a candidate nobody can score has spent the budget
    and answered nothing. So a five-compile allowance executes four, and says it was starved rather than
    claiming the catalog was exhausted."""
    runners = {label: _runner({"DRAFT": f"step-{index}"})
               for index, label in enumerate(search.CATALOG)}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0}}
    context = _Context(verdicts)
    found = search.Search(context=context, runners=runners, depth=3, beam=4, compiles=5).run(
        "DRAFT", verdicts["DRAFT"])
    assert found["allowance_exhausted"] is True
    assert found["compiles_used"] == 4
    assert len(context.compiles) == 4, "the allowance counts real compiles, not attempted actions"
    outcome = search.classify(found, single_action_solution=False)
    assert outcome["outcome"] == "unresolved-within-budget"
    assert "not proof that no sequence exists" in outcome["why"]


def test_an_actions_internal_compiles_are_charged_to_the_allowance(monkeypatch):
    """`regalloc-search` runs a beam whose default budget is 64 compiles. Counting one outer compile per
    action would under-report the work by up to 64x and let a search spend far past its declared
    allowance -- the hidden internal compile budget the brief forbids."""
    def hungry(namespace, params):
        # What the real runner reports back after doing its own compiling.
        return {"status": "ok", "changed": True, "source": "CHILD", "compiles": int(params.get("budget", 64))}

    class _Action:
        params = (type("P", (), {"kind": "int", "name": "budget", "minimum": 1, "maximum": 2048,
                                 "default": 64})(),)
        runner = "stub.hungry"

    monkeypatch.setitem(__import__("eval.tool_registry", fromlist=["x"]).ACTIONS, "stub.hungry", _Action)
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "CHILD": {"compiled": True, "exact": False, "score": 10.0}}
    context = _Context(verdicts)
    found = search.Search(context=context, runners={"stub.hungry": hungry}, depth=1, beam=1,
                          compiles=20, catalog=("stub.hungry",)).run("DRAFT", verdicts["DRAFT"])
    # The beam was clamped to what the search could afford (19), charged, and one compile certified it.
    assert found["internal_compiles"] == 19, found
    assert found["compiles_used"] == 20 and found["compiles_used_certifying"] == 1
    # The depth bound ended this search, and the allowance is gone with it: two different facts, and the
    # receipt reports both rather than making them look alike.
    assert found["allowance_spent"] is True and found["allowance_exhausted"] is False
    assert "depth bound" in search.classify(found, single_action_solution=False)["why"]


def test_a_crashing_action_is_recorded_and_does_not_stop_the_search():
    def boom(_namespace, _params):
        raise RuntimeError("runner exploded")

    runners = {search.CATALOG[0]: boom, search.CATALOG[1]: _runner({"DRAFT": "GOOD"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "GOOD": {"compiled": True, "exact": False, "score": 40.0}}
    search_run = search.Search(context=_Context(verdicts), runners=runners, depth=1, beam=1, compiles=4)
    found = search_run.run("DRAFT", verdicts["DRAFT"])
    assert found["final_score"] == 40.0
    crashes = [node for node in search_run.nodes if node.get("status") == "crashed"]
    assert crashes and "runner exploded" in crashes[0]["error"]


def test_a_do_while_action_waits_for_the_compiler_to_name_the_token():
    """The campaign's gate, kept: the action that owns the `do` token must not fire on a draft the
    compiler never complained about."""
    assert search.applicable("eval.intake_runners.rewrite_do_while", "") is False
    assert search.applicable("eval.intake_runners.rewrite_do_while",
                            "cfe: Error: contains a do-while loop") is True
    assert search.applicable("eval.intake_runners.resolve_placeholders", "") is True


def test_a_move_that_breaks_a_compiling_candidate_is_counted_separately():
    """THE SAFETY MEASUREMENT. `opaque_variant` used to turn 3 of 17 compiling development candidates into
    uncompilable ones; the score never noticed, because every caller keeps the better incumbent. Counting
    'nodes that do not compile' cannot see it either -- most nodes descend from a draft that never
    compiled. What is counted here is exactly the damaging move."""
    runners = {search.CATALOG[0]: _runner({"GOOD": "BROKEN"})}
    verdicts = {"GOOD": {"compiled": True, "exact": False, "score": 80.0},
                "BROKEN": {"compiled": False, "exact": False, "score": 0.0,
                           "stderr": "cfe: Error: redeclaration of 'X'"}}
    context = _Context(verdicts)
    found = search.Search(context=context, runners=runners, depth=1, beam=1, compiles=4).run(
        "GOOD", verdicts["GOOD"])
    assert found["destructive_moves"] == 1
    detail = found["destructive_detail"][0]
    assert detail["action"] == "resolve_placeholders" and detail["parent_score"] == 80.0
    assert "redeclaration" in detail["child_stderr_head"]
    # ...and the incumbent is still the compiling candidate, which is why only this counter can see it.
    assert found["final_score"] == 80.0 and found["exact"] is False


def test_a_move_from_an_uncompilable_draft_is_not_counted_as_destructive():
    """The draft does not compile, so a child that does not compile either has broken nothing."""
    runners = {search.CATALOG[0]: _runner({"DRAFT": "STILL BROKEN"})}
    verdicts = {"DRAFT": {"compiled": False, "exact": False, "score": 0.0},
                "STILL BROKEN": {"compiled": False, "exact": False, "score": 0.0}}
    found = search.Search(context=_Context(verdicts), runners=runners, depth=1, beam=1,
                          compiles=4).run("DRAFT", verdicts["DRAFT"])
    assert found["destructive_moves"] == 0


def test_the_starting_draft_must_reproduce_the_frozen_frame_hash(tmp_path, monkeypatch):
    """THE PROVENANCE GUARD. If the context's draft is not the bytes the frame measured, the search is
    answering a different question and must say so instead of reporting a rate."""
    from eval.tool_agent import Context

    frame = {"schema_version": 5, "entries": [
        {"function": "f", "sha256": search.sha256_text("POLICY"),
         "baseline_draft_sha256": search.sha256_text("THE FROZEN DRAFT")}]}
    dev = tmp_path / "dev-set.json"
    dev.write_text(json.dumps(frame), encoding="utf-8")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "f.c").write_text("POLICY", encoding="utf-8")
    real = Context(function="f", candidate="A DIFFERENT DRAFT")
    real.compile_fn = lambda source: {"compiled": True, "exact": False, "score": 1.0}
    monkeypatch.setattr("eval.tool_agent_run.build_context", lambda *a, **k: (real, None))
    out = tmp_path / "search.json"
    search.main(["--dev-set", str(dev), "--out", str(out), "--compiles", "2"])
    payload = json.loads(out.read_text(encoding="utf-8"))
    record = payload["results"][0]
    assert record["classification"]["outcome"] == "infrastructure"
    assert "does not hash to the frozen frame" in record["infrastructure_error"]
