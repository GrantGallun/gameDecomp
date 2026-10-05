"""Repair source-validity errors the frontend gate reports, using the diagnostic as the evidence.

A candidate whose object already matches is still rejected when its C does not type-check under the frontend
gate. The diagnostic states the fix, its line and the types involved, and each repair below emits no code on
IDO (same-width int/pointer casts, prototypes), so the object certificate decides as usual. Found by scanning
the recorded population searches: five functions held byte-exact objects rejected only here, and these rules
closed all five (MusAsk, MusHandleAsk, osGetThreadPri, createGameTask, releaseRelocatableHeapBlockMetadata;
eval/results/retrodiction-20260922/). Rules:
  incompatible int/pointer conversion assigning to / initializing / returning 'T'  -> cast that value to T
  a function declaration without a prototype is deprecated                           -> `()` at that column -> `(void)`
  implicit declaration of function 'f'                  -> prototype before the definition, types from the call
  passing 'T *' to parameter of type 'U' + note at a parameter of a prototype in the candidate
                                                        -> that parameter becomes T * (void * where calls disagree);
                                                           pointer types only, one word like the s32 it replaces
Diagnostics are read only from the frontend's own `candidate.c:LINE:COL: error:` records, whose lines are
user-source lines (the gate compiles the source under `#line 1 "candidate.c"`).
"""
from __future__ import annotations

import re

DIAG = re.compile(r"^candidate\.c:(\d+):(\d+): error: (.*)$", re.M)
CONVERT = re.compile(r"incompatible (?:integer to pointer|pointer to integer) conversion "
                     r"(?P<how>assigning to|initializing|returning '[^']+' from a function with result type) '(?P<type>[^']+)'")
IMPLICIT = re.compile(r"implicit declaration of function '(\w+)'")
PASSING = re.compile(r"conversion passing '(?P<type>[^']+)'(?: \(aka '(?P<aka>[^']+)'\))? to parameter of type")
NOTE = re.compile(r"^candidate\.c:(\d+):(\d+): note: passing argument to parameter here$", re.M)
PROTOTYPE = re.compile(r"^\s*[A-Za-z_][\w \t*]*\b\w+\s*\((?P<params>[^()]*)\)\s*;\s*$")


def _parameter_types(diagnostics: str, lines: list[str]) -> list[tuple]:
    """`("param_type", line, start, end, type)` edits: each prototype parameter a call passed a pointer to."""
    wanted: dict[tuple[int, int, int], set] = {}
    for m in DIAG.finditer(diagnostics):
        passing = PASSING.search(m.group(3))
        note = NOTE.search(diagnostics, m.end())
        if not passing or not note or DIAG.search(diagnostics, m.end(), note.start()):
            continue
        canonical = passing.group("aka") or passing.group("type")
        if not canonical.rstrip().endswith("*"):
            continue
        n, col = int(note.group(1)), int(note.group(2))
        proto = PROTOTYPE.match(lines[n - 1]) if 1 <= n <= len(lines) else None
        if not proto:
            continue
        at, start = col - 2, proto.start("params")
        for part in proto.group("params").split(","):
            end = start + len(part)
            if start <= at < end:
                lead = len(part) - len(part.lstrip())
                wanted.setdefault((n, start + lead, start + len(part.rstrip())), set()).add(passing.group("type"))
            start = end + 1
    return [("param_type", n, s, e, types.pop() if len(types) == 1 else "void *")
            for (n, s, e), types in sorted(wanted.items())]
SCALAR = r"(?:unsigned\s+|signed\s+)?(?:[su](?:8|16|32|64)|f32|f64|int|short|char|long|float|double)"


def _declared_type(source: str, name: str) -> str:
    """C type of an identifier declared in the source, for a prototype argument; s32 when unknown."""
    m = re.search(rf"\b({SCALAR}|[A-Z]\w*)\s*(\*?)\s*\b{re.escape(name)}\b\s*[,;)=\[]", source)
    if not m:
        return "s32"
    return "void *" if m.group(2) or m.group(1)[0].isupper() else m.group(1)


def _prototype(source: str, line: str, name: str) -> str:
    call = re.search(rf"\b{re.escape(name)}\s*\((?P<args>[^()]*)\)", line)
    args = [a.strip() for a in call.group("args").split(",")] if call and call.group("args").strip() else []
    types = [_declared_type(source, a) if re.fullmatch(r"[A-Za-z_]\w*", a) else "s32" for a in args]
    before = line[:call.start()] if call else ""
    if re.search(r"\([^()]*\*\s*\)\s*\(?\s*$", before):      # result cast to a pointer
        result = "void *"
    elif re.match(r"^\s*$", before):                           # a statement: result unused
        result = "void "
    else:
        result = "s32 "
    return f"{result}{name}({', '.join(types) or 'void'});\n"


def variants(source: str, function: str, frontend: dict | None):
    """`(label, candidate)`: one candidate with every stated repair applied, then each repair alone."""
    from solver import repair_context
    diagnostics = (frontend or {}).get("diagnostics") or ""
    if (frontend or {}).get("passed") is not False or not diagnostics:
        return
    lines = source.split("\n")
    edits, prototypes = [], []
    for m in DIAG.finditer(diagnostics):
        n, col, message = int(m.group(1)), int(m.group(2)), m.group(3)
        if not 1 <= n <= len(lines):
            continue
        text = lines[n - 1]
        if (conv := CONVERT.search(message)):
            ctype = conv.group("type")
            value = (re.search(r"\breturn\b\s*(.+?);", text) if conv.group("how").startswith("returning")
                     else re.search(r"(?<![=!<>+\-*/%&|^])=(?!=)\s*(.+?);", text))
            if value and not value.group(1).startswith(f"({ctype})"):
                edits.append(("cast", n, value.start(1), value.end(1), f"({ctype})({value.group(1)})"))
        elif "without a prototype" in message:
            empty = re.compile(r"\(\s*\)").search(text, max(0, col - 1))
            if empty:
                edits.append(("void_params", n, empty.start(), empty.end(), "(void)"))
        elif (implicit := IMPLICIT.search(message)) and implicit.group(1) not in prototypes:
            prototypes.append(implicit.group(1))
            edits.append(("prototype", n, -1, -1, _prototype(source, text, implicit.group(1))))
    edits += _parameter_types(diagnostics, lines)

    def apply(chosen):
        out = list(lines)
        for kind, n, start, end, text in sorted(chosen, key=lambda e: (-e[1], -e[2])):
            if kind != "prototype":
                out[n - 1] = out[n - 1][:start] + text + out[n - 1][end:]
        result = "\n".join(out)
        protos = "".join(text for kind, *_rest, text in chosen if kind == "prototype")
        if protos:
            try:
                definition, _end = repair_context.definition(result, function)
            except ValueError:
                return None
            at = result.rfind("\n", 0, definition.start()) + 1
            result = result[:at] + protos + "\n" + result[at:]
        return result

    seen = {source}
    for label, chosen in [("all", edits)] + [(f"{e[0]}@{e[1]}", [e]) for e in edits] if len(edits) > 1 else [("all", edits)]:
        candidate = apply(chosen) if chosen else None
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield f"frontend_type:{label}", candidate
