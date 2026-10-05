"""THE ACTION SPACE: what the model is allowed to do, declared once and inspectable.

WHY THIS FILE EXISTS
--------------------
A tool-calling policy is only trainable, evaluable and replayable if its output space is FINITE,
ENUMERABLE and VALIDATABLE. Free-form Python is none of those: it cannot be enumerated, it cannot be
replayed deterministically, and a bad action is indistinguishable from a crash. So the action space
is declared here as data -- named actions, typed parameters with bounds -- and the model's entire
output is one of those actions with arguments.

WHAT DETERMINISM REQUIRES, AND WHERE IT LIVES
---------------------------------------------
Four separate things, and this module owns the first:

  action space   finite, enumerable, typed            <- HERE
  environment    (tool, params, input) -> same result  <- each runner, and each has a test
  reward         the certificate                       <- solver.byte_certificate
  evaluation     paired, equal-budget, off-policy      <- eval/equal_budget_eval.py

With those four fixed, the ONLY stochastic component is the policy, which is what makes an offline
replay exact and a policy trainable on the small amount of data this project has.

THE ORACLE IS NOT AN ACTION
---------------------------
`compile` and `stop` are declared because a policy must be able to ask "where am I" and "give up",
but the VERDICT is never the model's to make. Every transform's outcome is certified by
`solver.byte_certificate` outside this module. A tool that returns `exact` is reporting what the
certificate said, never judging for itself.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# The three kinds, and why the distinction is load-bearing for the loop:
#   transform  changes the candidate under consideration
#   observe    changes what the policy KNOWS, not the candidate (so it can loop forever if unbounded)
#   terminal   ends the episode
TRANSFORM, OBSERVE, TERMINAL = "transform", "observe", "terminal"
KINDS = (TRANSFORM, OBSERVE, TERMINAL)


@dataclass(frozen=True)
class Param:
    """One typed argument. Bounded wherever a bound is meaningful, so the space stays finite."""
    name: str
    kind: str                                  # "int" | "str" | "bool" | "choice"
    summary: str = ""
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def coerce(self, value: Any) -> Any:
        """Validate and normalise one argument. Raises ValueError with the reason."""
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.name}: {value!r} is not one of {list(self.choices)}")
            return value
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.name}: expected true/false, got {value!r}")
            return value
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.name}: expected an integer, got {value!r}")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.name}: {value} is below the minimum {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.name}: {value} is above the maximum {self.maximum}")
            return value
        if self.kind == "str":
            if not isinstance(value, str):
                raise ValueError(f"{self.name}: expected a string, got {value!r}")
            return value
        raise ValueError(f"{self.name}: unknown parameter kind {self.kind!r}")

    def as_dict(self) -> dict:
        out = {"name": self.name, "kind": self.kind, "summary": self.summary}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.default is not None:
            out["default"] = self.default
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out


@dataclass(frozen=True)
class Action:
    """One thing the policy may do. `runner` is a dotted path, resolved lazily on dispatch."""
    name: str
    kind: str
    summary: str
    params: tuple[Param, ...] = ()
    runner: str = ""
    # What the action must be given to run at all. Declared so a missing input is reported as
    # "not applicable, because X" rather than looking like a tool that quietly did nothing -- the
    # failure mode this project has hit four times.
    needs: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "summary": self.summary,
                "params": [p.as_dict() for p in self.params],
                "wired": bool(self.runner), "needs": list(self.needs)}

    def resolve(self) -> Callable:
        if not self.runner:
            raise RuntimeError(
                f"action {self.name!r} is DECLARED BUT NOT WIRED. It appears in the action space so "
                f"a policy can see the full repertoire, and invoking it fails loudly rather than "
                f"returning an empty result that would read as a tool with nothing to do.")
        module_name, _, attribute = self.runner.rpartition(".")
        return getattr(importlib.import_module(module_name), attribute)


def _p(name, kind, summary, **kw) -> Param:
    return Param(name=name, kind=kind, summary=summary, **kw)


# --- THE ACTION SPACE ----------------------------------------------------------
#
# Built from the deterministic machinery that already exists. The registry does not reimplement any
# of it: each runner is the existing entry point, so a fix to a tool reaches the policy for free and
# there is no second copy to drift.

ACTIONS: dict[str, Action] = {a.name: a for a in (
    Action(
        name="compile",
        kind=OBSERVE,
        summary=("Compile the current candidate and report the certificate verdict plus the fault "
                 "profile. This is the observation, not a decision: exactness is the certificate's "
                 "call, never the action's."),
        params=(_p("save", "bool", "record the candidate as the new incumbent if it is exact",
                   default=True),),
        runner="eval.tool_runners.compile_candidate",
        # `compile_fn` was missing from this declaration while the runner refused to run without it:
        # `compile_candidate` returns `not-applicable: the context does not carry candidate,
        # compile_fn`. A declaration that under-states what a tool needs is how a policy ends up
        # proposing an action that cannot possibly answer, so the two are now checked against each
        # other by `tests/test_tool_action_dataset.py::test_declared_prerequisites_match_the_runners`.
        needs=("candidate", "compile_fn"),
    ),
    Action(
        name="reconstruct-wide",
        kind=TRANSFORM,
        summary=("Reconstruct a complete recognized 64-bit arithmetic helper from target assembly "
                 "and the MIPS III/o32 ABI. Applicable to compiling wrong drafts as well as broken ones; "
                 "the controller must compile and certify the proposed C."),
        params=(), runner="eval.tool_runners.closed_wide_runtime",
        needs=("candidate", "function", "workspace", "target_asm_path", "initial_verdict"),
    ),
    Action(
        name="repair-mmio",
        kind=TRANSFORM,
        summary=("Reconstruct an encoded hardware-register polling loop and an uncached OR-address "
                 "word access. Requires original instruction words and big-endian o32; proposes C "
                 "without claiming device semantics or exactness."),
        params=(), runner="eval.tool_runners.mmio_repair",
        needs=("candidate", "function", "workspace", "target_asm_path"),
    ),
    Action(
        name="diffrepair",
        kind=TRANSFORM,
        summary=("Repair struct layout from the compiler's own diff: padding, field order and field "
                 "widths are stated by the differing instructions. The one deterministic repair in "
                 "this project with a demonstrated new match."),
        params=(),
        runner="eval.tool_runners.diffrepair",
        needs=("candidate", "diff"),
    ),
    Action(
        name="regalloc-search",
        kind=TRANSFORM,
        summary=("Gradient beam search over register-allocation mutations -- the intervention with "
                 "the most evidence in this codebase. Addresses the 39.5% of game residual faults "
                 "that are allocation rather than structure."),
        params=(
            _p("budget", "int", "how many candidates the beam may compile", default=64,
               minimum=1, maximum=2048),
            _p("beam", "int", "beam width", default=8, minimum=1, maximum=64),
        ),
        runner="eval.tool_runners.regalloc_search",
        needs=("candidate", "target_dump", "compile_fn"),
    ),
    Action(
        name="resolve-placeholders",
        kind=TRANSFORM,
        summary=("Replace m2c's `?` type placeholders with concrete types. m2c writes `?` where it "
                 "cannot infer, and IDO refuses the whole translation unit for it, so this is often "
                 "the difference between 0 and a scorable candidate."),
        params=(),
        runner="eval.tool_runners.resolve_placeholders",
        needs=("candidate",),
    ),
    Action(
        name="invert-mutations",
        kind=TRANSFORM,
        summary=("Apply every registered inverse of the synthetic mutation catalogue, plus the "
                 "lost-assignment enumeration. Zero model calls; 27/27 on the synthetic panel."),
        params=(_p("combinations", "bool", "also try pairs of inverses", default=True),),
        runner="eval.tool_runners.invert_mutations",
        needs=("candidate",),
    ),
    Action(
        name="uopt-trace",
        kind=OBSERVE,
        summary=("Dump IDO's own allocator decisions for this candidate's live ranges (adjsave, "
                 "forbidden colours, interference). Reads the compiler instead of guessing at it; "
                 "the deepest compiler model in the repository."),
        params=(_p("level", "choice", "uopt debug level", default="5", choices=("5", "6")),),
        runner="eval.tool_runners.uopt_trace",
        # The real prerequisites, now that the runner calls the real producer: `solver.uopt_diagnosis`
        # needs the workspace's recipe to drive the traced compile and both object dumps to attribute
        # what the allocator did. The previous declaration named `source_path` alone, which the runner no
        # longer reads -- an under-stated `needs` is how a policy proposes an action that cannot answer.
        needs=("function", "candidate", "repo", "target_dump", "dump", "workspace"),
    ),
    Action(
        name="redraft",
        kind=TRANSFORM,
        summary=("Re-run m2c on the target assembly with NO --context. The decontaminated draft: "
                 "`tools/claude` builds ctx.c from the project's own matched source, which for a "
                 "100%-matched target IS the answer, so a draft made that way is partly the "
                 "reference and cannot be used as a starting point."),
        params=(),
        runner="eval.tool_runners.redraft",
        # `repo` and `function` are required now that the runner re-drafts through the canonical producer
        # (`workspace.m2c_draft`) instead of shelling out to the m2c binary: the producer needs the
        # function's workspace. Declared here because a `needs` that under-states an action's inputs is
        # how a policy proposes something that cannot possibly run.
        needs=("target_asm_path", "repo", "function"),
    ),
    Action(
        name="header-context",
        kind=TRANSFORM,
        summary=("Recover a compatible header/include context for a translation unit that does not "
                 "compile, via `solver.compile_recovery.header_variant`. THE INTAKE ACTION: the "
                 "campaign runs it on every noncompiling draft, and the action registry offered only "
                 "a defaults-only placeholder rewrite instead."),
        params=(),
        runner="eval.intake_runners.header_variant",
        needs=("candidate", "repo", "target"),
    ),
    Action(
        name="globals-declare",
        kind=TRANSFORM,
        summary=("Extern hypotheses for undeclared symbols, from symbol addresses and cited access "
                 "observations, via `solver.compile_recovery.globals_variant`. Needs a read-only KB "
                 "connection supplied as `kb_conn`; declines by name without one."),
        params=(),
        runner="eval.intake_runners.globals_variant",
        needs=("candidate", "repo"),
    ),
    Action(
        name="opaque-struct",
        kind=TRANSFORM,
        summary=("Build a padded candidate struct for parameters whose fields the target accesses, "
                 "via `solver.compile_obligations.opaque_variant`. Owner of the "
                 "`Selector requires struct/union pointer` class."),
        params=(),
        runner="eval.intake_runners.opaque_variant",
        needs=("candidate", "repo"),
    ),
    Action(
        name="do-while-rewrite",
        kind=TRANSFORM,
        summary=("Express a bottom-tested loop without the build-forbidden `do` token, via "
                 "`tools.score_repo_function.rewrite_do_while`. Owner of the do-while failure class."),
        params=(_p("style", "str", "for-break (default) or while", default="for-break"),),
        runner="eval.intake_runners.rewrite_do_while",
        needs=("candidate",),
    ),
    Action(
        name="negative-offsets",
        kind=TRANSFORM,
        summary=("Lower m2c's `base->unk-N` field offsets into typed byte views, via "
                 "`solver.m2c_negative_offset`. `->unk-4` is not a member access at all -- the identifier "
                 "ends at `unk` and `- 4` is a subtraction -- so cfe refuses the line as a Syntax Error "
                 "and clang reports `member reference base type ... is not a structure or union`. The "
                 "largest identified blocker among states that still will not compile."),
        params=(),
        runner="eval.intake_runners.negative_offset",
        needs=("candidate",),
    ),
    Action(
        name="source-type-declarations",
        kind=TRANSFORM,
        summary=("Recover a struct/union/enum declaration from the function's own `src/*.c` translation "
                 "unit, gated on the binary: every member the draft uses must be annotated with an offset "
                 "the target assembly touches through that parameter. Owner of clang's `incomplete "
                 "definition of type 'X'` / cfe's `'member' undefined`. A declaration is shared vocabulary "
                 "and a body is the answer -- this copies declarations only, and a match leaning on it is "
                 "tiered `reference-source-assisted`, never `SOLVED`."),
        params=(),
        runner="eval.intake_runners.source_type_declarations",
        needs=("candidate", "repo", "target"),
    ),
    Action(
        name="undeclared-identifiers",
        kind=TRANSFORM,
        summary=("Declare the names the frontend says are undefined, from the KB's own cited access "
                 "observations. Measured as the largest single intake lever: +7 IDO-compiling states on "
                 "the frozen 200-state frame, 0 of them frontend-passing -- which is why the levels are "
                 "reported separately."),
        params=(),
        runner="eval.intake_runners.undeclared_identifiers_runner",
        needs=("candidate", "function"),
    ),
    Action(
        name="or-address",
        kind=TRANSFORM,
        summary=("Lower `*(ptr | n)` into a typed byte-address view. IDO emits ORed immediates for "
                 "addresses in a mask region, and m2c reads the | as arithmetic; the cast is what lets "
                 "the expression compile. Measured +2 IDO-compiling and +2 frontend-passing states."),
        params=(),
        runner="eval.intake_runners.or_address",
        needs=("candidate",),
    ),
    Action(
        name="stop",
        kind=TERMINAL,
        summary="End the episode and report the best candidate found.",
        params=(_p("reason", "str", "why the search is stopping", default=""),),
        runner="",
        needs=(),
    ),
)}


def action_space() -> dict:
    """The whole space, JSON-serialisable. This is what goes into the prompt AND into the record.

    One description serving both is deliberate: a prompt describing a different repertoire from the
    one that can execute is the classic way an agent silently loses capability.
    """
    return {
        "schema_version": 1,
        "actions": [ACTIONS[name].as_dict() for name in sorted(ACTIONS)],
        "kinds": list(KINDS),
        "note": ("the verdict is never an action's to make; every transform is certified outside "
                 "this module"),
    }


def enumerate_actions(*, include_unwired: bool = True) -> list[dict]:
    """Every legal (action, parameters) pair, as data.

    Only bounds that make the space finite are enumerated (int parameters with both bounds, and
    choices). Free integers have no upper bound to enumerate, so they are reported with their range
    rather than expanded -- an enumeration that silently dropped them would misrepresent the space.
    """
    out: list[dict] = []
    for name in sorted(ACTIONS):
        action = ACTIONS[name]
        if not action.runner and not include_unwired:
            continue
        combos: list[dict] = [{}]
        unbounded: list[str] = []
        for param in action.params:
            if param.kind == "choice":
                combos = [{**c, param.name: v} for c in combos for v in param.choices]
            elif param.kind == "bool":
                combos = [{**c, param.name: v} for c in combos for v in (True, False)]
            elif param.kind == "int" and param.minimum is not None and param.maximum is not None:
                width = param.maximum - param.minimum + 1
                if width * len(combos) > 4096:
                    unbounded.append(f"{param.name} (range {param.minimum}..{param.maximum})")
                    continue
                combos = [{**c, param.name: v}
                          for c in combos for v in range(param.minimum, param.maximum + 1)]
            else:
                unbounded.append(f"{param.name} ({param.kind})")
        for combo in combos:
            out.append({"action": name, "params": combo, "kind": action.kind,
                        "unbounded": unbounded})
    return out


def validate(action_name: str, params: dict | None = None) -> dict:
    """Normalise one proposed action. Raises ValueError naming the problem."""
    if action_name not in ACTIONS:
        raise ValueError(f"unknown action {action_name!r}; the space is {sorted(ACTIONS)}")
    action = ACTIONS[action_name]
    given = dict(params or {})
    known = {p.name for p in action.params}
    unknown = sorted(set(given) - known)
    if unknown:
        raise ValueError(f"{action_name}: unknown parameter(s) {unknown}; it accepts {sorted(known)}")
    out = {}
    for param in action.params:
        if param.name in given:
            out[param.name] = param.coerce(given[param.name])
        elif param.default is not None:
            out[param.name] = param.default
    return {"action": action_name, "params": out, "kind": action.kind}


def validate_json(text: str) -> dict:
    """Parse and validate a policy's raw output: `{"action": ..., "params": {...}}`."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"action is not JSON: {exc}") from exc
    if not isinstance(payload, dict) or "action" not in payload:
        raise ValueError('action must be an object with an "action" key')
    return validate(payload["action"], payload.get("params") or {})


def unwired_actions() -> list[str]:
    """Declared TRANSFORMS/OBSERVATIONS with no implementation. Reported rather than hidden, because
    a repertoire smaller than the prompt claims is exactly how an agent loses a capability silently.

    Terminal actions are excluded: `stop` has no runner BY DESIGN, since the loop handles it, and
    listing it as unwired would train a reader to ignore this report.
    """
    return sorted(name for name, action in ACTIONS.items()
                  if not action.runner and action.kind != TERMINAL)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--enumerate", action="store_true", help="expand every legal action")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    payload = action_space()
    payload["unwired"] = unwired_actions()
    if args.enumerate:
        payload["enumerated"] = enumerate_actions()
        payload["enumerated_count"] = len(payload["enumerated"])
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
