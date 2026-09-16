"""Chain compile fixes inside one work item, so a non-compiling function is not stuck at its first error.

Measured 2026-09-15 on the 78 pending non-compiling functions: placeholder recovery fixed the first
error of all 9 it applied to, but only 2 then compiled; the rest failed on a second error. A child
that still does not compile scores 0 like its parent, so it was never kept, and every later profile
restarted at the same first error (updateRacePlayerSurfaceContact: line 21 `extern M2C_UNK`, then
undeclared `sp130`).

`chain(function, label, candidate, attempt, score)` repeatedly applies the next available fix to
the best child so far and re-scores it through the caller's `score(label, code) -> Attempt`:

    0. the C89/target-linkage rewrite (`solver.c89.to_c89` + `public_definition`) -- see `next_fixes`
    1. the project helper's do-while refusal (the campaign's existing for/break lowering)
    2. m2c placeholders (solver.placeholder_declarations)
    3. identifiers IDO reports as undefined (solver.undeclared_identifiers)

It continues only while `progress` strictly improves (compiled, score, reached IDO at all, later first
IDO error, fewer IDO errors) and stops at a compiling child, when no fix applies, or when `budget` compiles are spent.
Exactness and acceptance stay with the caller's oracle: this only proposes and orders sources.
"""
from __future__ import annotations

import re

from solver import placeholder_declarations, undeclared_identifiers

IDO_ERROR = re.compile(r"(?:cfe|as\d?|ugen|uopt): Error: [^,\n]*, line (\d+):")
DO_WHILE_POLICY = "The C file contains a do-while loop"


def first_error(attempt) -> tuple[int, str] | None:
    stderr = getattr(attempt, "compiler_stderr", "") or ""
    found = [(int(m.group(1)), stderr[m.end():stderr.find("\n", m.end()) % (len(stderr) + 1)].strip())
             for m in IDO_ERROR.finditer(stderr)]
    return min(found) if found else None


def progress(attempt, code: str | None = None, base_lines: int | None = None) -> tuple:
    """Higher is better: compiled, score, first IDO error later (in the base source's line numbers).

    The first error's position leads: IDO stops early at a syntax error, so fixing line 21 can reveal
    four later `'sp130' undefined` reports (updateRacePlayerSurfaceContact). The fixers only insert
    declarations above the function, so a child's line numbers are shifted back by the lines it
    gained; otherwise every inserted declaration would look like progress even when it resolves
    nothing (__osContGetInitData re-declared `data` six times before this correction).
    """
    shift = (code.count("\n") - base_lines) if code is not None and base_lines is not None else 0
    error = first_error(attempt)
    stderr = getattr(attempt, "compiler_stderr", "") or ""
    # A source the project build helper refuses (do-while policy) never reached IDO: it has no error
    # line, which must not read as "no errors" (updateRacePlayerSurfaceContact in the campaign path).
    reached_ido = not helper_blocked(attempt)
    return (bool(getattr(attempt, "compiled", False)), getattr(attempt, "score", 0.0) or 0.0, reached_ido,
            (error[0] - shift) if error else 10**9, -len(IDO_ERROR.findall(stderr)))


def helper_blocked(attempt) -> bool:
    return DO_WHILE_POLICY in (getattr(attempt, "compiler_stderr", "") or "")


def advanced(parent, parent_code: str, child, child_code: str, base_lines: int) -> bool:
    """A child advanced when it compiles, scores higher, or its first error moved later or changed."""
    before, after = progress(parent, parent_code, base_lines), progress(child, child_code, base_lines)
    if after[:4] > before[:4]:
        return True
    if after[:4] < before[:4]:
        return False
    old, new = first_error(parent), first_error(child)
    return bool(old and new and old[1] != new[1])            # same position, a different error


def next_fixes(candidate: str, function: str, attempt, headers: str = "") -> tuple[list[tuple[str, str]], dict]:
    report: dict = {}
    # FIRST, and before the IDO-error-driven rungs, because this class of failure gives IDO nothing
    # useful to report: `static inline void f()` is a Syntax Error at the opening brace, three errors
    # deep and pointing at the wrong line. Rewriting the text is deterministic and free, and it fixes
    # two failure classes that were being counted separately in the admission bucket:
    #
    #   `static inline void *f(void)` -> cfe: Syntax Error            (inline is C99)
    #   `static void *f(void)`         -> "no text symbols"           (IDO drops an unreferenced static)
    #
    # Measured 2026-09-16 on receipts 31124 / 31125: raw Syntax Error, `to_c89` alone still "no text
    # symbols", `to_c89` + `public_definition` compiles at 75.833 / 85.455.
    from solver import c89
    normalized = c89.public_definition(c89.to_c89(candidate), function)
    if normalized != candidate:
        report["c89"] = {"applied": True}
        return [("c89:normalize_target", normalized)], report
    if helper_blocked(attempt):
        # The project helper refuses do-while loops before IDO runs: reuse the campaign's sanctioned
        # for/break lowering, which itself declines unsafe `continue` semantics.
        try:
            from tools.score_repo_function import rewrite_do_while
            lowered = rewrite_do_while(candidate)
        except (ImportError, ValueError) as exc:
            report["do_while"] = {"declined": str(exc)}
        else:
            if lowered != candidate:
                return [("do_while:for_break", lowered)], report
    hoisted = hoist_for_declarations(candidate, function)
    if hoisted != candidate:
        return [("c89:for_declarations", hoisted)], report
    if placeholder_declarations.signals(candidate):
        rows, placeholder_report = placeholder_declarations.propose(candidate, function, headers)
        report["placeholders"] = placeholder_report
        if rows:
            return rows, report
    diagnostics = (getattr(attempt, "compiler_stderr", "") or "") + "\n" + \
        ((getattr(attempt, "frontend", None) or {}).get("diagnostics", "") or "")
    rows, undeclared_report = undeclared_identifiers.propose(candidate, function, diagnostics)
    report["undeclared"] = undeclared_report
    return rows, report


