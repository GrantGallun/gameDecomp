"""The agent loop, and the two policies that need no model.

THE LOOP HAS NO DOMAIN KNOWLEDGE. It observes, asks a policy for one action, dispatches it through
the registry, and repeats. There is no "try X then Y" anywhere in it -- that ordering is a POLICY, and
keeping it out of the loop is what stops this from becoming the giant standalone orchestrator that
makes an agent's behaviour impossible to attribute or train on.

TWO POLICIES, BOTH DETERMINISTIC, BOTH USEFUL:

  `ScriptedPolicy`  tries the action space in a fixed declared order. It is the null hypothesis any
                    trained policy has to beat, and on the evidence it may already close a real share
                    of the game corpus -- every match in this project's history came from the
                    deterministic side, never from generated C.

  `RecordingPolicy` wraps another policy and appends every step to a transcript. The transcript is the
                    training set: `(observation, action, params, result)`, labelled by whether the
                    episode ended in a CERTIFIED match. That label is not gameable -- it is the
                    certificate, not a similarity score and not a judge.

ORDER OF THE SCRIPTED POLICY IS A GUESS, AND SAYS SO. It is written down, versioned with the
transcript, and meant to be beaten; a scripted order that beat a model would be a result, not a
failure.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Protocol

from eval.tool_registry import ACTIONS, KINDS, TRANSFORM, OBSERVE, TERMINAL, validate

# Bumped when the loop or the transcript shape changes, so a training set can say which agent
# produced it. A dataset that cannot name its generator cannot be compared with another.
AGENT_VERSION = 1

# The scripted order. Free first (no model calls), then the expensive searched ones, and `compile`
# after each transform because a transform's outcome is only knowable through the oracle.
SCRIPTED_ORDER: tuple[tuple[str, dict], ...] = (
    ("compile", {}),
    ("reconstruct-wide", {}),
    ("repair-mmio", {}),
    ("invert-mutations", {"combinations": True}),
    ("resolve-placeholders", {}),
    ("diffrepair", {}),
    ("redraft", {}),
    ("regalloc-search", {"budget": 64, "beam": 8}),
    ("compile", {}),
)


@dataclass
class Context:
    """Everything a runner may read. Deterministic by construction: no clocks, no randomness."""
    function: str
    candidate: str
    target_asm_path: str | None = None
    target_dump: str | None = None
    diff: str | None = None
    source_path: str | None = None
    compile_fn: Callable[[str], dict] | None = None
    m2c: str | None = None
    ido_trace_cc: str | None = None
    timeout: int = 120
    # WHAT THE CAMPAIGN'S INTAKE MECHANISMS NEED AND THE CONTEXT NEVER CARRIED. `header_variant` takes
    # the repo root and the TU target, and without them the action registry could only offer the
    # defaults-only placeholder rewrite -- a weaker copy of a route the completion campaign already
    # owns (`solver/compile_recovery.py`, active on noncompiling intake). Both are known when the
    # context is built; they were simply not passed on.
    repo: str | None = None
    target: str | None = None
    # THE FUNCTION'S WORKSPACE, which is what the traced-compile path needs (`uopt_diagnosis` reads the
    # recorded compiler recipe from it). It is already known where the context is built and was never
    # passed on, which is the same omission that left `target_dump` unset and `uopt-trace` unable to run.
    workspace: str | None = None
    # SET BY THE LOOP BEFORE EVERY DECISION, never by a policy: remaining budget is an OBSERVATION
    # (spec §1), and a policy that has to guess it will either stop early or run past the cap. The
    # renderer prints it; the loop owns it.
    budget_remaining: int | None = None
    # THE CONTROLLER'S OWN FIRST COMPILE, as a verdict. `build_context` compiles the starting
    # candidate before the episode (it has to: the diff has to describe THIS source), and that result
    # used to exist only in `context.diff` -- invisible to the policy, so the observation said
    # "unverified" while a fresh verdict sat one field away, and on the collected functions the
    # compile FAILED, meaning the entire residual (the compiler's own error) was hidden from the model
    # that was being asked what to do about it. The loop records it as step -1 so the state the first
    # decision is made from includes it, and no action slot is charged for work already done.
    initial_verdict: dict | None = None

    def as_dict(self) -> dict:
        """JSON-safe view, with callables replaced by a marker so a transcript stays serialisable."""
        out = {}
        for key, value in asdict(self).items():
            out[key] = "<callable>" if callable(value) else value
        return out


@dataclass
class Step:
    index: int
    action: str
    kind: str
    params: dict
    status: str
    changed: bool
    exact: bool
    detail: dict = field(default_factory=dict)
    # THE RETAINED CANDIDATE'S IDENTITY. `candidate_sha256` is the source the episode CONTINUES with, which
    # is the incumbent -- not necessarily what this action produced. After a rejected child those differ,
    # and before this field existed the rejected child's score and diff were stored under the parent's hash.
    candidate_sha256: str = ""
    # THE ATTEMPTED CANDIDATE'S OWN IDENTITY. Present exactly when the action produced a source that was
    # judged and NOT adopted, so a reader can tell "the action produced nothing" from "the action produced
    # something worse than what we already had". Without it the rejection is visible only as a prose string.
    attempted_sha256: str = ""
    # The state the decision was made FROM. Without it a transcript cannot say what the policy saw,
    # which makes it useless as a training example: the label would be attached to the wrong input.
    pre_action_sha256: str = ""
    raw_response: str = ""
    cancelled: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Transcript:
    function: str
    steps: list[Step]
    exact: bool
    stop_reason: str
    seconds: float
    agent_version: int = AGENT_VERSION
    policy: str = ""
    candidates_seen: int = 0

    def as_dict(self) -> dict:
        return {**{k: v for k, v in asdict(self).items() if k != "steps"},
                "steps": [s.as_dict() for s in self.steps]}

    def to_jsonl(self) -> str:
        return json.dumps(self.as_dict())


class Policy(Protocol):
    """Chooses ONE action. Nothing else -- the loop owns dispatch, the certificate owns truth."""
    name: str

    def choose(self, context: Context, history: list[Step]) -> tuple[str, dict]: ...


class ScriptedPolicy:
    """A fixed declared order, with no model. The null hypothesis."""

    def __init__(self, order=SCRIPTED_ORDER, name: str = "scripted"):
        self.order = tuple(order)
        self.name = name

    def choose(self, context: Context, history: list[Step]) -> tuple[str, dict]:
        from eval.tool_agent_probe import compile_state

        done = {step.action for step in history}
        state = compile_state(context.candidate, history)
        # HONEST STOPPING IS PART OF THE NULL HYPOTHESIS. This policy used to keep proposing actions
        # after the certificate had already passed and after the budget was gone, because it only
        # stopped when its declared order ran out -- which made the scripted arm look busy and made
        # every stopping metric unmeasurable against it. A fixed order that ignores the verdict is not
        # a baseline for a policy that is supposed to read it.
        if state["exact"]:
            return "stop", {"reason": "certified match"}
        if context.budget_remaining is not None and context.budget_remaining <= 0:
            return "stop", {"reason": "budget exhausted"}
        # COMPILE ONLY WHEN THE SOURCE IS UNVERIFIED -- the same contract the prompt states and the
        # loop implements. This policy used to compile first and again after every change, which was
        # right when the controller did not compile a transform's output; now that it does, the
        # second compile re-asks a question the framework has already answered. A baseline that
        # violates the stated contract on its first move is not a null hypothesis, it is a handicap
        # introduced by the harness -- and the redundant-compile metric would have charged it for a
        # rule the other arm was taught.
        if not state["verified"]:
            return "compile", {}

        for action, params in self.order:
            if action in ("compile",) or action in done or action not in ACTIONS:
                continue
            # PREREQUISITES ARE PART OF THE STATE, NOT OF THE ORDER. Proposing `regalloc-search`
            # when the context has no `target_dump` is a legal call whose only possible answer is
            # "not-applicable", and a fixed order that does not look is not a fair null hypothesis
            # for a policy that is graded on looking.
            if not all(context.as_dict().get(key) for key in ACTIONS[action].needs):
                continue
            if action == "reconstruct-wide":
                recipe = (context.initial_verdict or {}).get("compiler_recipe") or {}
                if recipe.get("settings", {}).get("C_MIPS") != "-mips3 -32":
                    continue
            if action == "repair-mmio" and ("_REG" not in context.candidate or "do" not in context.candidate):
                continue
            return action, params
        return "stop", {"reason": "the scripted order is exhausted"}


def _rank(verdict: dict | None) -> tuple:
    """How good a candidate is, from the certificate: exact, then compiled, then score.

    Used to decide whether a transform's output REPLACES the candidate. `exact` first because it is the
    only thing that ends an episode; `compiled` before `score` because a candidate that does not compile
    has no score to compare (it reports 0.0, which would otherwise read as a large regression from any
    compiling candidate).
    """
    verdict = verdict or {}
    return (bool(verdict.get("exact")), bool(verdict.get("compiled")),
            float(verdict.get("score") or 0.0))


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def run_episode(context: Context, policy: Policy, *, budget: int = 8,
                runners: dict[str, Callable] | None = None,
                on_candidate: Callable[[str], None] | None = None) -> Transcript:
    """Observe, act, repeat. `budget` bounds ACTIONS, not compiles, so a transform that compiles
    many candidates internally cannot spend the budget invisibly."""
    from eval import tool_runners
    table = runners if runners is not None else {
        action.runner: action.resolve() for action in ACTIONS.values() if action.runner}

    started = time.time()
    steps: list[Step] = []
    candidate = context.candidate
    best_exact = False
    candidates_seen = 0
    stop_reason = "budget exhausted"
    # THE INCUMBENT'S VERDICT, so a transform can be judged against it. Measured on the intake actions:
    # `header-context` produced a certified exact match on one non-compiling function AND made two
    # already-compiling functions worse (score 100 -> 0.0). The loop adopted `result["source"]`
    # unconditionally, so the second behaviour would have replaced a good candidate with a broken one --
    # the action set was safe to adopt blind only while every action was a refinement that could
    # plausibly improve the incumbent.
    incumbent = dict(context.initial_verdict or {})
    if context.initial_verdict:
        # Step -1: the controller's pre-episode compile. Recorded, hashed against the candidate it
        # describes, and NOT charged to the budget -- it is state, not a decision.
        verdict = context.initial_verdict
        steps.append(Step(
            index=-1, action="compile", kind="observe",
            params={"source": "the candidate the episode started from (compiled by the controller)"},
            status="ok" if verdict.get("compiled") else "failed", changed=False,
            exact=bool(verdict.get("exact")),
            detail={key: verdict.get(key) for key in
                    ("certificate_status", "compiled", "exact", "diff", "stderr", "score",
                     "receipt_id", "verification", "frontend")},
            pre_action_sha256=_sha(candidate), candidate_sha256=_sha(candidate)))
        best_exact = best_exact or bool(verdict.get("exact"))

    for index in range(budget):
        # SYNC THE STATE BEFORE ASKING. The first version of this loop called `policy.choose` and
        # only afterwards assigned `context.candidate = candidate`, so at decision N+1 the policy
        # still saw the source from BEFORE transform N -- a read-only probe observed the parent at
        # both decisions while the intervening transform had produced a child. A policy cannot act on
        # a candidate it was never shown, and every transcript collected that way attached its label
        # to the wrong input.
        context.candidate = candidate
        context.budget_remaining = budget - index
        pre_action_sha = _sha(candidate)
        action_name, params = policy.choose(context, steps)
        try:
            request = validate(action_name, params)
        except ValueError as exc:
            steps.append(Step(index=index, action=str(action_name), kind="invalid", params=params,
                              status="invalid", changed=False, exact=False,
                              detail={"error": str(exc)}, pre_action_sha256=pre_action_sha,
                              candidate_sha256=pre_action_sha))
            stop_reason = f"policy proposed an invalid action: {exc}"
            break

        action = ACTIONS[request["action"]]
        if action.kind == TERMINAL:
            # RECORDED, not merely acted on. A terminal action is a decision like any other, and a
            # transcript that drops it cannot show whether the policy stopped honestly or ran out of
            # budget -- two very different behaviours that would look identical.
            steps.append(Step(index=index, action=action.name, kind=action.kind,
                              params=request["params"], status="terminal", changed=False,
                              exact=False, detail={"reason": request["params"].get("reason") or ""},
                              pre_action_sha256=pre_action_sha,
                              candidate_sha256=pre_action_sha))
            stop_reason = str(request["params"].get("reason") or "policy stopped")
            break

        runner = table.get(action.runner)
        if runner is None:
            result = {"status": "unwired", "changed": False, "exact": False,
                      "reason": f"{action.name} is declared but not wired"}
        else:
            # A FAILING TOOL IS AN OBSERVATION, NOT THE END OF THE EPISODE. `uopt_trace` used to raise
            # AttributeError five functions into a run and take the whole head-to-head with it; a
            # malformed return from a runner did the same one line later at `result.get`. The
            # controller owns state consistency, so it records the failure -- with the exception type
            # and text, so the model can see WHICH infrastructure is blocked -- and lets the policy
            # decide what to do about it. Blocked infrastructure is a legal terminal reason (spec §2).
            try:
                result = runner(context.__dict__, request["params"])
            except Exception as exc:                            # noqa: BLE001
                result = {"status": "runner-error", "changed": False, "exact": False,
                          "reason": f"the {action.name} runner raised {type(exc).__name__}: {exc}"}
            if not isinstance(result, dict):
                result = {"status": "runner-error", "changed": False, "exact": False,
                          "reason": f"the {action.name} runner returned "
                                    f"{type(result).__name__}, not a result dict"}

        changed = bool(result.get("changed"))
        new_source = result.get("source")
        produced = result.get("candidates") or []
        candidates_seen += len(produced)
        internal_compiles = 0

        # A transform's outcome is only knowable through the oracle, so the loop compiles what the
        # runner produced rather than trusting a `changed` flag. `exact` here is the CERTIFICATE's
        # verdict, fetched by the caller's compile_fn, never the runner's opinion.
        #
        # THE COMPILATION CONTRACT (spec §1, decided here and mirrored in the prompt): THE CONTROLLER
        # COMPILES, and it now keeps the WHOLE verdict. The framework compiles the initial candidate
        # before the episode and every source a transform produced, and the verdict -- certificate
        # status, exactness, diff, compiler stderr, score -- is attached to the step it describes.
        # The policy is therefore never required to choose `compile` to learn about the current
        # source, and the observation says so explicitly. The first version kept only `exact` and
        # `certificate_status`, so the fresh residual and the compiler's error text were thrown away
        # one frame after being produced and the model was asked what to do about a candidate it
        # could not see.
        # A FABRICATED FLAG CANNOT END THE EPISODE (spec §7). Exactness is conferred by the
        # certificate and by nothing else, so a runner's `exact` counts only when it declares that
        # the value came from the oracle callback (`certified`) or when the action IS the oracle call.
        # Without this an action could report its own success, the loop would record a certified match
        # that no object comparison ever made, and -- worse for training -- the transcript would
        # contain a positive label nothing verified. The claim is kept as `exact_claimed` so the
        # receipt shows what was asserted and what was proven.
        if result.get("exact") and not (action.name == "compile" or result.get("certified")):
            result = {**result, "exact": False, "exact_claimed": True,
                      "reason": (str(result.get("reason") or "")
                                 + " [uncertified exact claim ignored: exactness comes from the "
                                   "object certificate]").strip()}
        if result.get("exact"):
            best_exact = True
        adopted = True
        attempted_source: str | None = None
        if changed and isinstance(new_source, str) and context.compile_fn:
            verdict = context.compile_fn(new_source)
            candidates_seen += 1
            internal_compiles += 1
            # KEEP THE BETTER CANDIDATE, not the most recent one. A transform that cannot be judged (no
            # verdict) is adopted as before; one that is judged worse is recorded and dropped, with the
            # reason in the step so a reader can see the action ran and was rejected rather than inferring
            # it never happened.
            adopted = _rank(verdict) >= _rank(incumbent)
            if adopted:
                incumbent = dict(verdict)
            else:
                attempted_source = new_source
            result = {**result, "adopted": adopted,
                      **({} if adopted else {
                          "rejected_because": f"worse than the incumbent "
                                              f"({_rank(incumbent)} vs {_rank(verdict)})"}),
                      **{key: verdict.get(key) for key in
                         ("exact", "compiled", "certificate_status", "diff", "stderr", "score",
                          "faults", "verification", "frontend")}}
            best_exact = best_exact or bool(verdict.get("exact"))
            # THE DIFF BELONGS TO THE SOURCE IT DESCRIBES, AND ONLY WHEN THAT SOURCE IS ADOPTED. This was
            # unconditional, so a REJECTED child's diff became the context's diff while the candidate stayed
            # the parent -- reproduced: `context.diff == CHILD_DIFF` with `context.candidate == PARENT`.
            # `diffrepair` reads `context.diff` and would then edit the parent's struct according to a
            # verdict about a different source, and the model was shown feedback for a candidate it is not
            # being asked about. A rejected child's verdict is kept on the STEP, where it is a true record
            # of what was tried, and out of the CONTEXT, where it would be a false statement about the
            # current candidate.
            if adopted:
                context.diff = verdict.get("diff") or None
                if context.initial_verdict is not None:
                    # ONE EXPLICIT CURRENT VERDICT. Runners read `initial_verdict` for the diagnostics they
                    # key on; leaving the episode's FIRST verdict there after an adopted transform means
                    # every later runner is reasoning about a source that no longer exists. The name is
                    # historical; the contents are now the incumbent's.
                    context.initial_verdict = {**verdict, "source_sha256": _sha(new_source)}
        if action.name == "invert-mutations" and produced and context.compile_fn:
            for item in produced:
                verdict = context.compile_fn(item["source"])
                candidates_seen += 1
                internal_compiles += 1
                if verdict.get("exact"):
                    result = {**result, "exact": True, "source": item["source"],
                              "inverse": item["name"]}
                    changed, best_exact = True, True
                    break

        if changed and isinstance(result.get("source"), str) and adopted:
            candidate = result["source"]
            # An exact stop or the final budget slot has no next decision to
            # synchronize state. Callers must receive the source we certified.
            context.candidate = candidate
            if on_candidate is not None:
                on_candidate(candidate)

        # FLATTEN THE NESTED detail. `diffrepair` returns its repair info under a `detail` key, and
        # copying results wholesale put it at `step.detail["detail"]` -- a shape the renderer's
        # whitelist never reads, so the one runner whose whole point is reporting WHICH field moved
        # had its report invisible. Nested or flat, the observation must show it.
        step_detail = {k: v for k, v in result.items()
                       if k not in ("source", "candidates", "detail")}
        if isinstance(result.get("detail"), dict):
            step_detail = {**result["detail"], **step_detail}
        if internal_compiles:
            step_detail["internal_compiles"] = internal_compiles

        # THE ATTEMPTED VERDICT GOES UNDER THE ATTEMPTED SOURCE'S HASH. When a child is rejected, `detail`
        # carries that child's compile verdict while `candidate_sha256` names the parent the episode keeps
        # -- so the step read as "the parent scored 20", which is the opposite of what happened. The
        # attempted source gets its own identity and its verdict moves under it, leaving the step's own
        # detail describing the retained candidate.
        if attempted_source is not None:
            attempted_verdict = {k: step_detail.pop(k) for k in
                                 ("score", "diff", "stderr", "compiled", "certificate_status",
                                  "faults", "verification", "frontend")
                                 if k in step_detail}
            step_detail["attempted"] = {"source_sha256": _sha(attempted_source),
                                        "adopted": False, **attempted_verdict}

        steps.append(Step(index=index, action=action.name, kind=action.kind, params=request["params"],
                          status=str(result.get("status") or "unknown"), changed=changed,
                          exact=bool(result.get("exact")),
                          detail=step_detail,
                          pre_action_sha256=pre_action_sha,
                          candidate_sha256=_sha(candidate),
                          attempted_sha256=_sha(attempted_source) if attempted_source else "",
                          # A CANCELLATION IS A DECISION EVENT AND IS KEPT. The field existed and was
                          # never written, so a cancelled tool call was indistinguishable from a
                          # completed one in every transcript.
                          cancelled=bool(result.get("cancelled")),
                          raw_response=str(result.get("raw_response") or "")[:2000]))
        if best_exact:
            stop_reason = "certified match"
            break

    return Transcript(function=context.function, steps=steps, exact=best_exact,
                      stop_reason=stop_reason, seconds=round(time.time() - started, 3),
                      policy=getattr(policy, "name", type(policy).__name__),
                      candidates_seen=candidates_seen)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--space", action="store_true", help="print the action space and exit")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    from eval.tool_registry import action_space, unwired_actions
    payload = {"agent_version": AGENT_VERSION, "scripted_order": [
        {"action": a, "params": p} for a, p in SCRIPTED_ORDER], **action_space(),
        "unwired": unwired_actions()}
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
