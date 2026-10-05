"""The campaign's intake mechanisms, exposed as POLICY ACTIONS.

WHY THIS EXISTS. `PIPELINE_MAP.md` (intake table, and "Semantic tools already present: do not rebuild
them") lists mechanisms that are ACTIVE in the completion campaign and answer exactly the failures the
action policy was burning its budget on:

    header_variant    noncompiling TU -> compatible header candidate
    globals_variant   symbol addresses + cited access observations -> extern hypotheses
    opaque_variant    dataflow parameter accesses -> padded candidate struct
    rewrite_do_while  `do` loop -> the sanctioned for/break form

The registry offered `resolve-placeholders`, which calls `m2c_placeholders.rewrite(candidate)` with the
`s32` default -- a strictly weaker copy of machinery that already exists two directories away. So the
policy could choose between eight actions, none of which was the one the campaign uses on a candidate
that does not compile.

WHAT A RUNNER STILL HAS TO DO. Adapt, not reimplement: each returns `(new_source, report)` and each is
deterministic. A missing input returns `not-applicable` NAMING it, because that is this registry's
contract and a silent no-op is the failure mode the whole project keeps re-learning. `changed` compares
content, so an intake mechanism that reproduces the candidate is recorded as doing nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from eval.tool_runners import FAILED, NO_CHANGE, NOT_APPLICABLE, OK


def _asm(context: dict) -> str | None:
    path = context.get("target_asm_path")
    if not path or not Path(path).exists():
        return None
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _repo(context: dict) -> Path | None:
    value = context.get("repo")
    return Path(value) if value else None


def _missing(context: dict, *names) -> dict | None:
    absent = [name for name in names if not context.get(name)]
    return None if not absent else {
        "status": NOT_APPLICABLE, "changed": False, "exact": False,
        "reason": f"the context does not carry {', '.join(absent)}"}


def _result(source: str, candidate: str, report: dict, *, stage: str) -> dict:
    changed = source.strip() != (candidate or "").strip()
    entry = report.get("stage", stage)
    # A DECLINE MUST SAY WHY. `changed=False` with no reason is indistinguishable from a mechanism that
    # crashed quietly or was never handed its inputs -- the ambiguity this project keeps paying for. The
    # probe reports reasons, so a mechanism that had nothing to do reads differently from one that was
    # asked the wrong question.
    #
    # AND THE MECHANISM'S OWN REASON OUTRANKS THE GENERIC ONE. A pass that declined ON PURPOSE
    # (`declined_reason`: "this would redeclare the type name 'RacePlayer'") is a different event from a
    # pass that found nothing to do, and the generic sentence would erase the difference.
    explicit = str(report.get("declined_reason") or report.get("reason") or "")
    reason = ("" if changed else (explicit or f"{entry} produced no change for this candidate"))
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": source, "entry": entry, "reason": reason,
            # The report is the receipt for WHY the mechanism fired or declined. Kept flat so the
            # renderer's whitelist shows it instead of burying it under a nested key.
            #
            # `declined` IS IN THE WHITELIST NOW. A proposal the action considered and refused used to
            # leave no trace at all, so "the action declined three layouts" and "the action had nothing to
            # consider" produced identical receipts -- and the absent-type gap survived several rounds
            # because of exactly that. Every decline carries a reason and the evidence it had.
            "detail": {k: v for k, v in (report or {}).items()
                       if k in ("stage", "unresolved", "plans", "added", "removed_headers",
                                "accepted", "layout", "field_mapping", "authority",
                                "declined_reason", "declined", "derived_void_parameters",
                                "tag_aliases", "frontend_names", "frontend_unavailable",
                                "unresolved_names")}}


def resolve_placeholders(context: dict, params: dict) -> dict:
    """Resolve m2c's `?` type placeholders before anything else looks at the file.

    THE FIRST STEP OF THE CAMPAIGN'S INTAKE, not an optional extra. `eval/completion_campaign.py` applies
    this to every draft before it offers the header and symbol adapters, for a measured reason: cfe STOPS
    at the placeholder line and truncates its error list there, so every later adapter was reasoning about
    a file whose real defects were invisible. Measured on the size-bucketed frame
    (`eval/results/intake-20260921`): without this step the intake sequence converts 0 of 40, and every
    one of the drafts it was judging died on a `?` the front end refuses.

    `params["widths"]` passes types derived from the TARGET (`eval/binary_types.types_for`). It is a
    parameter rather than the default because deriving them needs the assembly and a KB, and a caller
    without those should get the `s32` default rather than a guess wearing the word "derived".
    """
    missing = _missing(context, "candidate")
    if missing:
        return missing
    from solver import m2c_placeholders as mp

    widths = params.get("widths") or None
    source, names = mp.rewrite(context["candidate"], widths)
    changed = source != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": source, "entry": "m2c-type-placeholder", "placeholders": list(names),
            # A decline must say which of the two decline reasons it was: nothing to do, or a `?` the
            # pattern cannot type. They look identical from `changed=False` and only one is a bug.
            "reason": "" if changed else
                      ("the candidate contains no `?` placeholder" if "?" not in context["candidate"]
                       else "the candidate contains a `?` no rule in m2c_placeholders can type"),
            "detail": {"stage": "m2c-type-placeholder", "resolved": list(names),
                       "widths_supplied": sorted((widths or {}))[:12]}}


def negative_offset(context: dict, params: dict) -> dict:
    """`base->unk-N` -> a typed byte view, via `solver.m2c_negative_offset.rewrite`.

    The largest identified construct among states that still will not compile after the rest of the intake
    route has run (8 of 31 by first error on the size-bucketed frame, and more once the first blocker is
    passed). It needs no frontend and no KB: the draft already says which field, and the spelling is
    forced. Every decline carries its reason, so "nothing to do" stays distinguishable from "the base has
    no type here".
    """
    missing = _missing(context, "candidate")
    if missing:
        return missing
    from solver import m2c_negative_offset

    source, changes = m2c_negative_offset.rewrite(context["candidate"])
    accepted = [c for c in changes if "after" in c]
    changed = source != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": source, "entry": "negative-field-offsets",
            "reason": ("" if changed else
                       ("the candidate has no `->unk-N` access" if not changes
                        else f"the candidate has {len(changes)} `->unk-N` access(es) and none is "
                             f"rewritable: {changes[0]['declined']}")),
            "detail": {"stage": "negative-field-offsets", "rewritten": len(accepted),
                       "declined": len(changes) - len(accepted), "changes": changes[:6]}}


def frontend_diagnostics(context: dict, params: dict) -> dict:
    """Run the project's clang checker on the candidate and report what it sees.

    NOT IN `RUNNERS` AND NOT IN THE INTAKE SEQUENCE, deliberately. It is written and tested and ready, but
    wiring a seventh step into the measurement changes what is being measured, and the measurement is the
    thing that was under-resourced: on a 40-state frame whose membership moved 4 of 40 between two builds,
    the effect of a new step cannot be resolved. The enlarged frame comes first; this gets wired against
    it, where its effect is visible.

    AN OBSERVATION. It does not change the source -- `changed` is always False -- it changes what a policy
    knows, and that difference is structural rather than a matter of strictness: cfe stops at the first
    error and truncates its list there, clang reports every independent blocker in one pass. Eight modules
    in `solver/` gate on clang's exact wording and have been starved on this route because nothing
    produced it.

    NOT APPLICABLE WITHOUT A RECIPE TARGET, and NOT APPLICABLE WITHOUT THE TEXT CONVERTER: the checker
    must read the same bytes the oracle compiles, and a checker pointed at a different file is worse than
    no checker. Both are named rather than silently skipped.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    target = context.get("target")
    if not target:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry target, which the checker recipe is built for"}
    from solver import frontend_diagnostics as frontend

    report = frontend.analyse(context["candidate"], repo=Path(context["repo"]), target=str(target),
                              timeout=int(params.get("timeout", 60)))
    if report["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "observation": report, "reason": report.get("reason") or "the frontend is unavailable"}
    # Six lines for a reader, every error for a classifier. `0` or a negative value means "no window".
    _requested = int(params.get("max_errors", 6))
    _window = len(report["errors"]) if _requested <= 0 else _requested
    return {"status": OK, "changed": False, "exact": False,
            "observation": {k: report[k] for k in
                            ("status", "passed", "error_count", "gates", "source_sha256")},
            # The first diagnostic, flattened, so the renderer shows WHY rather than only a count.
            "reason": "" if report["passed"] else (
                f"{report['error_count']} error(s); first: "
                f"{report['errors'][0]['line']}:{report['errors'][0]['column']} "
                f"{report['errors'][0]['what'][:70]}" if report["errors"]
                else "the checker rejected the candidate without an error line"),
            # THE WINDOW IS THE CALLER'S CHOICE, AND THE TRUE COUNT TRAVELS WITH IT. `errors[:6]` was silent,
            # so anything building a defect-class set from `detail["errors"]` -- `intake_probe.
            # _diagnostic_chain` did -- was classing a six-error window and reporting it as the state's
            # residue. The project's own audit named this: a diagnostic class count is an OBSERVATION, not
            # remaining repair distance.
            #
            # `max_errors` makes the window explicit rather than fixed, because the two consumers want
            # different things: a rendered receipt wants six lines, and a fault-class histogram wants every
            # error there is. A caller that asks for all of them gets all of them and labels its own storage.
            "detail": {"stage": "clang-frontend", "gates": report["gates"],
                       "errors": report["errors"][:_window],
                       "error_count": report["error_count"],
                       "errors_shown": min(_window, len(report["errors"])),
                       "errors_window": _window,
                       "errors_truncated": report["error_count"] > _window
                       or bool(report.get("errors_truncated")),
                       "diagnostics_truncated": bool(report.get("diagnostics_truncated"))}}


def source_type_declarations(context: dict, params: dict) -> dict:
    """Recover a struct body from the function's own `src/` translation unit, gated on the binary.

    THE CLASS THIS OWNS. `incomplete definition of type 'X'` (clang) and `'member' undefined` (cfe) are the
    same fact: the type has no visible members, so `arg0->member` cannot resolve. Measured on the wide frame
    this is the whole distance for 13 states and present in more, and `header_variant` cannot reach it
    because the definition lives in the function's own `.c`, not in `include/**`.

    WHY IT IS NOT CONTAMINATION. `src/**` is the reference decomp, and the rule exists because a copied
    BODY answers the question. This copies declarations only -- `solver.source_type_declarations` refuses
    any block containing a function body -- and every member the draft uses must be annotated with an offset
    the target assembly actually touches through that parameter. `include/**` is admitted on the same
    basis and matches leaning on it are tiered `header-assisted`, never `SOLVED`.

    Every decline names its reason, and the receipt separates members the BINARY corroborates from members
    carried on the PROJECT's authority, so the two kinds of support stay distinguishable.
    """
    missing = _missing(context, "candidate", "repo", "target")
    if missing:
        return missing
    from solver import compile_obligations, source_type_declarations, workspace

    asm = _asm(context)
    if asm is None:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry a readable target_asm_path"}

    # The binary's offsets per parameter, which is the corroboration evidence.
    analysis, _rows = compile_obligations.analyse(asm)
    offsets: dict[str, set[int]] = {}
    for access in analysis.accesses.values():
        address = access.address
        if address and address.kind == "address" and str(address.name).startswith("param"):
            offsets.setdefault(str(address.name), set()).add(address.offset)

    block, report = source_type_declarations.recover(
        context["candidate"], function=context["function"], repo=_repo(context),
        target=str(context["target"]), assembly=asm, binary_offsets=offsets)
    if not block:
        return {"status": NO_CHANGE, "changed": False, "exact": False,
                "source": context["candidate"],
                "reason": (report["declined"][0] if report["declined"]
                           else "no declaration could be recovered from this function's translation unit"),
                "detail": report}
    source = context["candidate"]
    includes = list(re.finditer(r"(?m)^\s*#\s*include\s*[<\"][^>\"]+[>\"]", source))
    at = includes[-1].end() if includes else 0
    changed_source = source[:at] + block + source[at:]
    return {"status": OK, "changed": True, "exact": False, "source": changed_source,
            "entry": "source-type-declarations", "reason": "", "detail": report}


def undeclared_identifiers_runner(context: dict, params: dict) -> dict:
    """Declare the identifiers the compiler itself reports as undefined, via
    `solver.undeclared_identifiers.propose`.

    THE LARGEST WHOLE-DISTANCE CLASS. On the wide frame, `undeclared-identifier` is the whole distance for
    30 of 180 blocked states -- the largest single class -- and this module is the owner, documented as
    such, tested, and NOT in the sequence until now. Measured before wiring: it fires on 13 of those 30 and
    compiles 7 (`acquireSoundEffectHandleNode` 86.7, `drawRaceSetupPlayerCountPrompt` 91.7,
    `drawControllerPakDeleteConfirmPrompt` 95.3).

    THE DIAGNOSTICS COME FROM THE CURRENT VERDICT, not from the episode's first one. The probe passes the
    run's freshest stderr as `initial_verdict['stderr']`; a runner that read a stale one would be asking
    about names that an earlier step already declared, which is the mid-pipeline read this whole round is
    about.
    """
    missing = _missing(context, "candidate", "function")
    if missing:
        return missing
    from solver import undeclared_identifiers

    # THE SAME STARVATION `globals_variant` WAS FIXED FOR, AND THIS OWNER NEVER WAS.
    #
    # The names come out of the diagnostic text, and this read cfe's alone -- which stops at the FIRST
    # error. So on any candidate whose first blocker is something else, the `'gX' undefined` line is not
    # in the text, `reported` is empty, and the action returns "the current diagnostics name no undefined
    # identifier": an action with nothing to do, from the owner of the frame's largest class.
    #
    # Measured on the frozen frame: `undeclared-identifier` is 94 states and the ONLY remaining class in
    # 10, and all three of `drawRaceSetupPlayerCountPrompt`, `drawControllerPakDeleteConfirmPrompt` and
    # `waitCourseSelectRecordsClose` declined with that sentence. This module's own docstring records it
    # compiling the first two at 91.7 and 95.3 when it was handed their names, so it is not incapable
    # here -- it was never told what to declare.
    #
    # `undefined_names` is variadic and its pattern ALREADY matches both wordings (`'X' undefined` from
    # cfe, `undeclared identifier 'X'` from clang), so merging the two texts needs no new parsing. cfe's
    # is still read, for the cases where the checker cannot run at all.
    diagnostics = (context.get("initial_verdict") or {}).get("stderr") or ""
    frontend_reason, frontend_count, frontend_allowed = None, 0, []
    repo, target = context.get("repo"), context.get("target")
    if repo and target:
        try:
            from solver import frontend_diagnostics as frontend
            report = frontend.analyse(context["candidate"], repo=Path(repo), target=str(target),
                                      timeout=int(params.get("frontend_timeout", 60)))
            if report["status"] == "unavailable":
                frontend_reason = report.get("reason")
            else:
                text = report.get("diagnostics") or ""
                # MERGED AGAIN, THROUGH A POSITIVE WHITELIST. The first attempt at this merged clang's
                # names RAW and broke the ratchet: firing went ~13 -> 159 of 200 and the frame went
                # 48 -> 45 IDO / 30 -> 24 frontend, 4 gained against 7 LOST.
                #
                # WHY, read off the lost states' own receipts. cfe stops at the first error, which was
                # accidentally acting as a filter; clang names every blocker, and this action declares
                # whatever it is handed. What it was handed was mostly NOT data:
                #
                #   alSynSetPan        ALFilter, temp_a0, ALParam, temp_v0, bitwise
                #   __osViSwapContext  OSViMode, temp_s0, __OSViContext, temp_s1, __osViNext, __osViCurr
                #   __osDequeueThread  OSThread, var_a2, var_a3
                #
                # Type names in type position, m2c's own SSA temporaries, and `bitwise` -- the dialect
                # spelling `m2c_dialect` LOWERS, which is how declaring it cost `alSynSetPan`, a state
                # this loop had already won. A name is not a datum.
                #
                # So the gate is a POSITIVE whitelist of shapes that ARE data, not a blacklist of the
                # breakage seen so far: m2c's own naming for a translated global (`gFoo`/`sFoo`) and the
                # address-named form (`D_801121E0`), whose name IS its address and which the relocation
                # itself carries. Both patterns are `eval/name_triage`'s, reused rather than restated.
                # Everything else is counted and not declared.
                from eval import name_triage

                allowed = [n for n in undeclared_identifiers.undefined_names(text)
                           if name_triage.GLOBAL_NAMED.match(n)
                           or name_triage.ADDRESS_NAMED.match(n)]
                frontend_count = len(undeclared_identifiers.undefined_names(text))
                frontend_allowed = list(allowed)
                if allowed:
                    # `propose` reads the text only to extract names, so handing it the allowed names in
                    # cfe's own wording is the whole plumbing -- no new parameter, no second parser.
                    synthetic = "".join(f"'{n}' undefined\n" for n in allowed)
                    diagnostics = f"{diagnostics}\n{synthetic}" if diagnostics else synthetic
        except Exception as exc:                                # noqa: BLE001
            frontend_reason = f"{type(exc).__name__}: {exc}"
    else:
        frontend_reason = "the context carries no repo/target, so only cfe's text is available"

    reported = undeclared_identifiers.undefined_names(diagnostics)
    if not reported:
        return {"status": NO_CHANGE, "changed": False, "exact": False,
                "source": context["candidate"],
                "reason": "the current diagnostics name no undefined identifier",
                "detail": {"stage": "undeclared-identifier-declarations",
                           "frontend_names": frontend_count,
                           "frontend_allowed": frontend_allowed,
                           "frontend_unavailable": frontend_reason}}
    variants, report = undeclared_identifiers.propose(context["candidate"], context["function"],
                                                      diagnostics)
    if not variants:
        reason = (report.get("reason") or report.get("status")
                  or (report.get("declines") or report.get("declined") or ["declined"])[0])
        return {"status": NO_CHANGE, "changed": False, "exact": False,
                "source": context["candidate"], "reason": str(reason)[:200],
                "detail": {"stage": "undeclared-identifier-declarations", "reported": reported[:12],
                           "report": {k: v for k, v in report.items() if k != "source"}}}
    label, source = variants[0]
    return {"status": OK if source != context["candidate"] else NO_CHANGE,
            "changed": source != context["candidate"], "exact": False, "source": source,
            "entry": label, "reason": "",
            "detail": {"stage": "undeclared-identifier-declarations", "reported": reported[:12],
                       "frontend_names": frontend_count, "frontend_allowed": frontend_allowed,
                       "frontend_unavailable": frontend_reason,
                       "report": {k: v for k, v in report.items() if k != "source"}}}


def or_address(context: dict, params: dict) -> dict:
    """`*(ptr | n)` -> `*(s32 *)((u32)ptr | n)`, via `solver.m2c_or_address.rewrite`.

    THE OPERATOR IS RIGHT AND THE OPERAND IS WRONG. `nonmatchings/osEPiRawReadIo/target.s` does
    `or $t1,$t0,$a1` / `or $t2,$t1,$at` / `lw $t3,%lo(D_A0000000)($t2)` -- an integer OR whose result is used
    as an address -- and the reference C says the same: `IO_READ(pihandle->baseAddress | devAddr)`. What
    differs is that `baseAddress` is a `u32` there, so `u32 | u32` is an integer address, while m2c declares
    the member as a POINTER and dereferences the OR -- and `pointer | int` has no operator in C.

    The cast goes where the reference puts it: operands integer, one conversion at the load. No offset is
    invented, no operator changed, and the object comparison still decides.

    ONLY FIRES ON A DEREFERENCE WHOSE OPERAND CONTAINS A `|`. A bitmask is not an address and is untouched.
    """
    missing = _missing(context, "candidate")
    if missing:
        return missing
    from solver import m2c_or_address

    source, changes = m2c_or_address.rewrite(context["candidate"])
    changed = source != context["candidate"]
    return {"status": OK if changed else NO_CHANGE, "changed": changed, "exact": False,
            "source": source, "entry": "or-chain-address",
            "reason": "" if changed else "no dereference has a `|` in its operand",
            "detail": {"stage": "or-chain-address", "changes": changes[:6], "count": len(changes)}}


def header_variant(context: dict, params: dict) -> dict:
    """Header context for a TU that does not compile. THE `?`-prototype class's real owner."""
    missing = _missing(context, "candidate", "repo", "target")
    if missing:
        return missing
    asm = _asm(context)
    if asm is None:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry a readable target_asm_path"}
    from solver import compile_recovery

    source, report = compile_recovery.header_variant(_repo(context), context["function"], asm,
                                                     context["candidate"], context["target"])
    return _result(source, context["candidate"], report, stage="header-context-recovery")


def globals_variant(context: dict, params: dict) -> dict:
    """Extern hypotheses for undeclared identifiers, from symbol addresses + observed accesses.

    Needs a KB connection; the caller supplies one as `kb_conn` (the loop's context does not open
    databases by design). Without it this declines by name rather than guessing a width.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    conn = context.get("kb_conn")
    if conn is None:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry kb_conn, which globals_variant needs to read "
                          "cited access observations"}
    from solver import compile_recovery

    diagnostics = (context.get("initial_verdict") or {}).get("stderr") or ""
    # CFE STOPS AT THE FIRST ERROR, SO `wanted` WAS ALMOST ALWAYS EMPTY. The names this action declares
    # come out of that diagnostic text, and cfe truncates its list at the first error -- so on a candidate
    # whose first error is a placeholder or a syntax error, the `undeclared identifier 'gRegionAllocPtr'`
    # line never appears and the action has nothing to declare. Measured on the frozen frame: 211 unresolved
    # names are exactly this action's business (`unresolved-global` in `name-triage.json`), the largest
    # bucket by far, and the action changed the source on 5 of 200 states.
    #
    # The frontend reports EVERY independent blocker in one pass, which is why this project built it. Its
    # names are merged in, and cfe's text is still read for the cases where the checker cannot run at all.
    frontend_names, frontend_reason = set(), None
    repo, target = context.get("repo"), context.get("target")
    if repo and target:
        try:
            from solver import frontend_diagnostics as frontend
            report = frontend.analyse(context["candidate"], repo=Path(repo), target=str(target),
                                     timeout=int(params.get("frontend_timeout", 60)))
            if report["status"] == "unavailable":
                frontend_reason = report.get("reason")
            else:
                for error in report["errors"]:
                    frontend_names.update(re.findall(r"undeclared identifier '([A-Za-z_]\w*)'",
                                                     error.get("what") or ""))
                    frontend_names.update(re.findall(r"unknown type name '([A-Za-z_]\w*)'",
                                                     error.get("what") or ""))
        except Exception as exc:                                # noqa: BLE001
            frontend_reason = f"{type(exc).__name__}: {exc}"
    if not diagnostics and not frontend_names:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "no compiler diagnostics are available to name the undeclared symbols"}
    source, report = compile_recovery.globals_variant(conn, _repo(context), context["function"],
                                                      context["candidate"], diagnostics,
                                                      extra_names=frontend_names)
    report = {**(report or {}), "frontend_names": len(frontend_names),
              "frontend_unavailable": frontend_reason}
    return _result(source, context["candidate"], report, stage="binary-global-declarations")


def opaque_variant(context: dict, params: dict) -> dict:
    """A padded candidate struct for parameters whose fields the target accesses.

    This is the owner of `Selector requires struct/union pointer as left hand side`, the dominant error
    after the placeholder itself is resolved.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    asm = _asm(context)
    if asm is None:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry a readable target_asm_path"}
    from solver import compile_obligations

    source, report = compile_obligations.opaque_variant(_repo(context), context["function"],
                                                        context["candidate"], asm)
    return _result(source, context["candidate"], report, stage="opaque-parameter-layout")


def rewrite_do_while(context: dict, params: dict) -> dict:
    """`do { } while(...)` -> the build helper's sanctioned form. Pure and cheap.

    The demand queue's two largest error clusters name the `do` token; this rewrite is the existing
    owner of that class, reused from intake/recovery rather than reinvented.
    """
    missing = _missing(context, "candidate")
    if missing:
        return missing
    from tools.score_repo_function import rewrite_do_while as rewrite

    source = context["candidate"]
    if "do" not in source:
        return {"status": NO_CHANGE, "changed": False, "exact": False, "source": source,
                # TOP-LEVEL reason, not only nested in `detail`. The probe reads the top level, so a
                # reason hidden one key down renders as an empty string -- the same invisible-detail
                # shape that hid `diffrepair`'s report earlier in this work.
                "reason": "the candidate contains no do-loop",
                "detail": {"reason": "the candidate contains no do-loop"}}
    try:
        out = rewrite(source, style=str(params.get("style", "for-break")))
    except Exception as exc:                                    # noqa: BLE001
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": f"rewrite_do_while declined: {type(exc).__name__}: {exc}"}
    return _result(out, source, {"stage": "do-while-rewrite"}, stage="do-while-rewrite")


# THE THREE SPELLINGS, AND ONLY ONE OF THEM IS A REWRITE. `eval/name_triage.M2C_DIALECT` groups
# `bitwise`, `unaligned` and `sp` because no header can ever supply them -- which is a statement about
# where the fix does NOT live. `eval/results/intake-20260921/LOOP-1.md` then read that as "a pure
# rewrite" and queued all 18 states behind one. Measured on the frame's OWN candidates
# (`nonmatchings/<function>/base.c`, the bytes `draft_sha256` pins), that holds for `bitwise` alone:
#
#   bitwise    3 states  `temp_v0->data.f = (bitwise f32) pan;` -- a reinterpretation at a point, and
#                        `solver/m2c_context.lower_bitcasts` is its existing owner. Measured: it fires
#                        on alSynSetPan (1 plan, clean), fires PARTIALLY on alSynSetVol, and declines
#                        on alSynStartVoice. So the bucket's own three are 1 clear, 1 partial, 1 no.
#   unaligned  9 states  `spC.unk0 = (s32) (unaligned s32) sp14->unk0;` -- NOT a spelling fault. The
#                        target for `__osContGetInitData` is `lwl $at, 0x0($t9)` / `lwr $at, 0x3($t9)`
#                        storing into `sp+0xC`: IDO emitted the unaligned PAIR because the type it
#                        copies from is not word-aligned. Deleting the marker compiles and emits a
#                        plain `lw` -- a different instruction, so a state that "converts" this way is
#                        further from exact, not closer. The fact is alignment, and it belongs to
#                        layout. This action must not touch it.
#   sp         6 states  `arg0->unk0 = (s32) sp->unk0;` -- m2c naming the stack frame it never
#                        declared. The repair is a declared frame local, not a substitution.
#
# So: own `bitwise`, and ABSTAIN ON THE OTHER TWO BY NAME. Abstaining loudly is the whole point --
# a silent no-op here is the exact shape that hid `globals_variant` for all of the previous round.
DIALECT_SPELLINGS = ("bitwise", "unaligned", "sp")
OWNED_SPELLINGS = ("bitwise",)


def _spellings_in(source: str) -> list[str]:
    return [name for name in DIALECT_SPELLINGS
            if re.search(rf"\b{re.escape(name)}\b", source)]


def m2c_dialect(context: dict, params: dict) -> dict:
    """m2c dialect spellings -> the build's C89, for the one spelling that has an owner.

    `lower_bitcasts` was reachable through `repair_context.normalize` and was never an ACTION, so a
    policy holding a candidate whose only blocker was `(bitwise f32) pan` had nothing it could choose.
    It is an action now.

    Whatever the pass leaves behind is REPORTED rather than dropped: a partial lowering still fails the
    front end, and `alSynSetVol` is the case that matters -- it lowers `(bitwise f32) volume` and keeps
    `(bitwise f32) _timeToSamples(synth, t)`, because the pass needs the operand's source type and a
    call has none until a prototype supplies one. That remainder is a COMPOSITION with the triage's
    `known-function` bucket, and it is only findable if the receipt shows it.
    """
    missing = _missing(context, "candidate", "function")
    if missing:
        return missing
    from solver import m2c_context

    source = context["candidate"]
    present = _spellings_in(source)
    if not present:
        reason = "the candidate carries no m2c dialect spelling"
        return {"status": NO_CHANGE, "changed": False, "exact": False, "source": source,
                "reason": reason, "detail": {"stage": "m2c-dialect", "reason": reason}}
    unowned = [name for name in present if name not in OWNED_SPELLINGS]
    if not any(name in OWNED_SPELLINGS for name in present):
        # A DECLINE THAT NAMES THE FACT IT IS NOT ALLOWED TO GUESS, so the state reads as "owned by
        # layout" rather than as "this action had nothing to do".
        reason = (f"{', '.join(unowned)} is an alignment/frame fact rather than a spelling; "
                  "no rewrite of this candidate can supply it")
        return {"status": NO_CHANGE, "changed": False, "exact": False, "source": source,
                "reason": reason,
                "detail": {"stage": "m2c-dialect", "declined_reason": reason,
                           "declined": unowned, "unresolved_names": present}}
    try:
        out, plans = m2c_context.lower_bitcasts(source, context["function"])
    except Exception as exc:                                    # noqa: BLE001
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": f"lower_bitcasts declined: {type(exc).__name__}: {exc}"}
    report = {"stage": "m2c-dialect", "plans": plans, "declined": unowned,
              "unresolved_names": _spellings_in(out)}
    if not plans:
        report["declined_reason"] = (
            "lower_bitcasts lowered no site in this candidate"
            + (f", and {', '.join(unowned)} has no rewrite" if unowned else ""))
    return _result(out, source, report, stage="m2c-dialect")


def implicit_externs(context: dict, params: dict) -> dict:
    """WITHDRAWN FROM THE REGISTRY AND THE SEQUENCE (LOOP-6): kept as the record of why.

    The project's clang policy rejects an unprototyped `extern int f();` ("a function declaration
    without a prototype is deprecated"), so this ADDED an error in every state it fired on -- 16 --
    while leaving acceptance flat, and rank ties adopted the worse candidates. There is no
    object-neutral declaration for a callee no header declares; a prototype needs parameter types,
    and those would be invented. `enqueueSoundEffect` and `drawMenuFillRectangle` stay unreachable
    by an invent-nothing route until a signature has evidence behind it.

    `extern int f();` for a real ROM function called only for effect, via `solver.implicit_extern`.

    AFTER `header_prototypes`, because a real prototype outranks the implicit one; this owns exactly the
    names that pass cannot, the ones NO header declares (`enqueueSoundEffect`, `drawMenuFillRectangle`).
    The object is unchanged by construction -- it writes out the declaration C89 already implies -- so
    this moves only the frontend gate. The ROM check needs the KB and declines by name without it.
    """
    missing = _missing(context, "candidate", "function", "repo")
    if missing:
        return missing
    conn = context.get("kb_conn")
    if conn is None:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry kb_conn, which the ROM-function check needs"}
    target = context.get("target")
    if not target:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry target, which the checker recipe is built for"}
    from solver import frontend_diagnostics as frontend, implicit_extern

    report = frontend.analyse(context["candidate"], repo=Path(context["repo"]), target=str(target),
                              timeout=int(params.get("timeout", 60)))
    if report["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": f"the called names come from the checker, and {report.get('reason') or ''}"}
    rom = {name for (name,) in conn.execute("select name from functions")}
    out, proposal = implicit_extern.propose(context["candidate"], context["function"],
                                            report.get("diagnostics") or "", rom,
                                            repo=Path(context["repo"]))
    entry = {"stage": "implicit-externs", "plans": proposal["declared"],
             "declined": [d.get("reason") for d in proposal["declines"]],
             "authority": proposal["authority"]}
    if not proposal["declared"]:
        entry["declined_reason"] = ((proposal["declines"] or [{}])[0].get("reason")
                                    or "the checker reports no implicitly declared function")
    return _result(out, context["candidate"], entry, stage="implicit-externs")


def widen_pointer_declarations(context: dict, params: dict) -> dict:
    """`extern s32 X;` -> `extern u16 X[];` when the checker says the use needs `u16 *`.

    THE WALL THE DECLARATION PASSES CREATED. `globals_variant` and `undeclared_identifiers` type an
    undeclared datum from access WIDTH, and a width is not a type -- so a datum used as an address is
    left asserting `s32` where the context needs a pointer. `incompatible-int-pointer` became the
    frame's largest sole blocker (65 states, 8 sole) precisely as those passes started converting
    states: each one hit this as its next wall.

    RUNS LAST among the declaration passes, because it edits what they emit. It only ever replaces an
    `extern` that this route itself wrote, and the replacement type is the CHECKER'S statement of what
    the use requires -- so it is inference with a citation, not a layout choice. A non-primitive pointee
    (`SoundHandleNode *`, `MenuGlyphScript *`) abstains by name: those structs exist only in a
    reconstructed `include/game/**` header, and adopting one would make the repair header-assisted.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    target = context.get("target")
    if not target:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry target, which the checker recipe is built for"}
    from solver import frontend_diagnostics as frontend, pointer_decl_widen

    report = frontend.analyse(context["candidate"], repo=Path(context["repo"]), target=str(target),
                              timeout=int(params.get("timeout", 60)))
    if report["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": f"the required pointee comes from the checker, and "
                          f"{report.get('reason') or 'it is unavailable'}"}
    diagnostics = report.get("diagnostics") or ""
    source, changes = pointer_decl_widen.rewrite(context["candidate"], diagnostics)
    applied = [c for c in changes if "after" in c]
    declined = [c for c in changes if "declined" in c]
    assisted = sorted({h for c in applied for h in (c.get("game_headers") or [])
                       if not c.get("pointee_is_primitive")})
    entry = {"stage": "widen-pointer-declarations", "plans": applied,
             "declined": [c["declined"] for c in declined],
             # The tier, stated where the gain is counted: a struct pointee adopted from a
             # reconstructed header already in scope is HEADER-ASSISTED, a width is not.
             "authority": ("HEADER-ASSISTED: struct pointee from " + ", ".join(assisted)
                           if assisted else "binary-only: primitive pointee from the checker")}
    if not applied:
        entry["declined_reason"] = (
            declined[0]["declined"] if declined
            else "the checker reports no integer-to-pointer conversion on a declaration we emitted")
    return _result(source, context["candidate"], entry, stage="widen-pointer-declarations")


def m2c_aligned_copy(context: dict, params: dict) -> dict:
    """`M2C_MEMCPY_ALIGNED(dst, src, n);` -> a bounded byte copy, via `solver.m2c_copy.propose`.

    A THIRD PASS THAT WORKS AND WAS NOT AN ACTION. `solver/m2c_copy.propose` is complete and guarded
    (three arguments, a literal size that is a non-zero multiple of 4 up to 4096, pure address
    expressions only, no name collisions) and is reachable from `solver/modelrepair.py:748`, which is
    the MODEL path. The deterministic intake route never called it, so `__osViSwapContext` sat with
    `implicit declaration of function 'M2C_MEMCPY_ALIGNED'` as its single remaining error.

    NOT A PROTOTYPE, and the distinction decides whether this moves toward exact or away from it.
    `M2C_MEMCPY_ALIGNED` is m2c's own spelling for a bulk copy, which CLAUDE.md already catalogues as
    what IDO emits for struct assignment. Declaring it `extern int M2C_MEMCPY_ALIGNED();` would compile
    and emit a `jal` to a function that does not exist in the ROM -- a state further from exact wearing
    a conversion. The same trap applies to `M2C_BREAK`, which is the MIPS `break` instruction and has
    no C89 spelling at all; nothing here touches it.
    """
    missing = _missing(context, "candidate", "function")
    if missing:
        return missing
    from solver import m2c_copy

    source = context["candidate"]
    if "M2C_MEMCPY_ALIGNED" not in source:
        reason = "the candidate has no M2C_MEMCPY_ALIGNED call"
        return {"status": NO_CHANGE, "changed": False, "exact": False, "source": source,
                "reason": reason, "detail": {"stage": "m2c-aligned-copy", "reason": reason}}
    try:
        # `propose` returns ONE dict carrying the rewritten source, not a (source, report) pair like
        # the other passes in this registry. Unpacking it as a pair silently yields its KEYS.
        proposal = m2c_copy.propose(source, context["function"])
    except Exception as exc:                                    # noqa: BLE001
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": f"m2c_copy declined: {type(exc).__name__}: {exc}"}
    out = proposal.get("source") or source
    report = {"stage": "m2c-aligned-copy", "plans": proposal.get("changes") or []}
    if out == source:
        report["declined_reason"] = ("m2c_copy found no lowerable call: it requires three arguments, a "
                                     "literal size that is a multiple of 4, and pure address operands")
    return _result(out, source, report, stage="m2c-aligned-copy")


def header_prototypes(context: dict, params: dict) -> dict:
    """An extern prototype for a called function, via `compile_recovery.scalar_header_prototypes`.

    ANOTHER WORKING PASS WITH NO ACTION. It is composed in `solver/modelrepair.py:772` and absent from
    the intake sequence, so three states sat with `implicit declaration of function` as their only
    remaining class: `updateRaceUiResultsBannerWaitForInput` (`enqueueSoundEffect`),
    `drawMainMenuModeSelectIcons` (`drawMenuFillRectangle`) and `updateCharacterSelectMenu`.

    ITS OWN GUARD IS THE POINT: it takes a prototype only when the project's headers give exactly ONE
    signature for the name and every type in it is a primitive scalar. Anything ambiguous, absent or
    structured declines, so no signature is ever invented here.

    ASSISTANCE TIER. The prototypes come from `repo/include/**`. A declaration found under
    `include/game/**` is a RECONSTRUCTED game header -- the decomp team's own answer, parameter types
    included -- and any match reached through it is header-assisted rather than binary-only. The pass
    records the include path and its digest per prototype; `assistance` below lifts that into the
    receipt so the tier is visible where the gain is counted, instead of being reconstructed later.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    target = context.get("target")
    if not target:
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": "the context does not carry target, which the checker recipe is built for"}
    from solver import compile_recovery, frontend_diagnostics as frontend

    report = frontend.analyse(context["candidate"], repo=Path(context["repo"]), target=str(target),
                              timeout=int(params.get("timeout", 60)))
    if report["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": f"the called names come from the checker, and {report.get('reason') or ''}"}
    diagnostics = report.get("diagnostics") or ""
    if "implicit declaration of function" not in diagnostics:
        reason = "the checker reports no implicitly declared function"
        return {"status": NO_CHANGE, "changed": False, "exact": False,
                "source": context["candidate"], "reason": reason,
                "detail": {"stage": "header-prototypes", "reason": reason}}
    try:
        out, proto = compile_recovery.scalar_header_prototypes(
            Path(context["repo"]), context["candidate"], diagnostics)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": FAILED, "changed": False, "exact": False,
                "reason": f"scalar_header_prototypes declined: {type(exc).__name__}: {exc}"}
    taken = proto.get("prototypes") or []
    includes = sorted({h.get("include", "") for p in taken for h in (p.get("headers") or [])})
    entry = {"stage": "header-prototypes", "plans": taken,
             "declined": [d.get("reason") for d in (proto.get("declines") or [])],
             # The tier, stated where the gain is, not inferred afterwards.
             "authority": ("reconstructed include/game header: HEADER-ASSISTED, not binary-only"
                           if any(i.startswith("game/") for i in includes)
                           else "public SDK/project header projection")}
    if not taken:
        entry["declined_reason"] = (
            (proto.get("declines") or [{}])[0].get("reason")
            or "no implicitly declared name had one primitive-scalar header signature")
    return _result(out, context["candidate"], entry, stage="header-prototypes")


