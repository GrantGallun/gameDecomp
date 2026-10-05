"""Storage and address-use hypotheses selected by compiler residuals.

These compose on a real -O1 pointer-walk residual. Source names do not select
them. The caller must compile each hypothesis and require an exact certificate.
"""
import re
from solver import c89, project_headers, repair_context


def _body(source, function):
    definition, end = repair_context.definition(source, function)
    return definition.end(), end - 1, project_headers._mask_noncode(source)[definition.end():end - 1]


def _declarations(body):
    """Ordinary leading C89 locals only; no arrays or function declarators."""
    result, offset = [], 0
    for line in body.splitlines(keepends=True):
        if line.strip():
            match = c89.DECL_RE.fullmatch(line.rstrip("\r\n"))
            if match is None:
                break
            result.append((offset, match))
        offset += len(line)
    return result


def _unique_local(body, name):
    # Count declarations even in nested, single-line blocks. A shadowed name
    # cannot be associated with one storage location without a real C scope tree.
    word = re.escape(name)
    if re.search(r",\s*(?:\*\s*)*" + word + r"\b\s*(?:[;=,\[])", body):
        return False
    # Unknown typedefs can shadow a supported outer declaration too. This
    # intentionally treats ambiguous `type *name;` expressions as declarations.
    type_shape = (r"(?!(?:return|goto|break|continue|else|case|default)\b)"
                  r"(?:(?:register|static|extern|const|volatile|unsigned|signed|long|struct|union|enum)\s+)*"
                  r"[A-Za-z_]\w*")
    pattern = (r"(?:^|[;{}])\s*" + type_shape + r"(?:(?:\s*\*)+\s*|\s+)\b"
               + word + r"\b\s*(?:[;=,\[])" )
    return len(re.findall(pattern, body, re.M)) == 1


def _diff_sides(diff):
    old, new = [], []
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")) or not line:
            continue
        if line[0] in " -":
            old.append(line[1:].strip())
        if line[0] in " +":
            new.append(line[1:].strip())
    return old, new


def _spill_evidence(diff):
    old, new = _diff_sides(diff)
    stack = r"\b(?:lw|sw)\s+[^,]+,\s*(?:0x[0-9a-fA-F]+|\d+)\(\$?sp\)"
    return any(re.search(stack, line) for line in new) and not any(re.search(stack, line) for line in old)


def register_storage(source, function, diff):
    """Propose register-qualified locals for candidate-only stack accesses.

    The spill signal is a search trigger, not a proof of original declarations.
    Address-taken, shadowed, aggregate and already-qualified locals are excluded.
    One combined proposal and at most eight single-local ablations are emitted.
    """
    if not _spill_evidence(diff):
        return
    begin, stop, body = _body(source, function)
    if re.search(r"\b(?:volatile|asm|__asm__)\b|^[ \t]*#", body, re.M):
        return
    declarations = []
    for offset, match in _declarations(body):
        kind, name = match["type"], match["name"]
        if re.search(r"\b(?:register|static|extern|const)\b", kind):
            continue
        if "*" not in match["ptr"] and not re.fullmatch(
                r"(?:(?:unsigned|signed)\s+)?(?:char|short|int|long|[su](?:8|16|32)|OSPri|OSId|OSIntMask)", kind):
            continue
        if not _unique_local(body, name):
            continue
        if re.search(r"(?<!&)&\s*(?:\(\s*)*" + re.escape(name) + r"\b(?!\s*(?:->|\[))", body):
            continue
        declarations.append((begin + offset + match.start("type"), name))
    if not declarations or len(declarations) > 8:
        return
    groups = [declarations] + ([[entry] for entry in declarations] if len(declarations) > 1 else [])
    for group in groups:
        candidate = source
        for at, _ in reversed(group):
            candidate = candidate[:at] + "register " + candidate[at:]
        names = ",".join(name for _, name in group)
        yield f"register_storage:{names}", "register_storage", candidate


LOAD = re.compile(r"(lw|lh|lhu|lb|lbu)\s+\$?(\w+),\s*(-?(?:0x[0-9a-fA-F]+|\d+))\(\$?(\w+)\)\s*$")
ALIAS = re.compile(
    r"^(?P<i>[ \t]*)(?P<alias>[A-Za-z_]\w*)\s*=\s*&\s*"
    r"(?P<field>(?P<base>[A-Za-z_]\w*)->[A-Za-z_]\w*)\s*;[ \t]*\n"
    r"(?P=i)(?P<dest>[A-Za-z_]\w*)\s*=\s*(?P=field)\s*;", re.M)


