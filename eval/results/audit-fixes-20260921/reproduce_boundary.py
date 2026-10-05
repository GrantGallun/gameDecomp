"""Reproduce the audit's finding 1 against the CURRENT code: does a rejected child's diff become the
current candidate's verdict?

THE CLAIM. `eval/tool_agent.py` adopts only a better candidate (`adopted = _rank(verdict) >= _rank(incumbent)`)
but then refreshes `context.diff` from that verdict unconditionally. So after a REJECTED child the next
decision is handed:

    candidate = PARENT
    diff      = REJECTED_CHILD_DIFF

which misdirects `diffrepair` (it repairs the struct named by a diff about a different source) and, in
collection, attaches training evidence to the wrong state.

WHAT THIS DOES. Drives the real `run_episode` with a scripted policy and a stub `compile_fn`, so the assertion
is on the interface the policy actually reads -- the audit's own point that a renderer unit test is not
enough. No compiler, no model, no network.

Every check prints its own verdict, and the script exits non-zero if the defect is present, so it can be used
as a regression directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

# RUNNABLE FROM EITHER SIDE. The first version hardcoded the WSL repo path, so the script only worked
# under `wsl.exe bash` and died with `ModuleNotFoundError: No module named 'eval'` on Windows -- a receipt
# that cannot be re-run where it is read is not a reproduction.
ROOT = Path(__file__).resolve().parents[3]
for candidate in (str(ROOT), "/mnt/c/Code/gameDecomp"):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from eval.tool_agent import Context, run_episode                             # noqa: E402

PARENT = "/* parent */\nint f(void) { return 1; }\n"
CHILD = "/* child */\nint f(void) { return 2; }\n"
PARENT_DIFF = "-lbu v1,0x24(a0)\n+lb v1,0x10(a0)\n"
CHILD_DIFF = "-lw v0,0x08(a0)\n+sw v0,0x0C(a0)\n"

seen: list[dict] = []


def compile_fn(source: str) -> dict:
    """Parent scores 80, child scores 20. Deterministic and explicit, as in the audit's reproduction."""
    if source == CHILD:
        return {"compiled": True, "exact": False, "score": 20.0, "diff": CHILD_DIFF,
                "stderr": "", "certificate_status": "object_sections_differ"}
    return {"compiled": True, "exact": False, "score": 80.0, "diff": PARENT_DIFF,
            "stderr": "", "certificate_status": "object_sections_differ"}


class OneBadAction:
    """Proposes one transform that produces the worse child, then stops.

    A POLICY, not a runner, so the loop's own adoption logic is what is under test. The interface is
    `choose(context, steps) -> (action_name, params)`, taken from `run_episode` rather than assumed.
    """

    def __init__(self) -> None:
        self.calls = 0

    def choose(self, context, steps) -> tuple[str, dict]:
        self.calls += 1
        # The observation the SECOND decision is made from: this is what the audit's claim is about.
        seen.append({"diff": context.diff, "candidate_is_parent": context.candidate == PARENT})
        if self.calls > 1:
            return ("stop", {"reason": "one action was enough"})
        return ("redraft", {})


def stub_redraft(context: dict, params: dict) -> dict:
    return {"status": "ok", "changed": True, "exact": False, "source": CHILD,
            "reason": "", "detail": {"stage": "stub"}}


context = Context(function="f", candidate=PARENT, compile_fn=compile_fn,
                  target_asm_path="target.s", repo=".", target="build/src/f.o",
                  diff=PARENT_DIFF,
                  initial_verdict={"compiled": True, "exact": False, "score": 80.0,
                                   "diff": PARENT_DIFF, "stderr": ""})

transcript = run_episode(context, OneBadAction(), budget=3,
                         runners={"eval.tool_runners.redraft": stub_redraft})

print("=" * 78)
print("THE TRANSCRIPT")
print("=" * 78)
for step in transcript.steps:
    print(f"  step {step.index} {step.action}: status={step.status} changed={step.changed} "
          f"exact={step.exact}")
    print(f"     pre_action_sha256  = {step.pre_action_sha256[:16]}")
    print(f"     candidate_sha256   = {step.candidate_sha256[:16]}")
    for key in ("adopted", "rejected_because"):
        if key in step.detail:
            print(f"     {key} = {step.detail[key]}")
    if "score" in step.detail:
        print(f"     score in the STEP detail = {step.detail['score']}")
print(f"  stop_reason: {transcript.stop_reason}")

print()
print("=" * 78)
print("THE DEFECT, STATED AS THE TWO IDENTITIES")
print("=" * 78)
parent_sha = __import__("hashlib").sha256(PARENT.encode()).hexdigest()
child_sha = __import__("hashlib").sha256(CHILD.encode()).hexdigest()

problems: list[str] = []

# 1. Is the child's diff now the context's diff, while the candidate is still the parent?
if context.diff == CHILD_DIFF:
    problems.append("context.diff is the REJECTED CHILD's diff while the candidate is still the PARENT")
else:
    print(f"  context.diff = {context.diff!r}")

# 2. Is the child's verdict recorded under the parent's candidate hash?
for step in transcript.steps:
    if step.candidate_sha256 != parent_sha and step.candidate_sha256 != child_sha:
        continue
    score = step.detail.get("score")
    if step.candidate_sha256 == parent_sha and score == 20.0:
        problems.append("a step under the PARENT's candidate hash carries the CHILD's score (20.0)")
    if step.candidate_sha256 == parent_sha and step.detail.get("diff") == CHILD_DIFF:
        problems.append("a step under the PARENT's candidate hash carries the CHILD's diff")

# 3. Did the candidate actually stay the parent?
if transcript.candidates_seen and context.candidate != PARENT:
    problems.append(f"the candidate moved after a rejected child: {context.candidate[:30]!r}")

print(f"  final candidate is still the parent : {context.candidate == PARENT}")
print(f"  final context.diff == child diff    : {context.diff == CHILD_DIFF}")
print()
if problems:
    print("DEFECT PRESENT:")
    for item in problems:
        print(f"  - {item}")
    raise SystemExit(1)
print("DEFECT NOT PRESENT: the rejected child's feedback did not become the parent's verdict.")
