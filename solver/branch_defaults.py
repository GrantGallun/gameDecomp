"""Move paired integer defaults into both arms of a simple conditional.

Confirmed on drawControllerPakFileDeleteConfirmOptions (2026-09-26): the
distributed defaults compile byte-exact where the preceding defaults did not.
The ordinary compiler/object comparison remains the acceptance gate.
"""

from __future__ import annotations

import re

from solver import c89, regalloc_mutations


_NAME = r"[A-Za-z_]\w*"
_LITERAL = r"(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*"
_SITE = re.compile(
    rf"^(?P<i>[ \t]*)(?P<a>{_NAME})[ \t]*=[ \t]*(?P<av>{_LITERAL});[ \t]*\n"
    rf"(?P=i)(?P<b>{_NAME})[ \t]*=[ \t]*(?P<bv>{_LITERAL});[ \t]*\n"
    rf"(?P=i)if[ \t]*\((?P<condition>[^;\n]+)\)[ \t]*\{{[ \t]*\n"
    rf"(?P<j>[ \t]+)(?P=a)[ \t]*=[ \t]*(?P<then>{_LITERAL});[ \t]*\n"
    rf"(?P=i)\}}[ \t]*else[ \t]*\{{[ \t]*\n"
    rf"(?P=j)(?P=b)[ \t]*=[ \t]*(?P<otherwise>{_LITERAL});[ \t]*\n"
    rf"(?P=i)\}}[ \t]*(?:\n|$)", re.M,
)
_SCALAR = r"(?:(?:unsigned|signed)[ \t]+)?(?:u8|s8|u16|s16|u32|s32|u64|s64|char|short|int|long)"


def _depth_at(body: str, offset: int) -> int:
    depth = 0
    for char in body[:offset]:
        depth += (char == "{") - (char == "}")
    return depth


def _plain_local(body: str, name: str, site_start: int) -> bool:
    declarations = list(re.finditer(
        rf"^[ \t]*{_SCALAR}[ \t]+{re.escape(name)}[ \t]*;[ \t]*$", body, re.M,
    ))
    if len(declarations) != 1 or declarations[0].end() > site_start or _depth_at(body, declarations[0].start()) != 0:
        return False
    # No address escape or alternate declaration form (including a shadow).
    if re.search(rf"&[ \t]*\b{re.escape(name)}\b", body):
        return False
    all_declarations = re.findall(
        rf"\b[A-Za-z_]\w*[ \t]+\**{re.escape(name)}\b[ \t]*(?:[;=,\[])", body,
    )
    return len(all_declarations) == 1


def _safe_condition(condition: str, a: str, b: str) -> bool:
    if any(re.search(rf"\b{re.escape(name)}\b", condition) for name in (a, b)):
        return False
    if re.search(r"\+\+|--|(?<![=!<>])=(?!=)|[?:,]|\b[A-Za-z_]\w*\s*\(", condition):
        return False
    return True


def _braceless_parent(prefix: str) -> bool:
    prefix = prefix.rstrip()
    if re.search(r"\b(?:else|do)$", prefix):
        return True
    if not prefix.endswith(")"):
        return False
    depth = 0
    for offset in range(len(prefix) - 1, -1, -1):
        if prefix[offset] == ")":
            depth += 1
        elif prefix[offset] == "(":
            depth -= 1
            if depth == 0:
                return re.search(r"\b(?:if|for|while)\s*$", prefix[:offset]) is not None
    return False


def variants(source: str, function: str):
    """Yield conservative ``(label, family, source)`` rewrites for one function."""
    try:
        begin, stop = regalloc_mutations._body(source, function)
    except (ValueError, LookupError):
        return
    body = source[begin:stop]
    masked = c89._mask(source)[begin:stop]
    if re.search(r"^[ \t]*#", body, re.M):
        return
    macros = set(re.findall(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)", source, re.M))
    for match in _SITE.finditer(masked):
        if _braceless_parent(masked[:match.start()]):
            continue  # Moving the first assignment would escape a braceless parent.
        a, b = match.group("a", "b")
        if a == b or not _plain_local(masked, a, match.start()) or not _plain_local(masked, b, match.start()):
            continue
        if body[match.start():match.end()] != masked[match.start():match.end()]:
            continue  # Comments, strings, or preprocessing in the rewrite span.
        if macros & set(re.findall(r"\b[A-Za-z_]\w*\b", match.group("condition"))):
            continue
        if not _safe_condition(match.group("condition"), a, b):
            continue
        i, j = match.group("i", "j")
        replacement = (
            f"{i}if ({match.group('condition')}) {{\n"
            f"{j}{a} = {match.group('then')};\n"
            f"{j}{b} = {match.group('bv')};\n"
            f"{i}}} else {{\n"
            f"{j}{a} = {match.group('av')};\n"
            f"{j}{b} = {match.group('otherwise')};\n"
            f"{i}}}\n"
        )
        output = source[:begin + match.start()] + replacement + source[begin + match.end():]
        yield f"branch_defaults:{a},{b}@{match.start()}", "branch_defaults", output