def scalar_member_index(context: dict, params: dict) -> dict:
    """`p->unkN` on a pointer to a scalar -> `p[N / sizeof(T)]`, via `solver.scalar_member_index`.

    THE LARGEST SOLE BLOCKER ON THE FRAME, and it only became visible once the checker stopped being
    capped at 20 errors (`eval/results/intake-20260921/ERROR-LIMIT.md`). `member-on-typed-pointer` is
    the ONLY remaining class in 11 states and appears in 74; under the ceiling it read as a depth-1
    curiosity cleared in 3.

    THE DIAGNOSTIC IS THE INPUT, and it has to be clang's rather than cfe's for the same reason
    `globals_variant` needed it: cfe stops at the first error, so on a candidate whose first blocker is
    something else the member-reference lines are simply not in the text and this action would report
    "produced no change" -- an action with nothing to do, which is the shape this project keeps paying
    for. clang reports every independent blocker in one pass AND names the base type, which is the
    fact the rewrite is derived from.
    """
    missing = _missing(context, "candidate", "repo")
    if missing:
        return missing
    from solver import frontend_diagnostics as frontend, scalar_member_index as rewriter

    target = context.get("target")
    diagnostics = ""
    unavailable = None
    if target:
        report = frontend.analyse(context["candidate"], repo=Path(context["repo"]),
                                  target=str(target), timeout=int(params.get("timeout", 60)))
        if report["status"] == "unavailable":
            unavailable = report.get("reason") or "the frontend is unavailable"
        else:
            diagnostics = report.get("diagnostics") or ""
    else:
        unavailable = "the context does not carry target, which the checker recipe is built for"
    if unavailable:
        # NAMED, not skipped: without the checker there is no base type, and guessing one is the
        # failure mode this module exists to avoid.
        return {"status": NOT_APPLICABLE, "changed": False, "exact": False,
                "reason": f"the base type comes from the checker, and {unavailable}"}

    source, changes = rewriter.rewrite(context["candidate"], diagnostics)
    applied = [c for c in changes if "after" in c]
    declined = [c for c in changes if "declined" in c]
    report = {"stage": "scalar-member-index", "plans": applied,
              "declined": [c["declined"] for c in declined]}
    if not applied:
        report["declined_reason"] = (
            declined[0]["declined"] if declined
            else "the candidate has no scalar-based `unkN` access the checker named")
    return _result(source, context["candidate"], report, stage="scalar-member-index")


