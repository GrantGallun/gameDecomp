"""Relocation-name repairs read from the object diff: reference the named data the target uses.

Two shapes, both measured on the 2026-09-14 campaign (214 pending functions had a
relocation-name mismatch; 28 had no other fault):

  literal_names   the candidate emits its own constant (`.rodata+K`) where the target
                  references a named symbol: string literals ("%6dG" ->
                  gRaceSplitscreenSelectEntryFeeFormat), repeated strings ("Hit" x5 ->
                  D_800E1368..D_800E1378), float constants (4.0f/3.0f -> D_800E10C4).
                  The literals are replaced, in source order, by extern references.
  offset_names    the candidate addresses `B+K` where the target names `A`, e.g.
                  `((s16 *)&gRaceSplitscreenSelectCursorTarget + 2)[0]` against
                  gRaceSplitscreenSelectPortraitAlpha. The expression reaching offset K of B
                  is replaced by A, declared with the same element type.

Keeping such data external is ordinary decomp practice until its translation unit is
complete. Every variant is only a proposal; the object oracle decides.
"""
from __future__ import annotations

import re

from solver import repair_context

RELOC = re.compile(r"%(?P<part>hi|lo)\((?P<sym>[.\w]+)(?:\+(?P<k>0x[0-9a-fA-F]+|\d+))?\)")
FLOAT_LOAD = re.compile(r"^\s*(?P<op>lwc1|ldc1)\b")
SIZES = {"s8": 1, "u8": 1, "char": 1, "s16": 2, "u16": 2, "short": 2, "s32": 4, "u32": 4, "int": 4, "f32": 4, "f64": 8}


def _diff_pairs(diff: str):
    """(target symbol+offset, candidate symbol+offset, target instruction) for each differing relocation line pair."""
    removed, added, pairs = [], [], []

    def flush():
        for (want_sym, want_k, want_line), (got_sym, got_k, _got_line) in zip(removed, added):
            pairs.append((want_sym, want_k, got_sym, got_k, want_line))
        removed.clear()
        added.clear()

    for line in diff.splitlines():
        if line.startswith(("---", "+++")):
            continue
        match = RELOC.search(line)
        if line.startswith("-") and match:
            removed.append((match.group("sym"), int(match.group("k") or "0", 0), line[1:]))
        elif line.startswith("+") and match:
            added.append((match.group("sym"), int(match.group("k") or "0", 0), line[1:]))
        elif not line.startswith(("-", "+")):
            flush()
    flush()
    return pairs


