"""Bounded representation candidates confirmed on binary-led development repairs.

Names never select a repair. These are source hypotheses; the caller compiles
and certifies every candidate. No type facts or runtime admission are changed.
"""
import re
from solver import project_headers, repair_context


def _region(source, function):
    definition, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    return definition, end, masked[definition.end():end - 1]


def _unconditional(prefix):
    return (prefix.count("{") == prefix.count("}")
            and not re.search(r"\b(?:if|else|for|while)\b[^;{}]*$", prefix))


CONVERSION = re.compile(
    r"^(?P<i>[ \t]*)(?P<x>[A-Za-z_]\w*)\s*=\s*(?P<load>[^;{}\n]+);\s*\n"
    r"(?P=i)(?P<f>[A-Za-z_]\w*)\s*=\s*\(f32\)\s*(?P=x)\s*;\s*"
    r"if\s*\(\s*\(s32\)\s*(?P=x)\s*<\s*0\s*\)\s*\{\s*"
    r"(?P=f)\s*\+=\s*4294967296\.0[fF]\s*;\s*\}\s*\n"
    r"(?P=i)(?P<use>[^;{}\n]+);", re.M)


def unsigned_float(source, function):
    """Collapse the explicit unsigned-conversion scaffold at one adjacent use.

    A narrow unsigned local plus the signed-negative fixup is an m2c lowering
    artefact. Preserve single evaluation and f32 rounding while exposing the
    conversion as a compiler expression rather than two allocated C temporaries.
    """
    definition, end, body = _region(source, function)
    if re.search(r"\b(?:volatile|asm|__asm__|goto)\b|^[ \t]*#", body, re.M):
        return
    for match in CONVERSION.finditer(body):
        x, floating = match["x"], match["f"]
        if x == floating or not _unconditional(body[:match.start()]):
            continue
        xdecl = re.findall(r"(?m)^[ \t]*(u8|u16)\s+" + re.escape(x) + r"\s*;", body)
        fdecl = re.findall(r"(?m)^[ \t]*f32\s+" + re.escape(floating) + r"\s*;", body)
        if len(xdecl) != 1 or len(fdecl) != 1:
            continue
        if any(len(re.findall(r"\b" + re.escape(name) + r"\b", body)) != 4 for name in (x, floating)):
            continue
        load, use = match["load"], match["use"]
        original_load = source[definition.end() + match.start("load"):definition.end() + match.end("load")]
        original_use = source[definition.end() + match.start("use"):definition.end() + match.end("use")]
        if (not load.strip() or "'" in original_load or '"' in original_load
                or original_load != load or original_use != use
                or re.search(r"\+\+|--|=|\b[A-Za-z_]\w*\s*\(", load)
                or re.search(r"\+\+|--|\?|&&|\|\||\b[A-Za-z_]\w*\s*\(", use)
                or len(re.findall(r"\b" + re.escape(floating) + r"\b", use)) != 1):
            continue
        # The scalar must occur on the RHS, never as a destination or declarator.
        if "=" not in use or re.search(r"\b" + re.escape(floating) + r"\b", use.split("=", 1)[0]):
            continue
        replacement = re.sub(r"\b" + re.escape(floating) + r"\b",
                             lambda _: f"((f32) (u32) {load})", use)
        a, b = definition.end() + match.start(), definition.end() + match.end()
        yield (f"unsigned_float:{x}", "unsigned_float",
               source[:a] + match["i"] + replacement + ";" + source[b:])