def void_members(context: dict, params: dict) -> dict:
    """Reuse target-width void-member hypotheses with fresh, unwindowed diagnostics."""
    missing = _missing(context, "candidate", "function", "repo", "target", "target_asm_path")
    if missing:
        return missing
    assembly = _asm(context)
    if assembly is None:
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "the context does not carry a readable target_asm_path"}
    from solver import frontend_diagnostics as frontend, void_field_repair
    diagnostic = frontend.analyse(context["candidate"], repo=_repo(context),
        target=str(context["target"]), timeout=int(params.get("timeout", 60)), full_diagnostics=True)
    if diagnostic["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": diagnostic.get("reason") or "the frontend is unavailable"}
    proposal = void_field_repair.propose(context["candidate"], context["function"],
                                        assembly, diagnostic.get("diagnostics") or "")
    report = {"stage": "void-field-byte-view", "plans": proposal["changes"],
              "authority": proposal["scope"]}
    if not proposal["changes"]:
        report["declined_reason"] = "no diagnostic-bound void member has unambiguous target access evidence"
    return _result(proposal["source"], context["candidate"], report, stage="void-field-byte-view")


def global_scalars(context: dict, params: dict) -> dict:
    """Expose witnessed scalar reads of header-declared aggregate globals."""
    return _type_view(context, params, signature=False)


