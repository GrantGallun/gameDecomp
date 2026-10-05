"""The runners: what each declared action actually DOES, wired to the tools that already exist.

THE CONTRACT. A runner is `run(context, params) -> dict` and is DETERMINISTIC in `(params, context)`:
the same inputs must produce the same output, which is what makes an offline replay of a policy
exact. Nothing here judges exactness -- a runner reports what the certificate said.

A MISSING INPUT IS NOT A NO-OP. Every runner that cannot run returns `status: "not-applicable"` and
NAMES what it was missing. That is the difference between a tool that declined and a tool that
quietly did nothing, which this project has been bitten by four times.
"""
from __future__ import annotations

from pathlib import Path

# Outcomes a runner may report. `changed` is about the SOURCE, not about exactness.
OK, NO_CHANGE, NOT_APPLICABLE, FAILED = "ok", "no-change", "not-applicable", "failed"


def _missing(context, *names) -> list[str]:
    return [name for name in names if not context.get(name)]


def _skip(*names) -> dict:
    return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
            "reason": f"the context does not carry {', '.join(names)}"}


def compile_candidate(context: dict, params: dict) -> dict:
    """Compile the current candidate and report the CERTIFICATE's verdict (plus the fault profile).

    The verdict is `certify_exact`, never a similarity score: `.text` equality called a changed
    callee exact, which is why the certificate is the only oracle this project accepts.
    """
    missing = _missing(context, "candidate", "compile_fn")
    if missing:
        return _skip(*missing)
    result = context["compile_fn"](context["candidate"])
    return {"status": OK if result.get("compiled") else FAILED,
            "changed": False,
            "compiled": bool(result.get("compiled")),
            "exact": bool(result.get("exact")),
            "certificate_status": result.get("certificate_status"),
            "score": result.get("score"),
            "faults": result.get("faults") or {},
            "stderr": (result.get("stderr") or "")[-400:]}


def diffrepair(context: dict, params: dict) -> dict:
    """Repair struct layout from the compiler's own diff, via `solver.diffrepair.repair`.

    UNREACHABLE ON A CANDIDATE THAT DOES NOT COMPILE, and the reason is stated in those words rather
    than as a generic missing input. The diff only exists once a candidate compiles far enough to be
    compared, so on the front-door failure class -- a draft cfe never accepts -- this action declined
    40 of 40 states (`eval/results/intake-20260921/class-control.json`) for a reason no amount of
    plumbing can remove. That is a phase boundary, not a wiring defect, and a policy reading
    "the context does not carry diff" would keep proposing it.
    """
    if missing := _missing(context, "candidate", "diff"):
        # `diff` present-but-empty is the phase boundary described above; `candidate` absent is an
        # ordinary missing input. The registry declares both, so both names reach the reason text.
        if missing == ["diff"]:
            return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                    "reason": ("the context does not carry diff: the candidate does not compile, so the "
                               "oracle never compared it against the target and there is nothing to "
                               "repair from"),
                    "detail": {"stage": "struct-layout-repair",
                               "reachable_in_phase": "compiling candidates"}}
        return _skip(*missing)
    from solver import diffrepair as dr
    repaired, changed, info = dr.repair(context["candidate"], context["diff"])
    return {"status": OK if changed else NO_CHANGE, "changed": bool(changed),
            "exact": False, "source": repaired if changed else context["candidate"],
            "detail": {k: info.get(k) for k in sorted(info)} if isinstance(info, dict) else {},
            "note": "changed says the SOURCE moved; exactness needs a recompile"}


def invert_mutations(context: dict, params: dict) -> dict:
    """Every registered inverse of the mutation catalogue, first one that the caller can certify.

    Reports the whole candidate list rather than picking one: the caller compiles them, and choosing
    here would mean this module deciding exactness by proxy.
    """
    missing = _missing(context, "candidate")
    if missing:
        return _skip(*missing)
    from eval.deterministic_repair import repairs
    produced = repairs(context["candidate"], combinations=bool(params.get("combinations", True)))
    return {"status": OK if produced else NO_CHANGE, "changed": bool(produced), "exact": False,
            "candidates": [{"name": name, "source": text} for name, text in produced],
            "count": len(produced)}


def resolve_placeholders(context: dict, params: dict) -> dict:
    """Resolve m2c's `?` type placeholders, via `solver.m2c_placeholders.rewrite`."""
    missing = _missing(context, "candidate")
    if missing:
        return _skip(*missing)
    from solver import m2c_placeholders as mp
    resolved, names = mp.rewrite(context["candidate"])
    changed = resolved != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": resolved, "placeholders": list(names)}