FOR_DECLARATION = re.compile(r"\bfor[ \t]*\([ \t]*(?P<type>(?:(?:unsigned|signed|const)[ \t]+)*"
                             r"(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|short|char|long)(?:[ \t]*\*+)?)[ \t]*"
                             r"(?P<name>[A-Za-z_]\w*)[ \t]*=")


def hoist_for_declarations(source: str, function: str) -> str:
    """C99 `for (int i = 0; ...)` is a syntax error for IDO (C89): declare `i` first in the body instead.

    Semantics-preserving when the name is unique in the function (a second `for (int i ...)` or an
    existing `i` declaration declines). Motivating: __osCheckPackId, IDO `line 19: Syntax Error`.
    """
    from solver import repair_context
    try:
        match, end = repair_context.definition(source, function)
    except ValueError:
        return source
    body_start = source.index("{", match.start(), end) + 1
    body = source[body_start:end]
    found = list(FOR_DECLARATION.finditer(body))
    names = [m.group("name") for m in found]
    if not found or len(names) != len(set(names)):
        return source
    if any(re.search(rf"^[ \t]*[\w \t*]+\b{re.escape(n)}[ \t]*(?:\[[^\]]*\])?[ \t]*;", body, re.M) for n in names):
        return source
    for m in reversed(found):
        body = body[:m.start()] + f"for ({m.group('name')} =" + body[m.end():]
    declarations = "".join(f"    {m.group('type').strip()} {m.group('name')};\n" for m in found)
    return source[:body_start] + "\n" + declarations + body.lstrip("\n") + source[end:]


def stub_fixes(candidate: str, function: str, attempt) -> tuple[list[tuple[str, str]], dict]:
    """Last resort (solver.compile_stub): neutralize the construct at the first stubbable IDO error line."""
    from solver import compile_stub
    stderr = getattr(attempt, "compiler_stderr", "") or ""
    tried = []
    for line in sorted({int(n) for n in IDO_ERROR.findall(stderr)}):
        rows, report = compile_stub.propose(candidate, function, line)
        tried.append(report)
        if rows:
            return rows, {"stub": tried}
    return [], {"stub": tried}


def chain(function: str, label: str, candidate: str, attempt, score, *, headers: str = "",
          rounds: int = 6, budget: int = 10, stub: bool = False,
          stub_rounds: int = 40) -> tuple[list[tuple[str, str, object]], list[dict]]:
    """[(label, code, attempt)] for every scored child, plus a per-round log.

    `stub=True` adds the last-resort compiling baseline (solver.compile_stub) when no real fix
    applies; rounds and budget then extend to `stub_rounds`, one compile per stubbed construct.
    """
    scored, log = [], []
    current = (label, candidate, attempt)
    seen = {candidate}
    base_lines = candidate.count("\n")
    limit = max(rounds, stub_rounds) if stub else rounds
    budget = max(budget, stub_rounds) if stub else budget
    for round_index in range(limit):
        if getattr(current[2], "compiled", False) or budget <= 0:
            break
        fixes, report = next_fixes(current[1], function, current[2], headers)
        entry = {"round": round_index, "parent": current[0], "fixes": [l for l, _ in fixes], "report": report}
        log.append(entry)

        def try_fixes(rows):
            nonlocal budget
            best = None
            for fix_label, code in rows[:2]:
                if code in seen or budget <= 0:
                    continue
                seen.add(code)
                child_label = f"{current[0]}+{fix_label}"
                child = score(child_label, code)
                budget -= 1
                scored.append((child_label, code, child))
                if best is None or progress(child, code, base_lines) > progress(best[2], best[1], base_lines):
                    best = (child_label, code, child)
            return best

        best = try_fixes(fixes)
        if (best is None or not advanced(current[2], current[1], best[2], best[1], base_lines)) and stub:
            # A real fix that leaves the first error in place (declaring slots while an m2c member access on a
            # u8 still fails, _Printf) must not end the chain: stub the construct at the first error instead.
            stub_rows, stub_report = stub_fixes(current[1], function, current[2])
            report.update(stub_report)
            entry["fixes"] += [l for l, _ in stub_rows]
            stubbed = try_fixes(stub_rows)
            if stubbed is not None and (best is None or progress(stubbed[2], stubbed[1], base_lines) >
                                        progress(best[2], best[1], base_lines)):
                best = stubbed
        entry["best"] = best[0] if best else None
        entry["progress"] = list(progress(best[2], best[1], base_lines)) if best else None
        if best is None or not advanced(current[2], current[1], best[2], best[1], base_lines):
            entry["stopped"] = "no progress" if best else "no fix applies"
            break
        current = best
    return scored, log
