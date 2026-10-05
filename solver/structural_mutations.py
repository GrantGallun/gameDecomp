"""Source shapes behind recurring structural residuals (2026-09-14 failure census).

Each family is a tested rewrite of one m2c artefact whose IDO output differs from the
target in instruction choice, not in register colouring:

  chained_assignments  `F = 2U; ... v = 2 & 0xFF;` -> `v = F = 2U;`. IDO reuses the
                       stored temporary and masks it (`andi v1,t3,0xff`) where the
                       two statements load the constant twice (`li v1,2`).
                       9 menu update functions, e.g. updateControllerPakRaceRecordSaveExitMessage.
  signed_hex_compares  `x < 0xFFD00000` is an unsigned comparison in C (`sltu`); the
                       target compares signed against -0x300000 (`slt`).
                       6 ending-scene functions, e.g. updateEndingJamSlideRightToMarker.
  register_locals      a local m2c named after a callee-saved register (`temp_s0`)
                       that IDO spills to the stack gets `register`, as libultra's own
                       `register u32 saveMask = __osDisableInt();` (osViBlack and 10 more).

Every variant is only a proposal; the object oracle decides.
"""
from __future__ import annotations

import re

from solver import repair_context

NUMBER = r"(?:0[xX][0-9a-fA-F]+|\d+)"


def _body(source: str, function: str) -> tuple[int, int]:
    match, end = repair_context.definition(source, function)
    return match.end(), end


def _value(text: str) -> int:
    return int(re.sub(r"[uUlL]+$", "", text.strip()), 0)


STORE = re.compile(rf"^(?P<i>[ \t]*)(?P<lhs>[^;=\n]+?)[ \t]*=[ \t]*(?P<k>{NUMBER}[uU]?)[ \t]*;[ \t]*\n"
                   rf"(?P<gap>(?:[ \t]*\}}[ \t]*\n)?)"
                   rf"[ \t]*(?P<v>[A-Za-z_]\w*)[ \t]*=[ \t]*(?:\([^()]*\)[ \t]*)?(?P<k2>{NUMBER}[uU]?)"
                   rf"(?:[ \t]*&[ \t]*(?P<mask>0[xX][fF]{{2,4}}))?[ \t]*;[ \t]*\n", re.M)


def chained_assignments(source: str, function: str):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    sites = []
    for found in STORE.finditer(body):
        if found.group("gap") or _value(found.group("k")) != _value(found.group("k2")):
            continue
        lhs = found.group("lhs").strip()
        if re.fullmatch(re.escape(found.group("v")), lhs) or "==" in lhs:
            continue
        chained = f"{found.group('i')}{found.group('v')} = {lhs} = {found.group('k')};\n"
        sites.append((found, chained))
    masked = [(f, c) for f, c in sites if f.group("mask")]
    for group, label in ((masked, "masked"), (sites, "all")):
        if not group or (label == "all" and len(group) == len(masked)):
            continue
        text = body
        for found, chained in sorted(group, key=lambda item: item[0].start(), reverse=True):
            text = text[:found.start()] + chained + text[found.end():]
        yield (f"chained_assign:{label}", "chained_assign", source[:begin] + text + source[stop:])
    for index, (found, chained) in enumerate(sites):
        if len(sites) > 1:
            yield (f"chained_assign:{index}", "chained_assign",
                   source[:begin] + body[:found.start()] + chained + body[found.end():] + source[stop:])


COMPARE = re.compile(rf"(?P<op><=|>=|<(?![<=])|>(?![>=]))(?P<space>\s*)(?P<lit>0[xX][89a-fA-F][0-9a-fA-F]{{7}})[uU]?(?![\w.])")


def signed_hex_compares(source: str, function: str):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    sites = list(COMPARE.finditer(body))
    if not sites:
        return

    def signed(match):
        magnitude = (1 << 32) - int(match.group("lit"), 16)
        return f"{match.group('op')}{match.group('space')}-0x{magnitude:X}"

    rewritten = COMPARE.sub(signed, body)
    yield ("signed_hex_compare:all", "signed_hex_compare", source[:begin] + rewritten + source[stop:])
    if len(sites) > 1:
        for index, match in enumerate(sites):
            text = body[:match.start()] + signed(match) + body[match.end():]
            yield (f"signed_hex_compare:{index}", "signed_hex_compare", source[:begin] + text + source[stop:])


LOCAL = re.compile(r"^(?P<i>[ \t]*)(?!register\b|static\b|volatile\b|const\b|extern\b)"
                   r"(?P<type>(?:unsigned\s+|signed\s+)?(?:s8|u8|s16|u16|s32|u32|int|short|char|long|OSPri|OSId|"
                   r"OSIntMask|[A-Z]\w*)\s*\**)\s*(?P<name>[A-Za-z_]\w*)(?P<init>\s*=[^;]+)?;[ \t]*$", re.M)


def register_locals(source: str, function: str):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    # m2c names a local after the register the TARGET kept it in: `temp_s0` records that
    # IDO chose a callee-saved register, which is the evidence this rewrite needs.
    saved = [found for found in LOCAL.finditer(body) if re.search(r"_s[0-7]$", found.group("name"))]
    if not saved:
        return
    text = body
    for found in sorted(saved, key=lambda m: m.start(), reverse=True):
        text = text[:found.start("type")] + "register " + text[found.start("type"):]
    yield ("register_locals:saved_named", "register_local", source[:begin] + text + source[stop:])


FAMILIES = (chained_assignments, signed_hex_compares, register_locals)


def signals(source: str) -> bool:
    """Cheap whole-file check for scheduling; the families still decide on the function body."""
    if COMPARE.search(source):
        return True
    if any(re.search(r"_s[0-7]$", found.group("name")) for found in LOCAL.finditer(source)):
        return True
    return any(_value(found.group("k")) == _value(found.group("k2")) and not found.group("gap")
               for found in STORE.finditer(source))


def variants(source: str, function: str):
    for family in FAMILIES:
        try:
            yield from family(source, function)
        except ValueError:
            continue