def header_signature(context: dict, params: dict) -> dict:
    """Lock the public definition to its header while retaining local body views."""
    return _type_view(context, params, signature=True)


def global_fields(context: dict, params: dict) -> dict:
    """Address/offset/width views of diagnosed named-global pseudo-fields."""
    return _type_view(context, params, owner="global_fields")


def stack_arrays(context: dict, params: dict) -> dict:
    """Indexed stack-word storage and overlapping scalar aliases."""
    return _type_view(context, params, owner="stack_arrays")


def _type_view(context: dict, params: dict, *, signature: bool = False, owner: str = "") -> dict:
    missing = _missing(context, "candidate", "function", "repo", "target", "workspace", "target_asm_path")
    if missing:
        return missing
    assembly = _asm(context)
    obj = Path(context["workspace"]) / "target.o"
    from solver import frontend_diagnostics as frontend, frontend_repair
    if assembly is None or not obj.is_file() or not frontend_repair.big_endian_o32(obj):
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "type views require readable assembly and a big-endian o32 target object"}
    diagnostic = frontend.analyse(context["candidate"], repo=_repo(context), target=str(context["target"]),
        timeout=int(params.get("timeout", 60)), full_diagnostics=True)
    if diagnostic["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": diagnostic.get("reason") or "the frontend is unavailable"}
    from solver import global_scalar_view, header_signature_view
    if signature:
        proposal = header_signature_view.propose(_repo(context), context["candidate"], context["function"],
            diagnostic.get("diagnostics") or "", big_endian_o32=True)
    elif owner:
        from solver import global_field_view, stack_scalar_arrays
        generator = {"global_fields": global_field_view, "stack_arrays": stack_scalar_arrays}[owner]
        proposal = generator.propose(_repo(context), context["candidate"], context["function"],
            diagnostic.get("diagnostics") or "", assembly)
    else:
        proposal = global_scalar_view.propose(_repo(context), context["candidate"], context["function"],
            diagnostic.get("diagnostics") or "", assembly)
    stage = "header-signature-view" if signature else "global-scalar-view"
    if owner: stage = {"global_fields": "global-field-view", "stack_arrays": "stack-scalar-arrays"}[owner]
    plans = [dict(edits=proposal["changes"], header_declarations=proposal.get("header_declarations", []),
                  aliases=proposal.get("aliases", []))] if signature and proposal["changes"] else proposal["changes"]
    report = dict(stage=stage, plans=plans, declined=proposal["declines"], authority=proposal["scope"])
    if not proposal["changes"]:
        report["declined_reason"] = "no supported diagnostic-bound " + stage
    return _result(proposal["source"], context["candidate"], report, stage=stage)