def redraft(context: dict, params: dict) -> dict:
    """Re-run m2c on the target assembly with NO `--context`, through the SAME producer as the draft.

    The decontaminated draft. `tools/claude` builds `ctx.c` from the project's own matched source and
    passes it to m2c as `--context`; for a 100%-matched target those declarations ARE the reference
    answer, so a draft produced that way cannot be a solver starting point.

    CHANGE IS COMPUTED FROM CONTENT. The first version returned `changed: True` unconditionally, so
    a redraft that reproduced the current source byte for byte was recorded as a transformation --
    which would teach a policy that an action did something when it did nothing.

    THE SECOND VERSION STILL LIED, and it took a control run to see it. It re-ran the m2c BINARY
    directly, while the candidate under consideration was produced by `workspace.m2c_draft`, which
    sanitizes the assembly first (`solver/m2c_input.draft`: O32 register aliasing, optional context) and
    prepends `#include "common.h"`. Two different producers, so the comparison was between a bare draft
    and a wrapped one: the action "fired" on 40 of 40 states in `eval/results/intake-20260921/class-control.json`
    purely by deleting the wrapper -- 617 characters to 416 on `copyGfxCommandBlockToScratch`, whose
    error count then went 1 -> 6. A 40-of-40 firing rate that means "removed the include" is worse than a
    decline, because it looks like work.

    So it goes through the canonical producer, and the honest outcome is usually `no-change`: this action
    exists to give the policy a decontaminated SECOND DRAFT, and when the starting draft is already the
    assembly-only one there is nothing to re-derive. That is a measurement, not a failure.
    """
    missing = _missing(context, "target_asm_path", "repo", "function")
    if missing:
        return _skip(*missing)
    from solver import workspace

    ws = Path(context["repo"]) / "nonmatchings" / context["function"]
    if not (ws / "target.s").is_file():
        # The other branch of `m2c_draft` reads an existing `base.c`, so a workspace without the target
        # assembly is a MISSING INPUT and says so rather than shelling out to a path that does not exist.
        return _skip("a bootstrapped workspace with target.s")
    fresh = workspace.m2c_draft(ws)
    if not fresh.strip():
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": "the canonical draft producer returned nothing for this function"}
    changed = fresh.strip() != (context.get("candidate") or "").strip()
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": fresh,
            "reason": "" if changed else "the redraft reproduced the current candidate",
            "note": ("assembly-only draft via workspace.m2c_draft: no --context, so no reference "
                     "declarations, and the same sanitizer the candidate already went through")}


def closed_wide_runtime(context: dict, params: dict) -> dict:
    """Propose whole-stream wide arithmetic reconstruction, even if C compiles.

    This is a source transform: the controller owns compilation and certificates.
    Existing compile-recovery callers use exactly the same reconstruction entry.
    """
    missing = _missing(context, "candidate", "function", "workspace", "target_asm_path", "initial_verdict")
    if missing:
        return _skip(*missing)
    from solver import frontend_repair, wide_runtime_interfaces

    ws, assembly_path = Path(context["workspace"]), Path(context["target_asm_path"])
    recipe = (context["initial_verdict"] or {}).get("compiler_recipe") or {}
    flags = recipe.get("settings", {}).get("C_MIPS", "")
    if flags != "-mips3 -32":
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "requires the configured MIPS III/o32 compiler recipe"}
    if assembly_path.resolve() != (ws / "target.s").resolve():
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "assembly is not bound to the current workspace target"}
    try:
        o32 = frontend_repair.big_endian_o32(ws / "target.o")
        source, report = wide_runtime_interfaces.reconstruct_helper(
            context["candidate"], context["function"], assembly_path.read_text(),
            big_endian_o32=o32, compiler_mips=flags)
    except (OSError, ValueError) as exc:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False, "reason": str(exc)}
    changed = source != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": source, "detail": report,
            "reason": "" if changed else "no supported whole instruction stream under the target ABI"}


def mmio_repair(context: dict, params: dict) -> dict:
    """A source-only, encoded-address repair; compilation remains the controller's."""
    missing = _missing(context, "candidate", "function", "workspace", "target_asm_path")
    if missing:
        return _skip(*missing)
    from solver import frontend_repair, mmio_repair as repair, workspace
    ws = Path(context["workspace"])
    if Path(context["target_asm_path"]).resolve() != (ws / "target.s").resolve():
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "assembly is not bound to the current workspace target"}
    try:
        report = repair.propose(context["candidate"], context["function"],
            workspace.target_asm(ws, context["function"]),
            big_endian_o32=frontend_repair.big_endian_o32(ws / "target.o"))
    except (OSError, ValueError) as exc:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False, "reason": str(exc)}
    changed = bool(report["changes"])
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": report["source"], "detail": {k: v for k, v in report.items() if k != "source"},
            "reason": "" if changed else "no supported encoded poll and OR-address word access"}


