"""Verified observation -> next-action examples, from two separately labelled sources.

WHAT THIS IS FOR. The capability being trained is not "emit valid JSON" (the base model already
validates 100% of proposals) and not "follow a fixed order" (that is `ScriptedPolicy`). It is
choosing the next action FROM THE ACTUAL OBSERVATION: the fresh verdict, the residual, the reason a
tool declined, the arguments already spent, and how much budget is left. That capability can be
measured on states that never solve the function, which is why this dataset does not wait for exact
matches and does not clone the steps of one successful script.

TWO LABEL SOURCES, KEPT SEPARATE (spec §3):

  PROCEDURAL (`kind: "procedural"`, `label_source: "fixture"`)
    Small, varied state transitions, many of them executed through the REAL registry and the REAL
    runners so the recorded observation is real tool output rather than a hand-written dict. The
    labels come from rules over observable fields only -- unverified vs compiled, exact vs not,
    a no-effect call against an unchanged source, a missing prerequisite naming itself, a blocked
    tool, a cancellation, an exhausted budget -- so a grader can recheck every one of them without a
    model and without a compiler. PAIRED cases are the point: the same function with one field
    changed must change the right action (unverified -> `compile`; verified -> a transform).

  OUTCOME-BACKED (`kind: "outcome"`, `label_source: "execution"`)
    A state snapshotted from a real episode on a TRAINING-SPLIT function, with a bounded set of
    alternative applicable actions executed from independent copies of that state under the same
    continuation policy and ceilings. What the alternatives actually did -- changed the source,
    improved the certificate, spent compiles, or did nothing -- is recorded in the receipt, and the
    acceptable set is derived from that evidence rather than from an opinion. Where the evidence is
    sparse the label is `unresolved` and the record is a diagnostic, not a training target.

WHAT THIS DELIBERATELY DOES NOT DO: no model calls, no preference pairs, no chain-of-thought, and no
claim that one branch is optimal. Several actions are frequently acceptable and are recorded as such
(spec §2: "Do not require different actions where the evidence permits both").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from eval.tool_agent import Context, Step
from eval.tool_agent_probe import SYSTEM, compile_state, observation, step_view, tried_since_change
from eval.tool_registry import ACTIONS, TERMINAL, action_space

ROOT = Path(__file__).resolve().parents[1]

# Bumped when the record shape changes: a dataset that cannot name its own renderer cannot be
# compared with one produced under a different prompt, and silent drift here invalidates every
# number downstream.
SCHEMA_VERSION = 1
RENDERER_VERSION = "tool-action/1"

def prerequisites_met(context: dict, action: str) -> bool:
    """Does the context carry what this action declared it needs?

    `ACTIONS[name].needs` is the registry's own declaration, and the runners state the same thing at
    call time by returning `not-applicable: the context does not carry X`. This module briefly kept a
    SECOND copy of that table; `test_declared_prerequisites_match_the_runners` now checks the
    registry's declaration against the runners instead, so there is one place to be wrong.
    """
    return all(context.get(key) for key in ACTIONS[action].needs)

# The order a completion is drawn from when several actions are acceptable. Deterministic, and stated
# here rather than implied: the CEILING ON A RECORD is not that this is the only right answer but that
# every action in `acceptable` is defensible from the evidence in the same record.
PREFERENCE_ORDER = ("compile", "diffrepair", "resolve-placeholders", "invert-mutations", "redraft",
                    "regalloc-search", "uopt-trace", "stop")

# A tool that broke is not the same as a compile that failed: a compile error is the ordinary business
# of this system and is the residual to repair, whereas these statuses mean the tooling itself did not
# deliver. Blocked infrastructure is a legal terminal reason (spec §2), so `stop` joins the acceptable
# set -- LAST, because abandoning the episode the moment one route errors would be the wrong lesson.
BLOCKED_STATUSES = ("runner-error", "cancelled", "unwired")


def step_is_blocked(step: dict) -> bool:
    return step["status"] in BLOCKED_STATUSES or (
        step["status"] == "failed" and step["action"] != "compile")


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def system_prompt() -> str:
    """The policy's system turn, built from the live action space.

    ONE PROMPT, shared with inference. The dataset stores `messages` verbatim so a training example
    is the literal policy-visible input, not a re-rendering of it under a template that may since
    have changed.
    """
    return SYSTEM.replace("{space}", json.dumps(action_space(), indent=2))


def family_of(function: str) -> str:
    """The source/template family a function belongs to, for split isolation.

    Siblings share a family (spec §6: "Keep all sibling actions, descendants and counterfactual
    branches together"), so a family may never straddle train and test. The rule is deliberately
    crude and deterministic -- the leading lowercase run, after any underscores, which groups `os*`,
    `func*`, `init*`, `menu*`, `audio*` and treats `__osFoo` as the same family as `osBar` -- because
    a cleverer clustering would be a hypothesis about source genealogy and this is only meant to be a
    conservative fence.
    """
    name = function.lstrip("_")
    prefix = []
    for char in name:
        if char.islower() or char.isdigit():
            prefix.append(char)
        else:
            break
    return "".join(prefix) or name[:3] or function[:3]


def label_for(candidate: str, steps: list, budget_remaining: int, context: dict) -> dict:
    """The mechanically checkable label for one decision state.

    EVERY RULE HERE READS ONLY OBSERVABLE FIELDS, so the label can be recomputed by a grader that has
    the same state and no model. That is what makes procedural learning measurable separately from
    game-solving (spec §2 and §6), and it is why the rules are ordered: an exact certificate ends the
    episode before anything else is considered, and an exhausted budget is an honest stop rather than
    a failure.
    """
    state = compile_state(candidate, steps)
    tried = tried_since_change(steps)
    if state["exact"]:
        return {"acceptable": [{"action": "stop", "params": {"reason": "certified match"}}],
                "forbidden": [], "confidence": "certain", "rule": "certificate passed"}
    if budget_remaining <= 0:
        return {"acceptable": [{"action": "stop", "params": {"reason": "budget exhausted"}}],
                "forbidden": [], "confidence": "certain", "rule": "budget exhausted"}
    if not state["verified"]:
        return {"acceptable": [{"action": "compile", "params": {}}], "forbidden": sorted(tried),
                "confidence": "certain",
                "rule": "no verdict describes the current source; `compile` is what produces one"}
    # VERIFIED: `compile` IS NOT APPLICABLE, and this is the contract's load-bearing consequence. The
    # framework already recompiles after every source change, so a `compile` here returns the
    # identical verdict for the identical source and spends a slot. Leaving it in the acceptable set
    # would erase the one contrast this dataset is built to teach ("unverified -> compile, verified ->
    # something else"), and it is also the mechanical definition the evaluation uses for a redundant
    # compile, so training and grading agree by construction.
    applicable = [name for name in PREFERENCE_ORDER
                  if name in ACTIONS and ACTIONS[name].kind != TERMINAL and name != "compile"
                  and name not in tried and prerequisites_met(context, name)]
    blocked = any(step_is_blocked(step_view(step)) for step in steps)
    if not applicable:
        # Nothing left that has not already returned nothing, or returned nothing usable. Stopping
        # here is justified no-progress, which spec §2 lists as a legal terminal reason -- not a
        # failure and not a success.
        return {"acceptable": [{"action": "stop", "params": {"reason": "justified no-progress"}}],
                "forbidden": sorted(tried), "confidence": "certain",
                "rule": "every applicable action already returned nothing against this source"}
    acceptable = [{"action": name, "params": {}} for name in applicable]
    if blocked:
        acceptable.append({"action": "stop", "params": {"reason": "blocked infrastructure"}})
    return {"acceptable": acceptable, "forbidden": sorted(tried),
            "confidence": "acceptable-set",
            "rule": "verified source, residual present, prerequisites met"}


JSON_SAFE = (str, int, float, bool, type(None))


def record(record_id: str, *, kind: str, split: str, function: str, candidate: str, steps: list,
           context: dict, budget_remaining: int, label: dict, label_source: str,
           evidence: dict | None = None, target_dump: str | None = None) -> dict:
    """One training record: the exact policy-visible input, and the action to be trained.

    THE RECORD IS SELF-CONTAINED AND REPLAYABLE (spec §4). It carries the candidate itself, the
    JSON-safe context, and the full step records -- not just the rendered prompt -- so a closed-loop
    evaluation can rebuild the state exactly, re-run a different policy from it, and check the label
    without trusting the record's own claim about what happened. The prompt is stored too, verbatim,
    because that is what the model actually read and a re-render under a changed template would be a
    different input.
    """
    chosen = label["acceptable"][0]
    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": observation(
                    function, candidate, [step_view(s) for s in steps], budget=budget_remaining,
                    tried=tried_since_change(steps))}]
    return {
        "id": record_id, "kind": kind, "split": split, "function": function,
        "family": family_of(function),
        "messages": messages,
        "completion": json.dumps(chosen, sort_keys=True),
        "action": chosen,
        "acceptable": label["acceptable"],
        "forbidden": label["forbidden"],
        "label_source": label_source,
        "label_confidence": label["confidence"],
        "label_rule": label["rule"],
        "budget_remaining": budget_remaining,
        # --- the replayable state, outside the prompt ---
        "candidate": candidate,
        # `compile_fn` is collapsed to a truthy marker: it is a PREREQUISITE the label rules test for,
        # and a callable cannot be stored. Without the marker every record whose action needs the
        # oracle graded as "prerequisite missing" at evaluation time while the dataset had credited
        # it, which is a label that does not reproduce from its own state.
        "context": {**{k: v for k, v in context.items() if isinstance(v, JSON_SAFE)},
                    "compile_fn": bool(context.get("compile_fn"))},
        "steps": [step_view(s) for s in steps],
        "candidate_sha256": _sha(candidate),
        "has_target_dump": bool(target_dump),
        "evidence": {**(evidence or {}), "steps_rendered": len(steps)},
        "renderer_version": RENDERER_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def _stored_compile_fn():
    """Stands in for a stored oracle: its PRESENCE is what the rules test, and calling it is a bug.

    A record's context stores `compile_fn` as a boolean marker, so a replay that actually tried to
    compile would be compiling against a fiction. If some future replay path calls this, it should say
    so rather than silently returning an empty verdict.
    """
    raise RuntimeError("this record stores only the PRESENCE of a compile function, not one; "
                       "rebuild the real context with eval.tool_agent_run.build_context")


def state_from_record(row: dict) -> tuple[Context, list[Step]]:
    """Rebuild the decision state a record describes, for closed-loop evaluation.

    The SAME function the dataset used is used again here, so an evaluation arm starts from exactly
    the state the training example was built from -- not from a re-derivation that could differ.
    """
    stored = row.get("context") or {}
    context = Context(function=row["function"], candidate=row["candidate"],
                      target_dump=stored.get("target_dump"), diff=stored.get("diff"),
                      source_path=stored.get("source_path"),
                      target_asm_path=stored.get("target_asm_path"),
                      compile_fn=_stored_compile_fn if stored.get("compile_fn") else None,
                      budget_remaining=row.get("budget_remaining"))
    steps = []
    for position, raw in enumerate(row.get("steps") or []):
        steps.append(Step(index=raw.get("index", position), action=raw.get("action"),
                          kind=raw.get("kind") or "transform", params=raw.get("params") or {},
                          status=raw.get("status"), changed=bool(raw.get("changed")),
                          exact=bool(raw.get("exact")), detail=raw.get("detail") or {},
                          candidate_sha256=raw.get("candidate_sha256") or "",
                          pre_action_sha256=raw.get("pre_action_sha256") or "",
                          cancelled=bool(raw.get("cancelled"))))
    return context, steps


def grade(row: dict, action: str, params: dict | None = None) -> dict:
    """Grade one proposed action against the record's mechanically derived state label.

    Regraded from the RECORD'S OWN STATE rather than read from its stored `acceptable` list, so a
    record whose label was wrong when it was written cannot validate itself at evaluation time.
    """
    context, steps = state_from_record(row)
    label = label_for(context.candidate, steps, row.get("budget_remaining"), 
                      {**row.get("context", {}), "compile_fn": True})
    acceptable = [a["action"] for a in label["acceptable"]]
    redundant = action == "compile" and compile_state(context.candidate, steps)["verified"]
    premature_stop = action == "stop" and acceptable != ["stop"]
    return {"acceptable": acceptable, "forbidden": label["forbidden"], "rule": label["rule"],
            "is_acceptable": action in acceptable, "redundant_compile": redundant,
            "repeat_after_no_effect": action in label["forbidden"],
            "premature_stop": premature_stop}


def _step(candidate: str, action: str, result: dict, index: int = 0) -> Step:
    """A step built from a fixture result, hashed against the source it describes.

    Built through the REAL `Step` and the REAL renderer, so a fixture cannot invent an observation
    shape the live loop would never produce.
    """
    detail = {k: v for k, v in result.items() if k not in ("source", "candidates", "detail")}
    if isinstance(result.get("detail"), dict):
        detail = {**result["detail"], **detail}
    return Step(index=index, action=action, kind=str(result.get("kind") or "transform"),
                params=result.get("params") or {}, status=str(result.get("status") or "unknown"),
                changed=bool(result.get("changed")), exact=bool(result.get("exact")),
                detail=detail, pre_action_sha256=_sha(candidate),
                candidate_sha256=_sha(result.get("source") or candidate),
                cancelled=bool(result.get("cancelled")))


def _verdict(diff: str = "", stderr: str = "", score: float = 0.0,
             status: str = "no-match") -> dict:
    return {"compiled": True, "exact": False, "certificate_status": status, "score": score,
            "diff": diff, "stderr": stderr}


# --- procedural exercises ---------------------------------------------------------

CANDIDATE = ("s32 osGetThreadPri(OSThread *thread) {\n"
             "    return thread->priority;\n"
             "}\n")
RESIDUAL = "-lbu v1,0x24(a0)\n+lbu v1,0(a0)"

# HELD-OUT SURFACES. The procedural rules are identical across surfaces; only the function name, the
# source text, the residual text and the tool arguments change. That is deliberate (spec §6): a policy
# that has learned "the current source carries a verdict, so compile is redundant" transfers, while one
# that has memorised `osGetThreadPri` does not, and the difference between the two is measurable only
# if the test surface was never trained on. The rule set is not weakened for a held-out surface -- an
# evaluation that changes the question cannot compare answers.
SURFACES: tuple[dict, ...] = (
    {"function": "osGetThreadPri", "candidate": CANDIDATE, "residual": RESIDUAL,
     "regalloc": {"budget": 64, "beam": 8}},
    {"function": "funcUnnamed4C",
     "candidate": ("u32 funcUnnamed4C(u8 *p) {\n"
                   "    return *(u32 *)(p + 4);\n"
                   "}\n"),
     "residual": "-lw v0,0x10(a0)\n+lw v0,0x4(a0)",
     "regalloc": {"budget": 32, "beam": 4}},
    {"function": "initAudioHeap",
     "candidate": ("void initAudioHeap(AudioState *state) {\n"
                   "    state->count = 0;\n"
                   "}\n"),
     "residual": "-sw r0,0x8(s0)\n+sw r0,0x0(s0)",
     "regalloc": {"budget": 16, "beam": 2}},
)
SURFACE_FOR_SPLIT = {"train": 0, "dev": 1, "test": 2}


def procedural_exercises(surface: int = 0) -> list[dict]:
    """The states, as (name, candidate, steps, context, budget) before labelling.

    PAIRS ARE EXPLICIT. `verified_state` differs from `fresh_state` in exactly one observable field
    -- whether a verdict describes the current source -- and the right action differs with it.
    `retry_after_change` differs from `no_effect_unchanged` only by an intervening source change, and
    the action that was forbidden becomes legal again. A dataset that cannot show a policy changing
    its mind for one reason is not evidence that the policy reads anything.
    """
    from eval import tool_runners as trun

    face = SURFACES[surface % len(SURFACES)]
    candidate, residual = face["candidate"], face["residual"]
    verified = _step(candidate, "compile", _verdict(diff=residual, stderr="", score=38.0))
    no_effect = _step(candidate, "diffrepair",
                      {"status": "no-change", "changed": False, "exact": False,
                       "detail": {"reason": "no offset appears in both the diff and a declared field"}})
    moved = _step(candidate, "redraft",
                  {"status": "ok", "changed": True, "exact": False,
                   "source": candidate + "/*r*/\n"})
    moved_verdict = _step(candidate + "/*r*/\n", "compile",
                          _verdict(diff="-lw v0,0x1c(a1)\n+lw v0,0(a1)", score=52.0))
    # THE MISSING-PREREQUISITE STATE IS EXECUTED, not written by hand: the real runner is called with a
    # context that lacks `target_dump`, so the observation's reason text is the tool's own.
    missing = trun.regalloc_search({"candidate": candidate}, dict(face["regalloc"]))
    blocked = {"status": "failed", "changed": False, "exact": False,
               "reason": "m2c exit 1: target.s could not be parsed"}
    cancelled = {"status": "cancelled", "changed": False, "exact": False, "cancelled": True,
                 "reason": "the search was cancelled at the deadline"}
    broken = {"status": "runner-error", "changed": False, "exact": False,
              "reason": "the resolve-placeholders runner raised AttributeError: 'NoneType' object "
                        "has no attribute 'rewrite'"}

    base_context = {"candidate": candidate, "compile_fn": object(), "diff": residual,
                    "source_path": "base.c", "target_dump": None, "target_asm_path": "target.s"}
    rich_context = {**base_context, "target_dump": "lbu v1,0x24(a0)\n"}

    return [
        ("fresh_state", candidate, [], {**base_context, "diff": None}, 6),
        ("verified_state", candidate, [verified], base_context, 5),
        ("no_effect_unchanged", candidate, [verified, no_effect], base_context, 4),
        ("retry_after_change", candidate + "/*r*/\n", [verified, no_effect, moved, moved_verdict],
         base_context, 2),
        ("exact_state", candidate, [verified, _step(candidate, "compile",
                                                    {**_verdict(diff="", score=100.0,
                                                                status="exact"), "exact": True})],
         base_context, 3),
        ("budget_exhausted", candidate, [verified], base_context, 0),
        ("missing_prerequisite", candidate, [verified, _step(candidate, "regalloc-search",
                                                             {**missing,
                                                              "params": dict(face["regalloc"])})],
         base_context, 3),
        ("prerequisite_satisfied", candidate, [verified], rich_context, 3),
        ("infrastructure_blocked", candidate, [verified, _step(candidate, "redraft", blocked)],
         base_context, 3),
        ("cancelled_tool", candidate, [verified, _step(candidate, "regalloc-search",
                                                       {**cancelled,
                                                        "params": dict(face["regalloc"])})],
         rich_context, 3),
        ("malformed_runner_result", candidate, [verified, _step(candidate, "resolve-placeholders",
                                                                broken)], base_context, 3),
        ("no_progress_left", candidate,
         [verified] + [_step(candidate, name, {"status": "no-change", "changed": False,
                                               "exact": False,
                                               "detail": {"reason": "nothing moved"}})
                       for name in ("diffrepair", "resolve-placeholders", "invert-mutations",
                                    "redraft", "uopt-trace")],
         {**base_context, "diff": residual}, 2),
    ]


def procedural_records(split: str = "train") -> list[dict]:
    """The procedural exercises for one split, on that split's own held-out surface."""
    surface = SURFACE_FOR_SPLIT.get(split, 0)
    face = SURFACES[surface % len(SURFACES)]
    rows = []
    for index, (name, candidate, steps, context, budget) in enumerate(procedural_exercises(surface)):
        label = label_for(candidate, steps, budget, context)
        rows.append(record(f"proc-{split}-{index:04d}-{name}", kind="procedural", split=split,
                           function=face["function"], candidate=candidate, steps=steps,
                           context=context, budget_remaining=budget, label=label,
                           label_source="fixture",
                           evidence={"exercise": name, "rule": label["rule"],
                                     "surface": surface}))
    return rows


# --- outcome-backed decisions -----------------------------------------------------

class SnapshotPolicy:
    """Runs a real episode and stops at each decision to record the state the decision was made from.

    The wrapper exists so the dataset is built from the STATES THE LIVE LOOP PRODUCES -- same context,
    same budget accounting, same step records -- rather than from a re-implementation that could
    drift. Nothing here calls a model: branches are executed with the real runners and the real
    compiler.
    """

    def __init__(self, inner, budget: int, limit: int):
        self.inner, self.budget, self.limit = inner, budget, limit
        self.name = f"snapshot({inner.name})"
        self.snapshots: list[dict] = []
        # WHAT THE POLICY CHOSE, KEYS TO THE SAME STATE. The correction round (spec §5) needs the
        # states the LEARNED policy actually visits and what it did there; without the paired choice a
        # snapshot can only be labelled as though a script had visited it.
        self.choices: list[dict] = []

    def choose(self, context: Context, history):
        if len(self.snapshots) < self.limit:
            self.snapshots.append({
                "candidate": context.candidate,
                "steps": [step_view(step) for step in history],
                # Read from the loop rather than recomputed from `len(history)`: the loop now also
                # records the controller's pre-episode compile as step -1, so counting steps would
                # under-report the budget by one and every snapshot's label would be made against a
                # budget the policy never had.
                "budget_remaining": (context.budget_remaining if context.budget_remaining is not None
                                     else self.budget),
                "context_keys": {**{key: value for key, value in context.as_dict().items()
                                    if key != "compile_fn"},
                                 # A truthy marker rather than the callable: JSON-safe, and it is a
                                 # PREREQUISITE in its own right (a context with no oracle cannot
                                 # compile), so the label rule must be able to see it.
                                 "compile_fn": bool(context.compile_fn)},
                "has_compile_fn": context.compile_fn is not None,
                "target_dump": context.target_dump,
                "diff": context.diff,
            })
        chosen = self.inner.choose(context, history)
        if len(self.choices) < self.limit:
            self.choices.append({"action": chosen[0], "params": chosen[1]})
        return chosen


def branch_outcomes(snapshot: dict, names: list[str], compile_fn, runners: dict,
                    *, max_seconds: float = 120.0) -> list[dict]:
    """Execute alternative actions from INDEPENDENT COPIES of one state.

    Independent copies matter: a runner may mutate the context dict it is handed, and a second branch
    that inherited the first branch's context would be measuring the first branch. Each branch gets a
    fresh Context with the same candidate and diff, so the only variable is the action.
    """
    import time
    outcomes = []
    for name in names:
        if name not in ACTIONS or ACTIONS[name].kind == TERMINAL:
            continue
        context = Context(function=snapshot.get("function") or "unknown",
                          candidate=snapshot["candidate"],
                          target_dump=snapshot.get("target_dump"),
                          diff=snapshot.get("diff"),
                          source_path=snapshot.get("source_path"),
                          target_asm_path=snapshot.get("target_asm_path"),
                          compile_fn=compile_fn if snapshot.get("has_compile_fn") else None)
        runner = runners.get(ACTIONS[name].runner)
        started = time.time()
        try:
            result = runner(context.__dict__, {}) if runner else {"status": "unwired"}
        except Exception as exc:                                # noqa: BLE001
            result = {"status": "runner-error", "changed": False, "exact": False,
                      "reason": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            result = {"status": "runner-error", "changed": False, "exact": False,
                      "reason": f"returned {type(result).__name__}"}
        verdict = None
        changed = bool(result.get("changed")) and isinstance(result.get("source"), str)
        if changed and context.compile_fn and (time.time() - started) < max_seconds:
            verdict = compile_fn(result["source"])
        outcomes.append({
            "action": name, "status": str(result.get("status")), "changed": changed,
            # `detail` is a DICT for some runners and a string for others; slicing the dict raised
            # KeyError: slice(None, 300, None) and killed a collection run eleven functions in.
            "reason": str(result.get("reason") or result.get("detail") or "")[:300],
            "exact": bool(result.get("exact") or (verdict or {}).get("exact")),
            "certificate_status": (verdict or {}).get("certificate_status"),
            "score": (verdict or {}).get("score"), "seconds": round(time.time() - started, 2),
        })
    return outcomes


def outcome_records(specs: list[dict], *, split: str = "train") -> list[dict]:
    """Turn snapshot + branch evidence into records, honest about what the evidence supports."""
    rows = []
    for index, spec in enumerate(specs):
        snapshot = spec["snapshot"]
        outcomes = spec["outcomes"]
        candidate, steps, budget = snapshot["candidate"], snapshot["steps"], \
            snapshot["budget_remaining"]
        label = label_for(candidate, steps, budget, snapshot["context_keys"])
        improved = [o for o in outcomes if o["changed"] or o["exact"]]
        if improved and label["confidence"] != "certain":
            # Execution-backed: the actions that actually moved this state, in the same order the
            # preference rule would use. Actions that did nothing are removed from the acceptable set
            # but are NOT recorded as forbidden -- one episode at one budget is not proof that an
            # action can never help, which would be exactly the blanket ban spec §2 forbids.
            acceptable = [{"action": o["action"], "params": {}} for o in outcomes if o["changed"]]
            label = {"acceptable": acceptable, "forbidden": label["forbidden"],
                     "confidence": "acceptable-set" if len(acceptable) > 1 else "certain",
                     "rule": "executed from this state: the action(s) moved the source"}
        elif not improved and label["confidence"] != "certain":
            label = {**label, "confidence": "unresolved",
                     "rule": label["rule"] + "; no executed alternative moved the state"}
        rows.append(record(f"out-{index:04d}-{spec['function']}", kind="outcome", split=split,
                           function=spec["function"], candidate=candidate, steps=steps,
                           context=snapshot["context_keys"], budget_remaining=budget, label=label,
                           label_source="execution",
                           evidence={"outcomes": outcomes, "rule": label["rule"],
                                     "siblings_together": True},
                           target_dump=snapshot.get("target_dump")))
    return rows


def correction_records(specs: list[dict], *, split: str = "train") -> list[dict]:
    """States the LEARNED policy actually visited and got wrong, with the verified corrective action.

    Spec §5 permits exactly one bounded round of this, and the reason is that a policy trained on a
    script's states is only corrected where the SCRIPT went, not where the policy goes. The label is
    the SAME mechanical rule used everywhere else (`label_for`), and the corrective action is executed
    from an independent copy of the state before it is recorded -- a correction that has not been
    verified is a guess about a state, which is what this dataset exists to avoid.

    Held-out states are never used: `collect()` only visits functions from the requested split, and the
    only split this is run for is TRAIN.
    """
    rows = []
    for index, spec in enumerate(specs):
        snapshot, chosen = spec["snapshot"], spec["chosen"]
        label = label_for(snapshot["candidate"], snapshot["steps"], snapshot["budget_remaining"],
                          snapshot["context_keys"])
        acceptable = [item["action"] for item in label["acceptable"]]
        if chosen["action"] in acceptable:
            continue
        rows.append(record(f"fix-{index:04d}-{spec['function']}-{chosen['action']}", kind="correction",
                           split=split, function=spec["function"], candidate=snapshot["candidate"],
                           steps=[Step(index=step.get("index", position),
                                       action=step["action"], kind=step.get("kind") or "transform",
                                       params=step.get("params") or {}, status=step["status"],
                                       changed=step["changed"], exact=step["exact"],
                                       detail=step.get("detail") or {},
                                       candidate_sha256=step.get("candidate_sha256") or "",
                                       pre_action_sha256=step.get("pre_action_sha256") or "",
                                       cancelled=step.get("cancelled", False))
                                  for position, step in enumerate(snapshot["steps"])],
                           context=snapshot["context_keys"], budget_remaining=snapshot["budget_remaining"],
                           label=label, label_source="correction",
                           evidence={"chosen_by_policy": chosen,
                                     "chosen_was_not_acceptable": True,
                                     "rule": label["rule"], "outcomes": spec.get("outcomes") or []},
                           target_dump=snapshot.get("target_dump")))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row['kind']}/{row['label_source']}/{row['label_confidence']}/{row['split']}"
        counts[key] = counts.get(key, 0) + 1
    return {"path": str(path), "records": len(rows), "sha256": _sha(path.read_text("utf-8")),
            "counts": counts}


def freeze_splits(names: list[str], *, dev: set[str] | None = None, test_percent: int = 25,
                  seed: str = "tool-action-20260921") -> dict:
    """Assign whole FUNCTION FAMILIES to train/dev/test, before any derived state exists.

    Split isolation is a property of the fence, not of the labels (spec §6): every sibling action,
    descendant and counterfactual branch of a function must land in one bucket, so the unit of
    assignment is the family and a function may never be split across buckets. Functions already used
    by an earlier tool-agent panel are pinned to DEV rather than allowed into training -- reusing a
    function we have already reported numbers on would make the held-out claim fiction.
    """
    families: dict[str, list[str]] = {}
    for name in names:
        families.setdefault(family_of(name), []).append(name)
    pinned = {family_of(name) for name in (dev or set())}
    out: dict[str, list[str]] = {"train": [], "dev": [], "test": []}
    for family in sorted(families):
        if family in pinned:
            bucket = "dev"
        else:
            digest = hashlib.sha256(f"{seed}:{family}".encode("utf-8")).hexdigest()
            bucket = "test" if int(digest[:8], 16) % 100 < test_percent else "train"
        out[bucket].extend(sorted(families[family]))
    return {**{key: sorted(value) for key, value in out.items()},
            "families": {key: sorted({family_of(n) for n in out[key]}) for key in out},
            "seed": seed, "test_percent": test_percent, "pinned_dev_families": sorted(pinned)}


def dev_functions_from(paths: list[Path]) -> set[str]:
    """Function names an earlier panel already reported on, read straight from its result files.

    Read rather than remembered: the pilot's function list lives in its receipts, and a hand-copied
    list would drift from what was actually run.
    """
    names: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text("utf-8"))
        for row in payload.get("rows", []):
            if row.get("function"):
                names.add(row["function"])
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("procedural", "splits"), default="procedural",
                    help="outcome-backed collection is driven by eval/tool_action_collect.py, which "
                         "needs the real compiler and so runs inside WSL")
    ap.add_argument("--split", default="train")
    ap.add_argument("--functions", type=int, default=0,
                    help="with --mode splits: how many KB candidates to consider")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--dev-json", type=Path, action="append", default=[],
                    help="result files whose functions are pinned to DEV")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/procedural.jsonl")
    args = ap.parse_args(argv)

    if args.mode == "splits":
        import sqlite3
        conn = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
        names = [row[0] for row in conn.execute(
            "select f.name from attempts a join functions f on f.addr = a.func_addr "
            "group by f.name having sum(coalesce(a.exact,0)) = 0 order by max(f.size) asc").fetchall()]
        conn.close()
        from eval import seal
        sealed = seal.sealed_in_sets()
        names = [n for n in names if n not in sealed][:args.functions]   # filter BEFORE the limit
        default_dev = [ROOT / "eval/results/tool-agent-20260920/head-to-head.json"]
        pinned = dev_functions_from(args.dev_json or default_dev)
        payload = {**freeze_splits(names, dev=pinned),
                   "dev_source_files": [str(p) for p in (args.dev_json or default_dev)]}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: len(payload[key]) for key in ("train", "dev", "test")}, indent=2))
        print(f"families: {json.dumps(payload['families'])}")
        return 0

    rows = procedural_records(args.split)
    summary = write_jsonl(args.out, rows)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