def frontend_abi(context: dict, params: dict) -> dict:
    """Expose existing diagnostic-bound ABI/representation proposals to intake."""
    return _frontend_representation(context, params, projection=False)


def call_arity(context: dict, params: dict) -> dict:
    """Project excess call words onto an unambiguous header contract."""
    return _frontend_representation(context, params, projection=True)


def _frontend_representation(context: dict, params: dict, *, projection: bool) -> dict:
    missing = _missing(context, "candidate", "function", "repo", "target", "workspace", "target_asm_path")
    if missing:
        return missing
    assembly = _asm(context)
    obj = Path(context["workspace"]) / "target.o"
    if assembly is None or not obj.is_file():
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "ABI repair requires readable target assembly and target.o"}
    from solver import frontend_diagnostics as frontend, frontend_repair
    diagnostic = frontend.analyse(context["candidate"], repo=_repo(context),
        target=str(context["target"]), timeout=int(params.get("timeout", 60)), full_diagnostics=True)
    if diagnostic["status"] == "unavailable":
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": diagnostic.get("reason") or "the frontend is unavailable"}
    if projection:
        from solver import call_arity_repair
        proposal = call_arity_repair.propose(_repo(context), context["candidate"], context["function"],
            diagnostic.get("diagnostics") or "", assembly, big_endian_o32=frontend_repair.big_endian_o32(obj))
    else:
        proposal = frontend_repair.propose(_repo(context), context["candidate"], context["function"],
            diagnostic.get("diagnostics") or "", big_endian_o32=frontend_repair.big_endian_o32(obj),
            target_assembly=assembly)
    stage = "call-arity" if projection else "frontend-abi"
    report = {"stage": stage, "plans": proposal["changes"],
              "declined": proposal["declines"], "authority": proposal["scope"]}
    if not proposal["changes"]:
        report["declined_reason"] = "no supported diagnostic-bound ABI or representation repair"
    return _result(proposal["source"], context["candidate"], report, stage=stage)


