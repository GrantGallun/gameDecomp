"""Merge pointer locals whose uses are confined to opposite arms of an if/else.

The rewrite is a bounded proposal for compiler searches. It only handles plain,
uninitialized function-scope pointer declarations and simple braced if/else
forms. Compiler scoring remains the acceptance gate.
"""
from __future__ import annotations

import re

from solver import c89, regalloc_mutations


_DECL = re.compile(
    r"^[ \t]*(?P<type>[A-Za-z_]\w*(?:[ \t]+[A-Za-z_]\w*)*)"
    r"(?P<pointers>[ \t]*\*+[ \t]*)(?P<name>[A-Za-z_]\w*)[ \t]*;[ \t]*$"
)
_ANY_LOCAL_DECL = re.compile(
    r"(?:^|[{};\n])[ \t]*(?P<type>(?:(?:const|volatile|static|register|extern|unsigned|signed|short|long|"
    r"struct|union|enum)[ \t]+)*(?:[A-Za-z_]\w*)(?:[ \t]+[A-Za-z_]\w*)*?)"
    r"[ \t]*(?:\*+[ \t]*)?(?P<name>[A-Za-z_]\w*)(?:[ \t]*\[[^\]]*\])?[ \t]*(?:;|=)", re.M
)
_NON_TYPE = {"return", "if", "else", "case", "default", "goto", "break", "continue", "while", "for", "do",
             "switch", "sizeof"}
_IDENT = re.compile(r"[A-Za-z_]\w*")


