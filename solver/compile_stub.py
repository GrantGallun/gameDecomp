"""Last-resort compiling baseline: neutralize the construct at IDO's first error, keeping its text.

The campaign should always have a compiling version of a function to climb from (user direction,
2026-09-15). When no real fix applies (solver.compile_chain found nothing), this replaces only the
construct on the first reported error line, keeping the original text in a comment so repair can
restore it:

    simple statement            `x = bad;`            -> `/* compile-stub: x = bad; */`
    if/while/switch condition   `if (bad) {`          -> `if (0 /* compile-stub: bad */) {`
    do-while tail               `} while (bad);`      -> `} while (0 /* compile-stub: bad */);`
    for header                  `for (a; b; c) {`     -> `for (; 0 /* compile-stub: a; b; c */; ) {`
    return with a value         `return bad;`         -> `return 0 /* compile-stub: bad */;`

A stubbed source is knowingly incomplete: it exists to get a real byte score, never to be mistaken
for the draft. Declines (reported) when the error line is outside the function body, is a label/case line or a
lone brace, or when the construct spans lines or unbalanced parentheses.
"""
from __future__ import annotations

import re

from solver import repair_context

MARK = "compile-stub"
CONDITION = re.compile(r"^(?P<i>[ \t]*)(?P<kw>if|while|switch)[ \t]*\((?P<cond>.*)\)(?P<rest>[ \t]*\{?[ \t]*)$")
DO_TAIL = re.compile(r"^(?P<i>[ \t]*)\}[ \t]*while[ \t]*\((?P<cond>.*)\)[ \t]*;[ \t]*$")
RETURN = re.compile(r"^(?P<i>[ \t]*)return[ \t]+(?P<expr>[^;]+);[ \t]*$")
SIMPLE = re.compile(r"^(?P<i>[ \t]*)(?P<stmt>[^{}\n]*;)[ \t]*$")


def _comment(text: str) -> str:
    return text.replace("*/", "* /").strip()


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        depth += (char == "(") - (char == ")")
        if depth < 0:
            return False
    return depth == 0


def propose(source: str, function: str, line: int) -> tuple[list[tuple[str, str]], dict]:
    """Stub the construct on 1-based source `line`. [(label, candidate)] and a report."""
    report: dict = {"line": line}
    try:
        match, end = repair_context.definition(source, function)
    except ValueError as exc:
        report["declined"] = str(exc)
        return [], report
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        report["declined"] = "line outside the source"
        return [], report
    start_offset = sum(len(l) + 1 for l in lines[:line - 1])
    body_start = source.index("{", match.start(), end) + 1
    if not body_start <= start_offset < end:
        report["declined"] = "error line is outside the function body"
        return [], report
    text = lines[line - 1]
    stripped = text.strip()
    replacement = None
    if MARK in text:
        report["declined"] = "line already stubbed"
    elif stripped in ("{", "}", "") or stripped.endswith(":") or re.match(r"^(case\b|default\b)", stripped):
        report["declined"] = "brace, label or case line"
    elif (found := re.match(r"^(?P<i>[ \t]*)for[ \t]*\((?P<head>.*)\)(?P<rest>[ \t]*\{?[ \t]*)$", text)) \
            and _balanced(found.group("head")):
        replacement = f"{found.group('i')}for (; 0 /* {MARK}: {_comment(found.group('head'))} */; ){found.group('rest')}"
        report["kind"] = "for_header"
    elif re.match(r"^for\b", stripped):
        report["declined"] = "for header spans lines"
    elif (found := DO_TAIL.match(text)) and _balanced(found.group("cond")):
        replacement = f"{found.group('i')}}} while (0 /* {MARK}: {_comment(found.group('cond'))} */);"
        report["kind"] = "do_while_condition"
    elif (found := CONDITION.match(text)) and _balanced(found.group("cond")):
        replacement = (f"{found.group('i')}{found.group('kw')} (0 /* {MARK}: {_comment(found.group('cond'))} */)"
                       f"{found.group('rest')}")
        report["kind"] = f"{found.group('kw')}_condition"
    elif (found := RETURN.match(text)):
        replacement = f"{found.group('i')}return 0 /* {MARK}: {_comment(found.group('expr'))} */;"
        report["kind"] = "return_value"
    elif (found := SIMPLE.match(text)) and _balanced(found.group("stmt")):
        replacement = f"{found.group('i')}/* {MARK}: {_comment(found.group('stmt'))} */"
        report["kind"] = "statement"
    else:
        report["declined"] = "construct spans lines or braces"
    if replacement is None:
        return [], report
    lines[line - 1] = replacement
    return [(f"stub:{report['kind']}@{line}", "\n".join(lines))], report


def stubbed_lines(source: str) -> int:
    return source.count(f"/* {MARK}:") + source.count(f"0 /* {MARK}:")
