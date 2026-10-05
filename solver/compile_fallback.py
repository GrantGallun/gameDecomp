"""Rescue model-written C that does not compile, cheapest first.

WHY
---
A compile error is a FRONTEND problem, orthogonal to whether the source's shape is right, and a
candidate must not be lost to it. On the redraft pilot 79 of 96 model redrafts never compiled;
the errors were C dialect -- `int16_t`, declarations after statements, GCC `__asm__` register
bindings -- which is the same failure mode ``solver/c89.py`` measured as the solver's largest.

ORDER
-----
1. ``c89.to_c89`` (+ ``public_definition``): free, deterministic, meaning-preserving.
2. ``with_context``: the pipeline, not the model, owns the compile context -- the incumbent's
   includes, header-owned declarations, no scalar self-typedefs.
3. ``repair_context.normalize``: the campaign's error-specific normalizers, chosen by the stderr.
4. The model fixes its own code given the compiler's error: a local task, much easier than the
   original. Bounded rounds; each round sees the NEW error.
Each step is compiled; the first that compiles is returned, with the steps that led there.
Nothing here judges the object -- a rescued candidate still has to earn its place by score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

FIX_PROMPT = """\
This C file must compile with IDO 5.3 (a 1994 C89 compiler) but fails. Fix ONLY what the
compiler rejects. Keep the code's structure, statements and meaning exactly as they are.

IDO rules: C89 -- every declaration at the top of its block, no declarations in for(...);
no <stdint.h> types (use s8 u8 s16 u16 s32 u32 s64 u64 f32 f64); no inline, no __attribute__,
no __asm__ register bindings, no // comments; declare every type and function you use or keep
the #include that declares it.

COMPILER ERRORS (clang's diagnostics are the precise ones; IDO's follow):
```
{errors}
```
{declarations}{facts}{feedback}
FILE:
```c
{code}
```

