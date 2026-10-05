"""Expose an input-cursor advance at its load instead of the final return.

Confirmed on the Fdistort development residual (2026-09-22): widening the
sign-extension local alone stalls at 81%; `value = *cursor++; return cursor;`
then produces an object-exact match. This is a candidate, never a verdict.
"""
from __future__ import annotations

import re

from solver import project_headers, repair_context


CAST = re.compile(r"^\(\s*(?:(?:unsigned|signed|const)\s+)?"
                  r"(?:s32|u32|s64|u64|int|long|char|short|u8|s8|u16|s16|void)\s*\**\s*\)")


def _whole_addition(expression: str, pointer: str, bare: bool = True) -> bool:
    """Casts must wrap the sum, not change the units of its left operand."""
    expression = expression.strip()
    if re.fullmatch(rf"{re.escape(pointer)}\s*\+\s*1[uU]?", expression):
        return bare
    if expression.startswith("("):
        depth = 0
        for index, char in enumerate(expression):
            depth += (char == "(") - (char == ")")
            if depth == 0:
                if index == len(expression) - 1:
                    return _whole_addition(expression[1:-1], pointer)
                break
    cast = CAST.match(expression)
    if cast:
        return _whole_addition(expression[cast.end():], pointer, bare=False)
    return False


def variants(source: str, function: str):
    match, end = repair_context.definition(source, function)
    begin = match.end()
    masked = project_headers._mask_noncode(source)
    body = masked[begin:end - 1]
    # Only simple pointer parameters. In particular a volatile pointer itself
    # cannot have its write moved, and a macro/control-flow edge is not parsed.
    parameters = match.group(2).split(",")
    pointers = set()
    for parameter in parameters:
        decl = re.fullmatch(r"\s*(?:const\s+)?(?:u8|s8|u16|s16|u32|s32|char|short|int|long)"
                            r"\s*\*\s*(?P<name>[A-Za-z_]\w*)\s*", parameter)
        if decl:
            pointers.add(decl.group("name"))
    if not pointers or re.search(r"\b(?:goto|for|while|do|switch)\b|^[ \t]*#|(?<!\?):", body, re.M):
        return
    returns = list(re.finditer(r"\breturn\s+(?P<value>[^;]+);", body))
    if len(returns) != 1 or body[returns[0].end():].strip():
        return
    tail = returns[0]
    # Return itself must be unconditional and at the function's outer level.
    before_return = body[:tail.start()]
    if before_return.count("{") != before_return.count("}") or re.search(r"\b(?:if|else)\b[^;{}]*$", before_return):
        return
    for pointer in sorted(pointers):
        token = re.compile(rf"\b{re.escape(pointer)}\b")
        if len(token.findall(body)) != 2:
            continue
        addition = re.search(rf"\b{re.escape(pointer)}\s*\+\s*1[uU]?(?![\w.])", tail.group("value"))
        if not addition:
            continue
        if not _whole_addition(tail.group("value"), pointer):
            continue
        load = re.search(rf"^[ \t]*[A-Za-z_]\w*\s*=\s*\*(?P<p>{re.escape(pointer)})\s*;", body, re.M)
        if not load or load.end() >= tail.start():
            continue
        prefix = body[:load.start()]
        if prefix.count("{") != prefix.count("}"):
            continue
        # Reject a one-line unbraced control statement just before the load.
        if re.search(r"\b(?:if|else)\b[^;{}]*$", prefix):
            continue
        start = begin + tail.start("value") + addition.start()
        stop = begin + tail.start("value") + addition.end()
        rewritten = source[:start] + pointer + source[stop:]
        insert = begin + load.end("p")
        rewritten = rewritten[:insert] + "++" + rewritten[insert:]
        yield f"cursor_advance:{pointer}", "cursor_advance", rewritten