def address_reuse(source, function, diff):
    """Use an immediately established field address for the following load."""
    removed = [LOAD.fullmatch(line[1:].strip()) for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [LOAD.fullmatch(line[1:].strip()) for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    if not any(a and b and a.groups()[:3] == b.groups()[:3] and a[4] != b[4] for a in removed for b in added):
        return
    begin, stop, body = _body(source, function)
    if re.search(r"\b(?:volatile|asm|__asm__)\b|^[ \t]*#", body, re.M):
        return
    declarations = {m["name"]: m for _, m in _declarations(body)}
    count = 0
    for match in ALIAS.finditer(body):
        prefix = body[:match.start()].rstrip()
        if prefix and prefix[-1] not in ";{}":
            # Equal indentation does not prove dominance after an unbraced
            # if/while/for/else/do. Require a preceding statement/block boundary.
            continue
        alias, dest, base = match["alias"], match["dest"], match["base"]
        if base not in declarations or "*" not in declarations[base]["ptr"] or not _unique_local(body, base):
            # Re-evaluating an unresolved global or a pointer-valued field can
            # be observable. Only a single field on an ordinary local pointer.
            continue
        if alias == dest or alias not in declarations or "*" not in declarations[alias]["ptr"]:
            continue
        if not _unique_local(body, alias) or not _unique_local(body, dest):
            continue
        if re.search(r"\b" + re.escape(alias) + r"\b", match["field"]):
            continue
        start, end = begin + match.start("dest"), begin + match.end()
        yield (f"address_reuse:{alias}:{dest}", "address_reuse",
               source[:start] + f"{dest} = *{alias};" + source[end:])
        count += 1
        if count == 8:
            return


def parameter_reuse(source, function, diff):
    """Eliminate a first-statement copy of an otherwise unused parameter.

    Only identical declared types, one ordinary local, no observed address
    escape and no further uses of the original parameter. Subsequent writes
    then update the parameter's own local C storage instead of a second local.
    """
    if not _spill_evidence(diff):
        return
    definition, _ = repair_context.definition(source, function)
    if re.search(r"[()\[\]]|\.\.\.", definition[2]):
        return
    begin, stop, body = _body(source, function)
    if re.search(r"\b(?:volatile|asm|__asm__)\b|^[ \t]*#", body, re.M):
        return
    declarations = _declarations(body)
    if not declarations:
        return
    offset, last = declarations[-1]
    at = offset + last.end()
    assignment = re.match(r"\s*(?P<local>[A-Za-z_]\w*)\s*=\s*(?P<parameter>[A-Za-z_]\w*)\s*;", body[at:])
    if not assignment:
        return
    name, parameter = assignment["local"], assignment["parameter"]
    locals_ = {m["name"]: (p,m) for p,m in declarations}
    params = [c89.DECL_RE.fullmatch(p.strip() + ";") for p in definition[2].split(",")]
    params = [p for p in params if p and p["name"] == parameter]
    if name not in locals_ or len(params) != 1 or name == parameter:
        return
    decl_at, decl = locals_[name]
    if decl["rest"] or re.search(r"\b(?:register|static|extern|const|volatile)\b", decl["type"]):
        return
    if "*" not in decl["ptr"] and not re.fullmatch(
            r"(?:(?:unsigned|signed)\s+)?(?:char|short|int|long|[su](?:8|16|32))", decl["type"]):
        return
    if (re.sub(r"\s+", "", decl["type"] + decl["ptr"])
            != re.sub(r"\s+", "", params[0]["type"] + params[0]["ptr"])):
        return
    if not _unique_local(body, name) or len(re.findall(r"\b" + re.escape(parameter) + r"\b", body)) != 1:
        return
    word = re.escape(name)
    if (re.search(r"(?<!&)&\s*(?:\(\s*)*" + word + r"\b(?!\s*(?:->|\[))", body)
            or re.search(r"(?:\.|->)\s*" + word + r"\b", body)
            or re.search(r"\b(?:struct|union|enum)\s+" + word + r"\b", body)):
        return
    removed = [(decl_at, decl_at + decl.end()), (at, at + assignment.end())]
    edits = [(begin+a, begin+b, "") for a,b in removed]
    for use in re.finditer(r"\b" + word + r"\b", body):
        if not any(a <= use.start() < b for a,b in removed):
            edits.append((begin + use.start(), begin + use.end(), parameter))
    candidate = source
    for a,b,text in sorted(edits, reverse=True):
        candidate = candidate[:a] + text + candidate[b:]
    yield f"parameter_reuse:{name}:{parameter}", "parameter_reuse", candidate