def _tokens(masked: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """Return identifiers and punctuation with absolute offsets, ignoring literals/comments."""
    rows = []
    i = start
    while i < end:
        ch = masked[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            match = _IDENT.match(masked, i)
            assert match
            rows.append((match.group(), i, match.end()))
            i = match.end()
            continue
        rows.append((ch, i, i + 1))
        i += 1
    return rows


def _matching(tokens: list[tuple[str, int, int]], index: int, left: str, right: str):
    if index >= len(tokens) or tokens[index][0] != left:
        return None
    depth = 0
    for j in range(index, len(tokens)):
        value = tokens[j][0]
        if value == left:
            depth += 1
        elif value == right:
            depth -= 1
            if depth == 0:
                return j
    return None


def _if_else_arms(masked: str, begin: int, stop: int):
    """Find simple `if (...) { ... } else { ... }` arm character spans."""
    toks = _tokens(masked, begin, stop)
    for i, (value, start, _end) in enumerate(toks):
        if value != "if" or i + 1 >= len(toks) or toks[i + 1][0] != "(":
            continue
        close_cond = _matching(toks, i + 1, "(", ")")
        if close_cond is None or close_cond + 1 >= len(toks) or toks[close_cond + 1][0] != "{":
            continue
        open_then = close_cond + 1
        close_then = _matching(toks, open_then, "{", "}")
        if close_then is None or close_then + 2 >= len(toks) or toks[close_then + 1][0] != "else":
            continue
        open_else = close_then + 2
        if toks[open_else][0] != "{":
            continue
        close_else = _matching(toks, open_else, "{", "}")
        if close_else is None:
            continue
        yield ((start, toks[open_then][1], toks[close_then][2]),
               (toks[open_else][1], toks[open_else][1], toks[close_else][2]))


def _top_level_declarations(masked: str, begin: int, stop: int):
    body = masked[begin:stop]
    depth = 0
    offset = begin
    for line in body.splitlines(keepends=True):
        content = line[:-1] if line.endswith("\n") else line
        if depth == 0:
            match = _DECL.fullmatch(content)
            if match:
                type_text = " ".join(match.group("type").split())
                if not ({"volatile", "const", "static"} & set(type_text.split())):
                    yield {
                        "name": match.group("name"),
                        "type": type_text + " " + "".join(match.group("pointers").split()),
                        "start": offset,
                        "end": offset + len(line),
                    }
        depth += content.count("{") - content.count("}")
        offset += len(line)


def _all_declaration_names(masked: str, begin: int, stop: int):
    """Find declarations at any block depth, including scalar and inline shadows."""
    names = {}
    body = masked[begin:stop]
    for match in _ANY_LOCAL_DECL.finditer(body):
        if match.group("type").split()[0] in _NON_TYPE:
            continue
        start = begin + match.start("name")
        name = match.group("name")
        names.setdefault(name, []).append((start, start + len(name)))
    return names


def variants(source: str, function: str, limit: int = 12):
    """Yield bounded compatible pointer-local merges in opposite braced if/else arms."""
    begin, stop = regalloc_mutations._body(source, function)
    masked = c89._mask(source)
    declarations = list(_top_level_declarations(masked, begin, stop))
    if len(declarations) < 2:
        return

    body_tokens = _tokens(masked, begin, stop)
    declaration_occurrences = _all_declaration_names(masked, begin, stop)
    occurrences: dict[str, list[tuple[int, int]]] = {}
    for token, start, end in body_tokens:
        if token and (token[0].isalpha() or token[0] == "_"):
            occurrences.setdefault(token, []).append((start, end))
    # A duplicate declaration spelling is a shadowing or redeclaration ambiguity.
    arms = list(_if_else_arms(masked, begin, stop))
    seen = set()
    emitted = 0
    for then_arm, else_arm in arms:
        then_locals = _eligible_locals(declarations, declaration_occurrences, occurrences,
                                       body_tokens, source, then_arm)
        else_locals = _eligible_locals(declarations, declaration_occurrences, occurrences,
                                       body_tokens, source, else_arm)
        else_by_type = {}
        for local in else_locals:
            else_by_type.setdefault(local[0]["type"], []).append(local)
        for first, _refs in then_locals:
            for second, second_refs in else_by_type.get(first["type"], ()):
                if second["name"] == first["name"]:
                    continue
                pair = (first["name"], second["name"], first["start"], second["start"])
                if pair in seen:
                    continue
                seen.add(pair)
                candidate = _replace_identifiers(source, second_refs, second["start"],
                                                 second["end"], second["name"], first["name"])
                label = f"local_web_merge:{first['name']}+{second['name']}"
                yield label, "local_web_merge", candidate
                emitted += 1
                if emitted >= limit:
                    return


def _eligible_locals(declarations, declaration_occurrences, occurrences, body_tokens, source, arm):
    eligible = []
    for decl in declarations:
        decl_start = decl["start"] + source[decl["start"]:decl["end"]].find(decl["name"])
        if decl["start"] >= arm[0] or declaration_occurrences.get(decl["name"]) != [
                (decl_start, decl_start + len(decl["name"]))]:
            continue
        uses = occurrences.get(decl["name"], [])
        if decl_start not in [start for start, _end in uses]:
            continue
        refs = [span for span in uses if span[0] != decl_start]
        if not refs or any(not (arm[1] <= start and end <= arm[2]) for start, end in refs):
            continue
        if _unsafe_uses(body_tokens, decl["name"], refs):
            continue
        eligible.append((decl, refs))
    return eligible


def _unsafe_uses(tokens, name: str, spans: list[tuple[int, int]]) -> bool:
    span_set = set(spans)
    for index, (value, start, end) in enumerate(tokens):
        if value != name or (start, end) not in span_set:
            continue
        previous = tokens[index - 1][0] if index else ""
        following = tokens[index + 1][0] if index + 1 < len(tokens) else ""
        prior = tokens[index - 2][0] if index > 1 else ""
        if (previous in ("&", ".") or (previous == ">" and prior == "-") or following == "["
                or following in ("++", "--") or previous in ("++", "--")):
            return True
    return False


def _replace_identifiers(source: str, refs: list[tuple[int, int]], decl_start: int, decl_end: int, old: str, new: str):
    edits = [(decl_start, decl_end, "")]
    edits.extend((start, end, new) for start, end in refs)
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source