def regalloc_search(context: dict, params: dict) -> dict:
    """Gradient beam search over register-allocation mutations, via `solver.regalloc_search.search`.

    WIRED TO THE ACTUAL API. The first version assumed a dict return and a one-argument compiler
    callback; the real function takes `(source, label) -> Compiled` and returns an `Outcome`
    dataclass with `exact`, `best_source`, `best_label`, `compiles` and `log`. Guessing the signature
    is what left this action reporting "not-applicable" on every function in the first run.
    """
    missing = _missing(context, "candidate", "target_dump", "compile_fn")
    if missing:
        return _skip(*missing)
    from solver import regalloc_search as rs

    def compile_candidate(source: str, label: str):
        verdict = context["compile_fn"](source)
        return rs.Compiled(compiled=bool(verdict.get("compiled")),
                           exact=bool(verdict.get("exact")),
                           dump=verdict.get("dump") or "",
                           diff=verdict.get("diff") or "",
                           evidence=verdict)

    outcome = rs.search(context["function"], context["candidate"], compile_candidate,
                        context["target_dump"], budget=int(params.get("budget", 64)),
                        beam=int(params.get("beam", 8)))
    changed = bool(outcome.best_source) and outcome.best_source != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed,
            # `certified` says WHERE the exactness came from: the search reports its own best result,
            # but every compile behind it went through the caller's oracle callback. The loop refuses
            # an `exact` claim that does not say this, so a runner cannot end an episode by asserting
            # success (spec §7).
            "certified": True,
            "exact": bool(outcome.exact), "source": outcome.best_source if changed else None,
            "best_label": outcome.best_label, "compiles": outcome.compiles,
            "trace_calls": outcome.trace_calls, "log_entries": len(outcome.log or [])}


def uopt_trace(context: dict, params: dict) -> dict:
    """Dump IDO's own allocator decisions for this candidate, via `solver.uopt_diagnosis`.

    An OBSERVATION, not a repair: it does not change the candidate, it changes what the policy knows.

    THE ENTRY POINT IS NOW THE REAL ONE. The previous version guessed five names on `solver.uopt_trace`
    -- `trace`, `dump`, `run`, `collect`, `main` -- and that module is a PARSER (it reads a `uoptlist`
    dump), so none of them exist and the action returned `not-applicable` on 40 of 40 states of the
    size-bucketed frame while the registry listed it as wired. The producer is
    `solver.uopt_diagnosis.traced_compile` (three compiles through the patched `-zdbug:5/6` toolchain)
    and the consumer is `solver.uopt_diagnosis.diagnose`. Both already existed.

    A MISSING PREREQUISITE STILL NAMES ITSELF, and the trace toolchain is one: the patched compiler is a
    separate build (`tools/ido-trace/README.md`), so its absence is `not-applicable` with the path in the
    reason, never a guess.
    """
    missing = _missing(context, "function", "candidate", "repo", "target_dump", "dump", "workspace")
    if missing:
        return _skip(*missing)
    from solver import uopt_diagnosis

    trace_cc = Path(context.get("ido_trace_cc")
                    or Path.home() / "decomp/tools-src/ido-trace/cc")
    if not trace_cc.is_file():
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": (f"the tracing compiler is not built at {trace_cc}; "
                           f"tools/ido-trace/README.md builds it")}
    workspace = context.get("workspace")
    if not workspace:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry workspace, which supplies the recorded recipe"}
    if context.get("trace_in_flight"):
        # `traced_compile` writes `uoptlist` into the REPO root and says callers must not share it
        # between concurrent traced compiles. Declining by name is the honest answer: the alternative is
        # two episodes reading each other's trace and attributing it to the wrong source.
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "another traced compile is using the repository's uoptlist"}

    texts = uopt_diagnosis.traced_compile(Path(workspace), Path(context["repo"]),
                                          context["candidate"], trace_cc, context["function"])
    if texts is None:
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": "the traced compile declined: a compile failed, the recipe is unreadable, or "
                          "uopt wrote no uoptlist"}
    report = uopt_diagnosis.diagnose(context["target_dump"], context["dump"],
                                     texts.get("level5", ""), texts.get("level6", ""),
                                     texts.get("ugen", ""), context["function"])
    if report.get("declined"):
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "observation": report,
                "reason": f"uopt's own trace could not be attributed: {report['declined']}"}
    return {"status": OK, "changed": False, "exact": False, "observation": report,
            "reason": "", "note": "observation only; the candidate is unchanged"}


RUNNERS = {
    "eval.tool_runners.compile_candidate": compile_candidate,
    "eval.tool_runners.closed_wide_runtime": closed_wide_runtime,
    "eval.tool_runners.mmio_repair": mmio_repair,
    "eval.tool_runners.diffrepair": diffrepair,
    "eval.tool_runners.invert_mutations": invert_mutations,
    "eval.tool_runners.resolve_placeholders": resolve_placeholders,
    "eval.tool_runners.redraft": redraft,
    "eval.tool_runners.regalloc_search": regalloc_search,
    "eval.tool_runners.uopt_trace": uopt_trace,
}