def frontend_casts(context: dict, params: dict) -> dict:
    """Reuse the bounded frontend cast fixer; the caller still compiles/logs its child."""
    missing = _missing(context, "candidate", "repo", "target", "workspace")
    if missing:
        return missing
    from solver import frontend_diagnostics as frontend, frontend_fixits
    import subprocess
    try:
        recipe = frontend.recipe(_repo(context), str(context["target"]))
        options = {"allow_partial": True} if params.get("allow_partial") else {}
        source, trace = frontend_fixits.propose(_repo(context), context["candidate"],
            recipe["command"], Path(context["workspace"]), rounds=4, **options)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": f"frontend cast repair unavailable: {type(exc).__name__}: {exc}"}
    report = {"stage": "frontend-casts", "plans": trace,
              "authority": "diagnosed expression casts; compiler and object verifier adjudicate"}
    if source is None:
        report["declined_reason"] = ("no measured frontend improvement within four cast rounds"
            if params.get("allow_partial") else "no changed candidate passed the frontend within four cast rounds")
    return _result(source or context["candidate"], context["candidate"], report, stage="frontend-casts")


def ido_byte_cursors(context: dict, params: dict) -> dict:
    """Lower byte cursors after clang passes but IDO still rejects the candidate."""
    missing = _missing(context, "candidate", "function", "repo", "target", "initial_verdict")
    if missing:
        return missing
    if context["initial_verdict"].get("compiled") is not False:
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "byte-cursor recovery requires a failed IDO compile"}
    from solver import frontend_diagnostics as frontend, void_pointer_units
    diagnostic = frontend.analyse(context["candidate"], repo=_repo(context),
        target=str(context["target"]), timeout=int(params.get("timeout", 60)))
    if diagnostic["status"] != "passed":
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "byte-cursor recovery requires a passing frontend check"}
    source, changes = void_pointer_units.propose(context["candidate"], context["function"])
    # This adapter owns byte arithmetic and compatible pointer comparisons only.
    # Pseudo-fields require the separate diagnostic/assembly-bound member repair.
    if changes["fields"]:
        return {"status": NOT_APPLICABLE, "changed": False,
                "reason": "byte-cursor recovery refuses inferred pseudo-field widths"}
    report = {"stage": "ido-byte-cursors", "plans": changes,
              "authority": "byte-unit cursor hypothesis; IDO and object verifier adjudicate"}
    return _result(source, context["candidate"], report, stage="ido-byte-cursors")