Return the complete corrected file in one ```c block and nothing else.
"""


@dataclass
class Rescue:
    attempt: object | None
    source: str
    steps: list[dict] = field(default_factory=list)
    model_seconds: float = 0.0

    @property
    def rescued_by(self) -> str | None:
        ok = [s for s in self.steps if s.get("compiled")]
        return ok[0]["step"] if ok else None


_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
_SCALAR_SELF_TYPEDEF = re.compile(
    r'^[ \t]*typedef\s+[\w\s]+?\b(s8|u8|s16|u16|s32|u32|s64|u64|f32|f64)\s*;[ \t]*\n?', re.M)


def includes_of(source: str) -> list[str]:
    return _INCLUDE.findall(source or "")


def with_context(source: str, includes, repo=None) -> str:
    """The PIPELINE owns the compile context; the model owns the function.

    Measured on the rescue arm: after C89 repair, the commonest remaining error was `Syntax
    Error` on lines like `s16 iconX[4];` -- the candidate had dropped `common.h`, so the
    project's scalar types did not exist -- followed by redeclarations of what the headers
    declare. So: re-attach any include the known-good incumbent compiled with, let included
    headers own duplicate declarations, and drop self-typedefs of the scalar types `common.h`
    always defines (`typedef s32 s32;` can only be an error)."""
    have = set(includes_of(source))
    missing = [inc for inc in includes if inc not in have]
    if missing:
        source = "".join(f'#include "{inc}"\n' for inc in missing) + source
    source = _SCALAR_SELF_TYPEDEF.sub("", source)
    if repo is not None:
        from solver import project_headers
        source, _removed = project_headers.reconcile_declarations(repo, source)
    return source


def _c89(code: str, function: str) -> str:
    """C89 repair plus public linkage. ``public_definition`` raises unless the text holds exactly
    one ordinary definition of the function; a malformed candidate then just gets the C89 pass --
    a rung that cannot apply fails, it does not crash the ladder (one redraft did)."""
    from solver import c89
    code = c89.to_c89(code)
    try:
        return c89.public_definition(code, function)
    except ValueError:
        return code


def _errors(stderr: str, limit: int = 30) -> str:
    lines = [l for l in (stderr or "").splitlines() if l.strip()]
    return "\n".join(lines[:limit])


_CLANG_ERROR = re.compile(r"error: (.*)")
_UNKNOWN = re.compile(r"unknown type name '(\w+)'|use of undeclared identifier '(\w+)'"
                      r"|implicit declaration of function '(\w+)'|'(\w+)' undefined")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_NOT_CALLS = frozenset({"if", "while", "for", "switch", "return", "sizeof", "do"})


def diagnostics(attempt) -> tuple[str, int]:
    """(text for the prompt, error count). Clang's frontend diagnostics say WHY ("unknown type
    name 's16'"); IDO's cfe mostly says "Syntax Error" at a line, which a model cannot act on --
    15,000 of the campaign's deterministic and 541 of its model compile failures said only that."""
    clang = []
    for line in ((getattr(attempt, "frontend", None) or {}).get("diagnostics") or "").splitlines():
        if _CLANG_ERROR.search(line):
            clang.append(line.strip().replace("candidate.c:", "line "))
    ido = [l for l in (getattr(attempt, "compiler_stderr", "") or "").splitlines()
           if "rror" in l][:8]
    text = "\n".join(clang[:25] + (["--- IDO:"] if clang and ido else []) + ido)
    return text, max(len(clang), len(ido))


def unknown_names(diag: str) -> list[str]:
    names = []
    for groups in _UNKNOWN.findall(diag or ""):
        name = next(g for g in groups if g)
        if name not in names:
            names.append(name)
    return names


_HEADER_INDEX: dict = {}
_DECL_PATTERNS = (r"typedef\b.*\b(\w+)\s*;", r"}\s*(\w+)\s*;", r"\bstruct\s+(\w+)\s*\{",
                  r"^(?:extern\s+)?[\w\s\*]+?\b(\w+)\s*\(", r"^extern\b.*\b(\w+)\s*(?:\[|;)",
                  r"^#\s*define\s+(\w+)")


def declaration_lookup(repo, names, limit: int = 12) -> str:
    """Where the project's headers declare each unknown name, so the model can add the right
    include or use the right type instead of guessing. Headers only, never reference C bodies."""
    from pathlib import Path
    if repo is None or not names:
        return ""
    root = Path(repo) / "include"
    key = str(root)
    if key not in _HEADER_INDEX:
        index: dict[str, str] = {}
        for path in root.rglob("*.h"):
            rel = path.relative_to(root).as_posix()
            for line in path.read_text(errors="replace").splitlines():
                text = line.strip()
                for pattern in _DECL_PATTERNS:
                    m = re.search(pattern, text)
                    if m and m.group(1) not in index:
                        index[m.group(1)] = f"{rel}: {text[:140]}"
        _HEADER_INDEX[key] = index
    index = _HEADER_INDEX[key]
    rows = [f"- {n}: declared in {index[n]}" if n in index else
            f"- {n}: not declared in any project header; declare it or use a declared name"
            for n in names[:limit]]
    return "DECLARATIONS FOR THE UNKNOWN NAMES:\n" + "\n".join(rows) + "\n"


def gutted(before: str, after: str) -> str | None:
    """Why a 'fix' is not acceptable, or None. Compiling is easy to satisfy by deleting the code
    that does not compile; a fix that drops a call or a quarter of the statements is rejected."""
    def body(text):
        return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    lost = ({c for c in _CALL.findall(body(before)) if c not in _NOT_CALLS}
            - {c for c in _CALL.findall(body(after)) if c not in _NOT_CALLS})
    if lost:
        return f"your fix deleted calls to {sorted(lost)[:6]}; restore them and fix the error instead"
    if body(after).count(";") < 0.75 * body(before).count(";"):
        return "your fix deleted a quarter or more of the statements; restore them, fix only the errors"
    return None


def rescue(source: str, stderr: str, *, function: str,
           compile_fn: Callable[[str, str], object],
           fix_fn: Callable[[str], tuple[str, float]] | None = None,
           max_fix_rounds: int = 6, patience: int = 2, context_includes=(), repo=None,
           first_attempt=None, chain: bool = True, facts: str = "") -> Rescue:
    """``compile_fn(label, code) -> Attempt``; ``fix_fn(prompt) -> (source or "", seconds)``.

    Rungs: C89 -> compile context -> normalizers -> ``solver.compile_chain`` (m2c placeholders,
    undefined identifiers, do-while lowering; ``chain=False`` skips it) -> the model. The chain was
    missing here: on the 27 functions where nothing ever compiled it compiled 3 in minutes, while the
    model loop compiled none of 2 in 80 minutes (eval/results/refinement-data-20260927/analysis/
    never_compiled_chain.out). The model starts from the furthest chained source.

    ``facts`` is shown to the model as evidence it cannot derive from the file, e.g. the target
    assembly: most remaining failures are struct layouts (``Selector requires struct/union``), and a
    load such as ``lw v0,0x24(a0)`` states a 4-byte field at 0x24 (never_compiled_causes.out).

    The model rung loops until the code compiles, while the error count keeps falling: it stops
    after ``patience`` rounds without progress, or at ``max_fix_rounds``."""
    from solver import repair_context
    out = Rescue(None, source)
    tried = {source}
    last = [first_attempt]

    def attempt(label: str, code: str) -> bool:
        if not code or code in tried:
            out.steps.append({"step": label, "skipped": "empty-or-duplicate"})
            return False
        tried.add(code)
        att = compile_fn(label, code)
        out.steps.append({"step": label, "compiled": bool(att.compiled)})
        if att.compiled:
            out.attempt, out.source = att, code
            return True
        out.source, last[0] = code, att
        return False

    deterministic = _c89(source, function)
    if attempt("c89", deterministic):
        return out
    contextual = with_context(deterministic, context_includes, repo)
    if attempt("context", contextual):
        return out
    stderr_now = getattr(last[0], "compiler_stderr", None) or stderr
    try:
        normalized = repair_context.normalize(contextual, stderr_now, function) or []
    except ValueError as exc:      # it too requires exactly one ordinary definition
        normalized = []
        out.steps.append({"step": "normalize", "skipped": str(exc)[:120]})
    for label, code in normalized[:4]:
        if attempt(f"normalize:{label}", code):
            return out
    if chain:
        from solver import compile_chain, placeholder_declarations
        start = out.source
        base = last[0] if last[0] is not None else compile_fn("chain-base", start)
        if last[0] is None:
            out.steps.append({"step": "chain-base", "compiled": bool(base.compiled)})
            if base.compiled:
                out.attempt = base
                return out
            last[0] = base
        try:
            headers = placeholder_declarations.header_names(repo, start) if repo is not None else ""
            chained, _log = compile_chain.chain(function, "chain", start, base,
                                                lambda lbl, code: compile_fn(f"chain:{lbl}", code),
                                                headers=headers)
        except Exception as exc:                    # a rung that cannot run declines; it never fails admission
            chained = []
            out.steps.append({"step": "chain", "skipped": f"{type(exc).__name__}: {exc}"[:120]})
        lines = start.count("\n")
        furthest = (start, base)
        for label, code, att in chained:
            tried.add(code)
            out.steps.append({"step": f"chain:{label}", "compiled": bool(att.compiled)})
            if att.compiled:
                out.attempt, out.source = att, code
                return out
            if compile_chain.progress(att, code, lines) > compile_chain.progress(furthest[1], furthest[0], lines):
                furthest = (code, att)
        out.source, last[0] = furthest
    if fix_fn is None:
        return out

    current = out.source
    if last[0] is None:
        # The free rungs changed nothing, so nothing was compiled here: no clang diagnosis and no
        # baseline error count. Diagnose once -- otherwise the model gets only "Syntax Error" and
        # its first round always looks like progress.
        last[0] = compile_fn("diagnose", current)
        out.steps.append({"step": "diagnose", "compiled": bool(last[0].compiled)})
        if last[0].compiled:
            out.attempt = last[0]
            return out
    diag, best = diagnostics(last[0])
    if not diag:
        diag = _errors(stderr)
    stale, feedback = 0, ""
    for round_index in range(max_fix_rounds):
        prompt = FIX_PROMPT.format(
            errors=diag, code=current,
            declarations=declaration_lookup(repo, unknown_names(diag)),
            facts=f"\n{facts.strip()}\n" if facts.strip() else "",
            feedback=f"\nYOUR LAST ATTEMPT WAS REJECTED: {feedback}\n" if feedback else "")
        fixed, seconds = fix_fn(prompt)
        out.model_seconds += seconds
        label = f"model-fix-{round_index + 1}"
        if fixed:
            fixed = with_context(_c89(fixed, function), context_includes, repo)
        reason = gutted(source, fixed) if fixed else "no code returned"
        if reason:
            out.steps.append({"step": label, "rejected": reason})
            feedback, stale = reason, stale + 1
        elif attempt(label, fixed):
            return out
        else:
            feedback = ""
            new_diag, count = diagnostics(last[0])
            if count < best:
                best, stale = count, 0
            else:
                stale += 1
            diag, current = (new_diag or diag), out.source
        if stale >= patience:
            out.steps.append({"step": "stopped", "reason": f"no progress in {patience} rounds"})
            break
    return out


# --- the admission choke point ------------------------------------------------------------------

def c89_repair(code: str, function: str) -> str:
    """Public name for the C89 rung (``_c89``)."""
    return _c89(code, function)


def intake_includes(repo, function: str, asm: str, *, assisted: bool) -> list[str]:
    """The compile context a fresh draft is admitted with: ``common.h`` plus the headers that
    declare the target and its direct callees (``project_headers.context_headers``).

    ``assisted=False`` is the binary-types branch, which the campaign deliberately keeps free of
    reconstructed ``game/`` headers; there only non-game headers are allowed."""
    from solver import project_headers
    headers = ["common.h"]
    try:
        headers += [h for h in project_headers.context_headers(repo, function, asm) if h not in headers]
    except (OSError, ValueError):
        pass
    if not assisted:
        headers = [h for h in headers if not h.startswith("game/")]
    return headers


def admit(source: str, attempt, *, function: str, compile_fn: Callable[[str, str], object],
          repo=None, context_includes=(), fix_fn=None, **loop) -> Rescue:
    """THE choke point: no candidate counts as a compile failure until the ladder has run.

    Every place that creates a candidate from outside a search's own rewrites -- intake drafts,
    model output, normalizations -- passes its compiled attempt here. A candidate that compiled is
    returned as is; one that did not is run through ``rescue`` (C89 -> compile context ->
    normalizers -> the model's convergent self-fix when ``fix_fn`` is given). The caller MUST adopt
    ``result.source`` with ``result.attempt``: a rescued candidate is a different text from the one
    proposed, which is why this is an admission step and not hidden inside ``workspace.score``
    (callers such as register search key attempts by source hash)."""
    if getattr(attempt, "compiled", False):
        return Rescue(attempt, source)
    return rescue(source, getattr(attempt, "compiler_stderr", "") or "", function=function,
                  compile_fn=compile_fn, fix_fn=fix_fn, context_includes=context_includes,
                  repo=repo, first_attempt=attempt, **loop)