def _declared(source: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", source) is not None


def _with_declarations(source: str, function: str, declarations: list[str]) -> str:
    match, _end = repair_context.definition(source, function)
    start = source.rfind("\n", 0, match.start()) + 1
    return source[:start] + "".join(declarations) + source[start:]


STRING = re.compile(r'"(?:\\.|[^"\\])*"')
NUMBER = r"(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?[fFlL]?"
FLOAT_EXPR = re.compile(rf"(?<![\w.]){NUMBER}(?:\s*[-+*/]\s*{NUMBER})*(?![\w.])")


def literal_names(source: str, function: str, diff: str):
    pairs = [p for p in _diff_pairs(diff) if p[2] == ".rodata" and not p[0].startswith(".")]
    if not pairs:
        return
    by_offset = {}
    kinds = {}
    for want, want_k, _got, got_k, line in pairs:
        if want_k:
            continue                          # `%lo(SYM+K)` in the target is not one datum per literal
        by_offset.setdefault(got_k, want)
        loaded = FLOAT_LOAD.match(line)
        if loaded:
            kinds[want] = "f64" if loaded.group("op") == "ldc1" else "f32"
    ordered = [by_offset[k] for k in sorted(by_offset)]
    match, end = repair_context.definition(source, function)
    body_start, body_end = match.end(), end
    body = source[body_start:body_end]
    literals = []
    for token in re.finditer(rf'{STRING.pattern}|{FLOAT_EXPR.pattern}', body):
        text = token.group(0)
        if text.startswith('"') or re.search(r"[.eE]", text):
            literals.append((token.start(), token.end(), "string" if text.startswith('"') else "float"))
    strings = [l for l in literals if l[2] == "string"]
    floats = [l for l in literals if l[2] == "float"]
    names_strings = [n for n in ordered if n not in kinds]
    names_floats = [n for n in ordered if n in kinds]
    if len(strings) != len(names_strings) or len(floats) != len(names_floats):
        return                                # cannot align literals to symbols unambiguously
    replacements = list(zip(strings, names_strings)) + list(zip(floats, names_floats))
    text = body
    for (start, stop, _kind), name in sorted(replacements, key=lambda item: item[0][0], reverse=True):
        text = text[:start] + name + text[stop:]
    declarations = []
    for name in dict.fromkeys(names_strings + names_floats):
        if not _declared(source[:body_start], name):
            declarations.append(f"extern {kinds[name]} {name};\n" if name in kinds else f"extern char {name}[];\n")
    variant = source[:body_start] + text + source[body_end:]
    yield ("literal_names", "relocation_name", _with_declarations(variant, function, declarations) if declarations else variant)


CAST_OFFSET = re.compile(r"\(\(\s*(?P<type>[su](?:8|16|32)|f32|f64|char|short|int)\s*\*\s*\)\s*&\s*(?P<base>\w+)\s*\+\s*(?P<n>\d+)\s*\)\s*\[\s*(?P<m>\d+)\s*\]")
BYTE_OFFSET = re.compile(r"\(?\*\s*\(\s*(?P<type>[su](?:8|16|32)|f32|f64)\s*\*\s*\)\s*\(\s*\(\s*u8\s*\*\s*\)\s*\(?\s*&\s*(?P<base>\w+)\s*\)?\s*\+\s*(?P<k>0x[0-9a-fA-F]+|\d+)\s*\)\)?")


def offset_names(source: str, function: str, diff: str):
    pairs = {(got, got_k): want for want, want_k, got, got_k, _line in _diff_pairs(diff)
             if got_k and not want_k and want != got and not want.startswith(".") and not got.startswith(".")}
    if not pairs:
        return
    match, end = repair_context.definition(source, function)
    body_start, body_end = match.end(), end
    body = source[body_start:body_end]
    edits, declarations = [], []
    for found in CAST_OFFSET.finditer(body):
        size = SIZES[found.group("type")]
        offset = size * (int(found.group("n")) + int(found.group("m")))
        target = pairs.get((found.group("base"), offset))
        if target:
            edits.append((found.start(), found.end(), target, found.group("type")))
    for found in BYTE_OFFSET.finditer(body):
        target = pairs.get((found.group("base"), int(found.group("k"), 0)))
        if target:
            edits.append((found.start(), found.end(), target, found.group("type")))
    if not edits:
        return
    text = body
    for start, stop, target, _type in sorted(edits, reverse=True):
        text = text[:start] + target + text[stop:]
    for _s, _e, target, kind in edits:
        if not _declared(source[:body_start], target) and f"extern {kind} {target};\n" not in declarations:
            declarations.append(f"extern {kind} {target};\n")
    variant = source[:body_start] + text + source[body_end:]
    yield ("offset_names", "relocation_name", _with_declarations(variant, function, declarations) if declarations else variant)


ACCESS_TYPE = {"lb": "s8", "lbu": "u8", "sb": "u8", "lh": "s16", "lhu": "u16", "sh": "u16", "lw": "s32", "sw": "s32",
               "lwc1": "f32", "swc1": "f32", "ldc1": "f64", "sdc1": "f64"}


def field_names(source: str, function: str, diff: str):
    """The candidate reaches `B+K` through a struct field `B.f`; the target names a separate global `A` there.

    Measured 2026-09-30 (updateRaceSetupPlayerCountPrompt, 99.83): `gRaceSetupMenuSubState.alpha` compiles to
    `%lo(gRaceSetupMenuSubState+2)` where the target uses `%lo(gRaceSetupPlayerCountPromptAlpha)`. offset_names
    declined silently because it only recognises cast-and-index spellings. No layout is read: each field `f` that the
    function accesses on `B` is a proposal (`B.f` -> `A`, declared with the access width the target instruction
    uses), and the object oracle decides which field sits at K.
    """
    wanted = {}
    for want, want_k, got, got_k, line in _diff_pairs(diff):
        if got_k and not want_k and want != got and not want.startswith(".") and not got.startswith("."):
            opcode = line.split(None, 1)[0] if line.strip() else ""
            if ACCESS_TYPE.get(opcode):
                wanted.setdefault((got, want), ACCESS_TYPE[opcode])
    if not wanted:
        return
    match, end = repair_context.definition(source, function)
    body_start, body_end = match.end(), end
    body = source[body_start:body_end]
    for (base, target), kind in sorted(wanted.items()):
        fields = dict.fromkeys(re.findall(rf"(?<![\w.>]){re.escape(base)}\s*\.\s*(\w+)", body))
        for field in fields:
            uses = list(re.finditer(rf"(?<![\w.>]){re.escape(base)}\s*\.\s*{re.escape(field)}\b", body))
            # The target names `A` at one access site only; the other uses may still need `B`'s base address. So
            # every use at once, and each single use (measured: all-uses alone scored 93.4 against a 99.8 baseline).
            choices = [("*", uses)] + ([(str(i), [use]) for i, use in enumerate(uses)] if len(uses) > 1 else [])
            for tag, chosen in choices:
                text = body
                for use in reversed(chosen):
                    text = text[:use.start()] + target + text[use.end():]
                variant = source[:body_start] + text + source[body_end:]
                if not _declared(source[:body_start], target):
                    variant = _with_declarations(variant, function, [f"extern {kind} {target};\n"])
                yield (f"field_names:{base}.{field}[{tag}]->{target}", "relocation_name", variant)


def variants(source: str, function: str, diff: str):
    for generator in (literal_names, offset_names, field_names):
        try:
            yield from generator(source, function, diff)
        except ValueError:
            continue