def cursor_rebase(source, function, diff):
    """Move a byte-view parameter's advance to its target-observed load boundary.

    All uses must be explicit byte views plus constant offsets. An update is
    inserted only at an unconditional statement boundary and every subsequent
    byte offset is reduced by exactly the same amount, including the return.
    """
    definition, end, body = _region(source, function)
    if re.search(r"\b(?:volatile|asm|__asm__|goto|while|for|switch|do)\b|^[ \t]*#", body, re.M):
        return
    params = definition[2].split(",")
    if len(params) > 4 or any(re.search(r"\b(?:u64|s64|f64|double)\b", p) for p in params):
        return
    returns = list(re.finditer(r"\breturn\b[^;]*;", body))
    if len(returns) != 1 or body[returns[0].end():].strip() or not _unconditional(body[:returns[0].start()]):
        return
    strides = set()
    for stride in re.finditer(r"(?m)^-\s*addiu\s+\$?(a[0-3]),\s*\$?\1,\s*(0x[\da-fA-F]+|\d+)\s*$", diff):
        amount = int(stride[2], 0)
        if 0 < amount <= 32767:
            strides.add((int(stride[1][1]), amount))
    for index, amount in sorted(strides):
        if index >= len(params):
            continue
        param = re.fullmatch(r"\s*(?:void|u8|s8|char|unsigned\s+char)\s*\*\s*(\w+)\s*", params[index])
        if not param:
            continue
        name = param[1]
        view = re.compile(r"\(\(\s*(?:u8|s8|char|unsigned\s+char)\s*\*\s*\)\s*"
                          + re.escape(name) + r"\s*\)\s*\+\s*(?P<offset>0x[\da-fA-F]+|\d+)(?![\w.])")
        sites = list(view.finditer(body))
        if len(sites) < 2 or len(sites) != len(re.findall(r"\b" + re.escape(name) + r"\b", body)):
            continue
        if not any(s.start() >= returns[0].start() for s in sites):
            continue
        selected = next((s for s in sites if int(s["offset"], 0) >= amount), None)
        if selected is None or selected.start() >= returns[0].start():
            continue
        at = body.rfind("\n", 0, selected.start()) + 1
        if not _unconditional(body[:at]) or any(int(s["offset"], 0) < amount for s in sites if s.start() >= at):
            continue
        # The insertion line must be a complete assignment, not an if/control
        # header, declaration, or continuation of a previous expression.
        line_end = body.find("\n", at)
        line = body[at:line_end if line_end >= 0 else len(body)]
        if "=" not in line or not line.rstrip().endswith(";") or re.search(r"\b(?:if|else|return)\b", line):
            continue
        lhs = line.split("=", 1)[0].strip()
        if not (re.fullmatch(r"[A-Za-z_]\w*", lhs) or lhs.startswith(("(*", "*("))):
            continue
        if body[:at].rstrip() and not body[:at].rstrip().endswith((";", "}")):
            continue
        # Preserve comments and original text when emitting edits.
        edits = [(definition.end() + s.start("offset"), definition.end() + s.end("offset"),
                  str(int(s["offset"], 0) - amount)) for s in sites if s.start() >= at]
        indent = re.match(r"[ \t]*", line)[0]
        edits.append((definition.end() + at, definition.end() + at,
                      f"{indent}{name} = (u8 *){name} + {amount};\n"))
        edited = source
        for start, stop, replacement in sorted(edits, reverse=True):
            edited = edited[:start] + replacement + edited[stop:]
        yield f"cursor_rebase:{name}:{amount}", "cursor_rebase", edited


def residual_evidence(source, function, diff):
    """Expose existing bounded residual generators to the normal mutation search."""
    from solver import rewrites
    definition, end, _ = _region(source, function)
    families = (rewrites.byte_pointer_step_rewrites, rewrites.pointer_element_width_rewrites,
                rewrites.pointer_difference_scale_rewrites, rewrites.compare_swap_rewrites,
                rewrites.branch_sentinel_rewrites, rewrites.signed_compare_rewrites)
    for family in families:
        for rewrite in family(source, diff):
            candidate = rewrite(source)
            # These integration candidates may edit only the selected function.
            # Each existing generator still applies its own residual guards.
            if (candidate.startswith(source[:definition.start()]) and candidate.endswith(source[end:])
                    and candidate != source):
                yield f"residual_evidence:{rewrite.label}", "residual_evidence", candidate