RUNNERS = {
    "eval.intake_runners.resolve_placeholders": resolve_placeholders,
    "eval.intake_runners.frontend_diagnostics": frontend_diagnostics,
    "eval.intake_runners.source_type_declarations": source_type_declarations,
    "eval.intake_runners.undeclared_identifiers": undeclared_identifiers_runner,
    "eval.intake_runners.or_address": or_address,
    "eval.intake_runners.negative_offset": negative_offset,
    "eval.intake_runners.header_variant": header_variant,
    "eval.intake_runners.globals_variant": globals_variant,
    "eval.intake_runners.opaque_variant": opaque_variant,
    "eval.intake_runners.rewrite_do_while": rewrite_do_while,
    "eval.intake_runners.m2c_dialect": m2c_dialect,
    "eval.intake_runners.scalar_member_index": scalar_member_index,
    "eval.intake_runners.m2c_aligned_copy": m2c_aligned_copy,
    "eval.intake_runners.header_prototypes": header_prototypes,
    "eval.intake_runners.widen_pointer_declarations": widen_pointer_declarations,
    "eval.intake_runners.void_members": void_members,
    "eval.intake_runners.frontend_casts": frontend_casts,
    "eval.intake_runners.frontend_abi": frontend_abi,
    "eval.intake_runners.call_arity": call_arity,
    "eval.intake_runners.global_scalars": global_scalars,
    "eval.intake_runners.global_fields": global_fields,
    "eval.intake_runners.stack_arrays": stack_arrays,
    "eval.intake_runners.header_signature": header_signature,
    "eval.intake_runners.ido_byte_cursors": ido_byte_cursors,
}


def register(registry: dict) -> list[str]:
    """Attach these runners to the action table the loop resolves through."""
    registry.update(RUNNERS)
    return sorted(RUNNERS)
