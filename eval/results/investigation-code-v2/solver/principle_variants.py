"""Deterministic C families for experimentally activated compiler principles.

These are search operators, not claimed decompilation rules.  Every emitted
source is compiled and accepted only by the authoritative exactness oracle.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from solver import c89


@dataclass(frozen=True)
class Variant:
    label: str
    source: str


@dataclass(frozen=True)
class _Declaration:
    type_text: str
    stars: str
    name: str
    initializer: str
    trailing: str = ""

    def render(self, indent: str, *, initialize: bool = True,
               register: bool | None = None) -> str:
        type_text = self.type_text
        has_register = type_text.startswith("register ")
        if register is True and not has_register:
            type_text = "register " + type_text
        elif register is False and has_register:
            type_text = type_text[len("register "):]
        declarator = f"{self.stars}{self.name}" if self.stars else self.name
        suffix = (f" = {self.initializer}" if initialize and self.initializer
                  else "")
        return f"{indent}{type_text} {declarator}{suffix};{self.trailing}"


_DECLARATION = re.compile(
    rf"(?P<indent>[ \t]*)(?P<type>{c89.TYPE_WORD})"
    rf"(?P<ptr>(?:\s*\*)+\s*|\s+)"
    rf"(?P<name>[A-Za-z_]\w*)\s*"
    rf"(?P<array>\[[^\]]+\])?\s*"
    rf"(?:=\s*(?P<initializer>[^;]+))?;",
)
_EXTERN = re.compile(
    r"(?m)^\s*extern\s+(?P<type>(?:unsigned\s+)?(?:char|short|int|long|"
    r"[us](?:8|16|32|64)|[A-Z][A-Za-z0-9_]*))\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*;\s*$",
)
_OFFSET_LVALUE_MACRO = re.compile(
    r"(?m)^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)\([^)]*\)"
    r"[^\r\n]*?\(\s*(?P<type>[us](?:8|16|32|64)|char|short|int|long)"
    r"\s*\*\s*\)[^\r\n]*?\+\s*(?P<offset>0x[0-9A-Fa-f]+|[0-9]+)")
_MUTATION_STATEMENT = re.compile(
    r"(?m)^(?P<indent>[ \t]+)"
    r"(?P<lhs>(?:[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)?|"
    r"[A-Za-z_]\w*[ \t]*\([^;\r\n]*\)))\s*"
    r"(?P<operator>\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|=)"
    r"(?P<rhs>[^;\r\n]*);[ \t]*$")
_CALLISH = re.compile(r"\b[A-Za-z_]\w*\s*\(")


def _body_span(source: str, function: str) -> tuple[int, int]:
    pattern = re.compile(rf"\b{re.escape(function)}\s*\(")
    search_from = 0
    match = pattern.search(source, search_from)
    while match is not None:
        cursor = match.end() - 1
        depth = 0
        while cursor < len(source):
            if source[cursor] == "(":
                depth += 1
            elif source[cursor] == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        brace = cursor + 1
        while brace < len(source) and source[brace].isspace():
            brace += 1
        if brace < len(source) and source[brace] == "{":
            depth = 1
            end = brace + 1
            while end < len(source) and depth:
                depth += (source[end] == "{") - (source[end] == "}")
                end += 1
            if depth == 0:
                return brace + 1, end - 1
        search_from = match.end()
        match = pattern.search(source, search_from)
    raise ValueError(f"definition of {function} was not found")


def _leading_declarations(
        body: str) -> tuple[str, list[_Declaration], int, str]:
    """Return a reorderable scalar declaration block and its fixed prefix.

    Semantic candidates commonly put a stack-shaping padding array and a
    comment before their scalar locals.  The old regex demanded that the very
    first body token be a supported scalar declaration, so one padding array
    made every declaration-order variant silently disappear.  Fixed leading
    declarations/comments are preserved verbatim; only the following
    contiguous scalar block is normalized and permuted.
    """
    declarations: list[_Declaration] = []
    masked_lines = c89._mask(body).splitlines(keepends=True)
    lines = body.splitlines(keepends=True)
    cursor = 0
    block_start = None
    block_end = 0
    indent = "    "
    stop = False
    for original_line, masked_line in zip(lines, masked_lines):
        original = original_line.rstrip("\r\n")
        masked = masked_line.rstrip("\r\n")
        if not masked.strip():
            if declarations:
                break
            cursor += len(original_line)
            continue
        line_cursor = 0
        while masked[line_cursor:].strip():
            match = _DECLARATION.match(masked, line_cursor)
            if match is None:
                stop = True
                break
            # Arrays and other fixed prologue declarations affect frame shape
            # but are not register webs. Preserve them before the permuted
            # scalar block.
            if match.group("array"):
                if declarations:
                    stop = True
                    break
                line_cursor = match.end()
                continue
            if block_start is None:
                block_start = cursor + match.start()
            indent = match.group("indent") or indent

            def original_group(name: str) -> str:
                start, end = match.span(name)
                return original[start:end] if start >= 0 else ""

            initializer = original_group("initializer").strip()
            semicolon_from = (
                match.end("initializer") if match.group("initializer")
                else match.end("name"))
            semicolon = original.find(";", semicolon_from)
            if semicolon < 0:
                stop = True
                break
            suffix_has_code = bool(masked[match.end():].strip())
            trailing = original[semicolon + 1:] if not suffix_has_code else ""
            declarations.append(_Declaration(
                original_group("type"),
                original_group("ptr").replace(" ", ""),
                original_group("name"), initializer, trailing))
            block_end = cursor + semicolon + 1
            line_cursor = match.end()
        if stop:
            break
        cursor += len(original_line)
    if not declarations or block_start is None:
        return "", [], 0, indent
    return body[:block_start], declarations, block_end, indent


def _replace_body(source: str, span: tuple[int, int], body: str) -> str:
    return source[:span[0]] + body + source[span[1]:]


def narrow_local_scopes(
        source: str, function: str,
        max_variants: int = 16) -> tuple[Variant, ...]:
    """End one scalar local's lexical lifetime immediately after its last use.

    This is a real allocator-web experiment: unlike declaration reordering or
    equivalent increment spelling, it changes which locals are simultaneously
    in scope.  The first implementation deliberately handles only a flat
    function-tail prefix.  Nested control flow, labels/gotos, address escapes,
    and dependencies from later declaration initializers are declined rather
    than guessed about.  Every emitted source still needs compile and semantic
    replay before it can be retained.
    """
    if max_variants <= 0:
        return ()
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if len(declarations) < 2:
        return ()
    first_indent = re.match(
        r"[ \t]*", body[len(declaration_prefix):]).group(0)
    scope_indent = first_indent or indent
    tail = body[declarations_end:]
    masked_tail = c89._mask(tail)
    variants: list[Variant] = []
    seen = {hashlib.sha256(source.encode()).digest()}

    for index, declaration in enumerate(declarations):
        if len(variants) >= max_variants:
            break
        name = declaration.name
        if "static" in declaration.type_text.split() or \
                "extern" in declaration.type_text.split():
            continue
        # A later declaration initializer executes in the original prologue
        # and may need this local before the proposed inner block exists.
        if any(re.search(rf"\b{re.escape(name)}\b", later.initializer)
               for later in declarations[index + 1:]):
            continue
        uses = list(re.finditer(rf"\b{re.escape(name)}\b", masked_tail))
        if not uses:
            continue
        # An alias can outlive the last textual name occurrence, so lexical
        # last-use reasoning is insufficient once the address escapes.
        if re.search(rf"&\s*\b{re.escape(name)}\b", masked_tail):
            continue
        last = uses[-1]
        semicolon = masked_tail.find(";", last.end())
        if semicolon < 0:
            continue
        scoped_prefix = masked_tail[:semicolon + 1]
        remainder = masked_tail[semicolon + 1:]
        if not remainder.strip():
            continue
        # Only flat statements are handled.  A brace can represent a loop,
        # branch, switch, or compound literal whose true statement boundary
        # needs CFG-aware parsing rather than a semicolon search.
        if "{" in scoped_prefix or "}" in scoped_prefix:
            continue
        if re.search(
                r"(?m)^\s*(?:case\b|default\s*:|[A-Za-z_]\w*\s*:)|\bgoto\b|^\s*#",
                scoped_prefix):
            continue

        remaining = [row for pos, row in enumerate(declarations)
                     if pos != index]
        outer = "\n".join(row.render(scope_indent) for row in remaining)
        original_prefix = tail[:semicolon + 1].strip()
        original_remainder = tail[semicolon + 1:]
        inner_indent = scope_indent + "    "
        inner_lines = "\n".join(
            inner_indent + line.lstrip()
            for line in original_prefix.splitlines() if line.strip())
        scoped = (
            declaration_prefix + outer + "\n" + scope_indent + "{\n" +
            declaration.render(inner_indent) + "\n" + inner_lines + "\n" +
            scope_indent + "}" + original_remainder)
        candidate = _replace_body(source, span, scoped)
        digest = hashlib.sha256(candidate.encode()).digest()
        if digest in seen:
            continue
        seen.add(digest)
        line = source.count(
            "\n", 0, span[0] + declarations_end + semicolon) + 1
        variants.append(Variant(
            f"narrow-scope-{name}-through-L{line}", candidate))
    return tuple(variants)


def pointer_lifetime_variants(
        source: str, function: str,
        max_variants: int = 16) -> tuple[Variant, ...]:
    """Remove or delay a stable address-only pointer web.

    For ``T *p = &base->field`` followed only by ``*p`` reads/writes, direct
    lvalue spelling removes p's complete live range.  A second experiment
    materializes p only around its final flat statement.  Both are emitted
    only when the address root is a function parameter that is never reassigned
    and every textual p use is an explicit dereference.
    """
    if max_variants <= 0:
        return ()
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if len(declarations) < 2:
        return ()
    first_indent = re.match(
        r"[ \t]*", body[len(declaration_prefix):]).group(0)
    outer_indent = first_indent or indent
    tail = body[declarations_end:]
    masked_tail = c89._mask(tail)
    signature_start = source.rfind(function, 0, span[0])
    signature = source[signature_start:span[0]] \
        if signature_start >= 0 else ""
    variants: list[Variant] = []
    seen = {hashlib.sha256(source.encode()).digest()}
    simple_lvalue = re.compile(
        r"&\s*(?P<lvalue>(?P<root>[A-Za-z_]\w*)"
        r"(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*$")

    def add(label: str, candidate: str) -> None:
        if len(variants) >= max_variants:
            return
        digest = hashlib.sha256(candidate.encode()).digest()
        if digest not in seen:
            seen.add(digest)
            variants.append(Variant(label, candidate))

    for index, declaration in enumerate(declarations):
        if len(variants) >= max_variants:
            break
        if not declaration.stars or not declaration.initializer:
            continue
        address = simple_lvalue.fullmatch(declaration.initializer.strip())
        if address is None:
            continue
        name = declaration.name
        root = address.group("root")
        lvalue = re.sub(r"\s+", "", address.group("lvalue"))
        if not re.search(rf"\b{re.escape(root)}\b", signature):
            continue
        # Recomputing &base->field later is equivalent only while base itself
        # retains its entry value. Member writes do not match this scalar-LHS
        # guard; assignments or increments of the root do.
        if re.search(
                rf"(?:\+\+|--)\s*\b{re.escape(root)}\b|"
                rf"\b{re.escape(root)}\b\s*(?:\+\+|--|[+\-*/%&|^]?=(?!=))",
                masked_tail):
            continue
        uses = list(re.finditer(rf"\b{re.escape(name)}\b", masked_tail))
        dereferences = list(re.finditer(
            rf"\*\s*\b{re.escape(name)}\b", masked_tail))
        if not uses or len(uses) != len(dereferences):
            continue
        if any(use.start() != deref.end() - len(name)
               for use, deref in zip(uses, dereferences)):
            continue
        remaining = [row for pos, row in enumerate(declarations)
                     if pos != index]
        outer = "\n".join(row.render(outer_indent) for row in remaining)

        direct_tail = re.sub(
            rf"\*\s*\b{re.escape(name)}\b", lvalue, tail)
        add(f"inline-pointer-lifetime-{name}", _replace_body(
            source, span, declaration_prefix + outer + direct_tail))

        # A late materialization must encompass one complete, flat statement.
        first_use, last_use = uses[0], uses[-1]
        statement_start = masked_tail.rfind(";", 0, first_use.start()) + 1
        statement_end = masked_tail.find(";", last_use.end())
        if statement_end < 0:
            continue
        statement_end += 1
        statement_mask = masked_tail[statement_start:statement_end]
        if "{" in statement_mask or "}" in statement_mask:
            continue
        if any(not (statement_start <= use.start() < statement_end)
               for use in uses):
            continue
        prefix = tail[:statement_start].rstrip()
        statement = tail[statement_start:statement_end].strip()
        suffix = tail[statement_end:]
        inner_indent = outer_indent + "    "
        late_tail = (
            prefix + "\n" + outer_indent + "{\n" +
            declaration.render(inner_indent) + "\n" +
            inner_indent + statement + "\n" + outer_indent + "}" + suffix)
        add(f"late-pointer-lifetime-{name}", _replace_body(
            source, span, declaration_prefix + outer + late_tail))
    return tuple(variants)


def fused_byte_update_lookup(
        source: str, function: str) -> tuple[Variant, ...]:
    """Fuse a byte update/store/immediate lookup into one expression web.

    ``u8 i=x; i++; x=i; return table[x];`` and ``return table[++x];``
    have the same C behavior when x is itself an unsigned byte lvalue.  Their
    compiler webs are materially different: the latter exposes promotion,
    increment, truncating store, and lookup conversion as one expression tree.
    An optional ``p=&x`` used only as the final ``*p`` spelling is removed too.
    """
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if not declarations:
        return ()
    first_indent = re.match(
        r"[ \t]*", body[len(declaration_prefix):]).group(0)
    outer_indent = first_indent or indent
    tail = body[declarations_end:]
    masked_tail = c89._mask(tail)
    # This family describes exactly three flat statements.  Besides making the
    # semantic guard explicit, the bound prevents an anchored whitespace regex
    # from backtracking over an unrelated large function during corpus scans.
    if (len(masked_tail) > 2048 or masked_tail.count(";") != 3 or
            "{" in masked_tail or "}" in masked_tail):
        return ()
    simple_lvalue = re.compile(
        r"(?P<root>[A-Za-z_]\w*)"
        r"(?:\s*(?:->|\.)\s*(?P<field>[A-Za-z_]\w*))*$")
    variants: list[Variant] = []
    seen = {hashlib.sha256(source.encode()).digest()}

    def add(label: str, remaining: list[_Declaration],
            statements: list[str]) -> None:
        outer = "\n".join(row.render(outer_indent) for row in remaining)
        replacement = declaration_prefix
        if outer:
            replacement += outer + "\n"
        replacement += "\n".join(
            outer_indent + statement for statement in statements) + "\n"
        candidate = _replace_body(source, span, replacement)
        digest = hashlib.sha256(candidate.encode()).digest()
        if digest not in seen:
            seen.add(digest)
            variants.append(Variant(label, candidate))

    for value_index, value in enumerate(declarations):
        if value.type_text.strip() not in {"u8", "unsigned char"} or \
                not value.initializer:
            continue
        lvalue_match = simple_lvalue.fullmatch(value.initializer.strip())
        if lvalue_match is None:
            continue
        lvalue = re.sub(r"\s+", "", value.initializer.strip())
        field = lvalue_match.group("field")
        if field:
            if re.search(
                    rf"\b(?:u8|unsigned\s+char)\s+"
                    rf"{re.escape(field)}\s*;", c89._mask(source)) is None:
                continue
        else:
            # A bare lvalue must have an explicit unsigned-byte declaration.
            if re.search(
                    rf"\b(?:u8|unsigned\s+char)\s+"
                    rf"{re.escape(lvalue)}\b", c89._mask(source)) is None:
                continue

        pointer_index = None
        for candidate_index, pointer in enumerate(declarations):
            if (pointer.stars and pointer.initializer and
                    re.sub(r"\s+", "", pointer.initializer) == "&" + lvalue):
                pointer_index = candidate_index
                break
        index_spelling = (
            rf"(?:{re.escape(lvalue)}|\*\s*"
            rf"{re.escape(declarations[pointer_index].name)})"
            if pointer_index is not None else re.escape(lvalue))
        pattern = re.compile(
            rf"^\s*{re.escape(value.name)}\s*\+\+\s*;\s*"
            rf"{re.escape(lvalue)}\s*=\s*{re.escape(value.name)}\s*;\s*"
            rf"return\s+(?P<table>[A-Za-z_]\w*)\s*\[\s*"
            rf"{index_spelling}\s*(?:&\s*(?:0x[fF][fF]|255))?\s*\]"
            rf"\s*;\s*$", re.DOTALL)
        matched = pattern.fullmatch(masked_tail)
        if matched is None:
            continue
        removed = {value_index}
        if pointer_index is not None:
            removed.add(pointer_index)
        remaining = [row for pos, row in enumerate(declarations)
                     if pos not in removed]
        table = matched.group("table")
        add(f"fuse-byte-update-lookup-{value.name}", remaining, [
            f"return {table}[++{lvalue}];",
        ])

        # Keep the old load as an expression temporary but retain a named byte
        # for the post-conversion new value. This preserves three distinct
        # webs instead of the pre-increment form's observed old/new collapse.
        combined_declarations = []
        for pos, declaration in enumerate(declarations):
            if pos == value_index:
                combined_declarations.append(_Declaration(
                    declaration.type_text, declaration.stars,
                    declaration.name, f"{lvalue} + 1",
                    declaration.trailing))
            else:
                combined_declarations.append(declaration)
        original_index = lvalue
        if pointer_index is not None and re.search(
                rf"\*\s*{re.escape(declarations[pointer_index].name)}\b",
                masked_tail):
            original_index = "*" + declarations[pointer_index].name
        add(f"combine-load-increment-{value.name}",
            combined_declarations, [
                f"{lvalue} = {value.name};",
                f"return {table}[{original_index} & 0xFF];",
            ])
        direct_combined = [
            declaration for pos, declaration in
            enumerate(combined_declarations) if pos != pointer_index]
        add(f"combine-load-increment-direct-{value.name}",
            direct_combined, [
                f"{lvalue} = {value.name};",
                f"return {table}[{value.name}];",
            ])

        # Direct field updates retain separate old/new values but remove the
        # named old-value local entirely. The subsequent byte read makes the
        # truncation used by the lookup a separate compiler web.
        for spelling, update in (
                ("postincrement", f"{lvalue}++;"),
                ("add-assign", f"{lvalue} += 1;"),
                ("expanded-add", f"{lvalue} = {lvalue} + 1;")):
            add(f"direct-byte-{spelling}-{value.name}", remaining, [
                update,
                f"return {table}[{lvalue}];",
            ])
    return tuple(variants)


def materialize_return_pointer_variants(
        source: str, function: str) -> tuple[Variant, ...]:
    """Name a pure pointer-add return value before the function's stores."""
    if re.search(r"\bexactnessReturn\b", c89._mask(source)):
        return ()
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if not declarations:
        return ()
    tail = body[declarations_end:]
    type_pattern = (
        r"(?:unsigned\s+)?(?:char|short|int|long)|"
        r"[us](?:8|16|32|64)")
    returned = re.search(
        rf"return\s+(?P<cast>\(\s*(?:{type_pattern})\s*\))\s*"
        rf"\(\s*(?P<pointer>[A-Za-z_]\w*)\s*\+\s*"
        rf"(?P<delta>0x[0-9A-Fa-f]+|[0-9]+)\s*\)\s*;",
        c89._mask(tail))
    if returned is None:
        return ()
    pointer = returned.group("pointer")
    pointer_decl = re.search(
        rf"\b(?P<type>{type_pattern})\s*\*\s*{re.escape(pointer)}\b",
        c89._mask(source))
    if pointer_decl is None:
        return ()
    new_declaration = _Declaration(
        pointer_decl.group("type"), "*", "exactnessReturn",
        f"{pointer} + {returned.group('delta')}")
    replacement_return = (
        f"return {returned.group('cast')} exactnessReturn;")
    changed_tail = (
        tail[:returned.start()] + replacement_return + tail[returned.end():])
    variants: list[Variant] = []
    for label, ordered in (
            ("materialize-return-after-locals",
             declarations + [new_declaration]),
            ("materialize-return-before-locals",
             [new_declaration] + declarations)):
        declaration_block = "\n".join(
            item.render(indent) for item in ordered)
        variants.append(Variant(
            label, _replace_body(
                source, span,
                declaration_prefix + declaration_block + changed_tail)))
    return tuple(variants)


def fuse_load_with_return_postincrement(
        source: str, function: str) -> tuple[Variant, ...]:
    """Tie a pointer load to the +1 pointer ultimately returned by the C."""
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if not declarations:
        return ()
    tail = body[declarations_end:]
    type_pattern = (
        r"(?:unsigned\s+)?(?:char|short|int|long)|"
        r"[us](?:8|16|32|64)")
    returned = re.search(
        rf"return\s+(?P<cast>\(\s*(?:{type_pattern})\s*\))\s*"
        rf"\(\s*(?P<pointer>[A-Za-z_]\w*)\s*\+\s*1\s*\)\s*;",
        c89._mask(tail))
    if returned is None:
        return ()
    pointer = returned.group("pointer")
    if re.search(rf"\b{re.escape(pointer)}\b",
                 c89._mask(tail[:returned.start()])):
        return ()
    variants: list[Variant] = []
    for index, declaration in enumerate(declarations):
        initializer = declaration.initializer.strip()
        loaded = re.fullmatch(
            rf"(?:(?P<cast>\(\s*(?:{type_pattern})\s*\))\s*)?"
            rf"\*\s*{re.escape(pointer)}", initializer)
        if loaded is None:
            continue
        changed_declarations = list(declarations)
        changed_declarations[index] = _Declaration(
            declaration.type_text, declaration.stars, declaration.name,
            ((loaded.group("cast") + " ") if loaded.group("cast") else "") +
            f"*{pointer}++", declaration.trailing)
        changed_tail = (
            tail[:returned.start()] +
            f"return {returned.group('cast')} {pointer};" +
            tail[returned.end():])
        declaration_block = "\n".join(
            item.render(indent) for item in changed_declarations)
        variants.append(Variant(
            f"fuse-load-return-postincrement-{declaration.name}",
            _replace_body(
                source, span,
                declaration_prefix + declaration_block + changed_tail)))
    return tuple(variants)


def inline_early_temp_before_disjoint_write(
        source: str, function: str) -> tuple[Variant, ...]:
    """Inline an early read into its store before an intervening disjoint write."""
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if not declarations:
        return ()
    tail = body[declarations_end:]
    statements = list(_MUTATION_STATEMENT.finditer(tail))
    if len(statements) < 2:
        return ()
    first, second = statements[0], statements[1]
    if tail[:first.start()].strip() or \
            tail[first.end():second.start()].strip():
        return ()
    macros = _offset_lvalue_macros(source)
    members = _direct_struct_member_lvalues(source)
    first_key = _mutation_key(first, macros, members)
    second_key = _mutation_key(second, macros, members)
    if first_key is None or second_key is None or first_key == second_key:
        return ()
    rhs_name = (second.group("rhs") or "").strip()
    declaration_index = next((
        index for index, declaration in enumerate(declarations)
        if declaration.name == rhs_name and declaration.initializer), None)
    if declaration_index is None:
        return ()
    declaration = declarations[declaration_index]
    if _CALLISH.search(declaration.initializer):
        return ()
    remaining = [row for index, row in enumerate(declarations)
                 if index != declaration_index]
    inlined = (
        f"{second.group('indent')}{second.group('lhs').strip()} = "
        f"({declaration.initializer.strip()});")
    changed_tail = (
        tail[:first.start()] + inlined +
        tail[first.end():second.start()] + first.group(0) +
        tail[second.end():])
    declaration_block = "\n".join(row.render(indent) for row in remaining)
    if declaration_block:
        declaration_block += "\n"
    return (Variant(
        f"inline-early-temp-before-disjoint-write-{declaration.name}",
        _replace_body(
            source, span,
            declaration_prefix + declaration_block + changed_tail)),)


def reuse_dead_parameter_for_local(
        source: str, function: str) -> tuple[Variant, ...]:
    """Reuse a same-typed parameter after its original value becomes dead."""
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    if not declarations:
        return ()
    signature = re.search(
        rf"\b{re.escape(function)}\s*\((?P<params>[^)]*)\)",
        c89._mask(source[:span[0]]))
    if signature is None:
        return ()
    parameters: dict[str, str] = {}
    for raw in signature.group("params").split(","):
        match = re.fullmatch(
            rf"\s*(?P<type>{c89.TYPE_WORD})\s+(?P<name>[A-Za-z_]\w*)\s*",
            raw)
        if match:
            parameters[match.group("name")] = " ".join(
                match.group("type").split())
    tail = body[declarations_end:]
    variants: list[Variant] = []
    for index, declaration in enumerate(declarations):
        if declaration.initializer or declaration.stars:
            continue
        local_type = " ".join(declaration.type_text.split())
        assignment = re.search(
            rf"(?m)^(?P<indent>[ \t]*){re.escape(declaration.name)}\s*=\s*"
            rf"(?P<rhs>[^;\r\n]+);", tail)
        if assignment is None:
            continue
        suffix = tail[assignment.end():]
        if not re.search(rf"\b{re.escape(declaration.name)}\b", suffix):
            continue
        for parameter, parameter_type in parameters.items():
            if parameter_type != local_type:
                continue
            rhs = assignment.group("rhs")
            if not re.search(rf"\b{re.escape(parameter)}\b", rhs):
                continue
            if re.search(rf"\b{re.escape(parameter)}\b", suffix):
                continue
            remaining = [row for pos, row in enumerate(declarations)
                         if pos != index]
            declaration_block = "\n".join(
                row.render(indent) for row in remaining)
            if declaration_block:
                declaration_block += "\n"
            changed_suffix = re.sub(
                rf"\b{re.escape(declaration.name)}\b", parameter, suffix)
            changed_tail = (
                tail[:assignment.start()] + assignment.group("indent") +
                f"{parameter} = {rhs.strip()};" + changed_suffix)
            variants.append(Variant(
                f"reuse-parameter-{parameter}-for-{declaration.name}",
                _replace_body(
                    source, span,
                    declaration_prefix + declaration_block + changed_tail)))
    return tuple(variants)


def materialize_return_array_load(
        source: str, function: str) -> tuple[Variant, ...]:
    """Give an array load in a return expression its own value web.

    The declaration remains at the start of the containing function for C89,
    while the assignment stays immediately before the original return.  That
    placement is important: hoisting the load itself would add a memory access
    to paths that return earlier.
    """
    if re.search(r"\bexactnessLoad\b", c89._mask(source)):
        return ()
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    masked_body = c89._mask(body)
    type_pattern = (
        r"(?:unsigned\s+)?(?:char|short|int|long)|[us](?:8|16|32|64)")
    returned = next((row for row in re.finditer(
        r"(?m)^(?P<indent>[ \t]*)return\s+(?P<expr>[^;\r\n]+);",
        masked_body) if "[" in row.group("expr")), None)
    if returned is None:
        return ()
    expression_start = returned.start("expr")
    original_expression = body[expression_start:returned.end("expr")]
    loaded = re.search(
        rf"(?P<cast>\(\s*(?P<type>{type_pattern})\s*\)\s*)?"
        r"(?P<table>[A-Za-z_]\w*)\s*\[\s*(?P<index>[^\]]+)\s*\]",
        original_expression)
    if loaded is None:
        return ()
    table = loaded.group("table")
    local_type = loaded.group("type") or ""
    if not local_type:
        external = re.search(
            rf"\bextern\s+(?P<type>{type_pattern})\s+"
            rf"{re.escape(table)}\s*\[[^\]]*\]\s*;",
            c89._mask(source[:span[0]]))
        if external is None:
            return ()
        local_type = external.group("type")
    load_expression = original_expression[loaded.start():loaded.end()].strip()
    changed_expression = (
        original_expression[:loaded.start()] + "exactnessLoad" +
        original_expression[loaded.end():])
    # Only a load surrounded by casts/parentheses and an optional constant
    # shift is supported. A ?:, &&, another load, or call could make extracting
    # this load unconditional or change its order relative to side effects.
    pure_expression = re.sub(
        rf"\(\s*(?:{type_pattern})\s*\)", "", changed_expression)
    pure_expression = pure_expression.replace("(", "").replace(")", "")
    if not re.fullmatch(
            r"\s*exactnessLoad\s*(?:(?:>>|<<)\s*"
            r"(?:0x[0-9a-fA-F]+|[0-9]+)\s*)?", pure_expression):
        return ()
    return_start = returned.start()
    return_indent = returned.group("indent")
    # A block retains the single-statement shape even under an unbraced if.
    changed_body = (
        body[:return_start] + return_indent + "{\n" + return_indent +
        f"    exactnessLoad = {load_expression};\n" + return_indent +
        f"    return {changed_expression};\n" + return_indent + "}" +
        body[returned.end():])

    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    declaration = f"{indent}{local_type} exactnessLoad;"
    if declarations:
        # The return replacement occurs after the declaration block, so its
        # original end offset remains a valid insertion point.
        changed_body = (
            changed_body[:declarations_end] + "\n" + declaration +
            changed_body[declarations_end:])
    else:
        first_code = re.search(r"\S", masked_body)
        if first_code is None:
            return ()
        insert_at = masked_body.rfind("\n", 0, first_code.start()) + 1
        changed_body = (
            changed_body[:insert_at] + declaration + "\n" +
            changed_body[insert_at:])
    variants = [Variant(
        f"materialize-return-array-load-{table}",
        _replace_body(source, span, changed_body))]
    # Also try the full returned scalar as the named web, but only when its
    # explicit conversion agrees with the local's type. This preserves the
    # truncation point while moving the shift out of the direct return web.
    outer_cast = re.match(
        rf"\(\s*(?P<type>{type_pattern})\s*\)", original_expression)
    if outer_cast and outer_cast.group("type") == local_type:
        scalar_body = changed_body.replace(
            f"exactnessLoad = {load_expression};",
            f"exactnessLoad = {original_expression};", 1).replace(
                f"return {changed_expression};", "return exactnessLoad;", 1)
        variants.append(Variant(
            f"materialize-return-array-result-{table}",
            _replace_body(source, span, scalar_body)))
    return tuple(variants)


def fuse_store_load_with_return_postincrement(
        source: str, function: str) -> tuple[Variant, ...]:
    """Fuse a leading field load/store with the +1 pointer being returned."""
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    statements = list(_MUTATION_STATEMENT.finditer(body))
    if len(statements) < 2:
        return ()
    first, second = statements[0], statements[1]
    if body[first.end():second.start()].strip():
        return ()
    macros = _offset_lvalue_macros(source)
    members = _direct_struct_member_lvalues(source)
    first_key = _mutation_key(first, macros, members)
    second_key = _mutation_key(second, macros, members)
    if first_key is None or second_key is None or first_key == second_key:
        return ()
    if first.group("operator") != "=":
        return ()
    type_pattern = (
        r"(?:unsigned\s+)?(?:char|short|int|long)|"
        r"[us](?:8|16|32|64)")
    returned = re.search(
        rf"return\s+(?P<cast>\(\s*(?:{type_pattern})\s*\))\s*"
        rf"\(\s*(?P<pointer>[A-Za-z_]\w*)\s*\+\s*1\s*\)\s*;",
        c89._mask(body[second.end():]))
    if returned is None:
        return ()
    returned_start = second.end() + returned.start()
    returned_end = second.end() + returned.end()
    pointer = returned.group("pointer")
    rhs = (first.group("rhs") or "").strip()
    direct_load = re.fullmatch(
        rf"\(*\s*(?:(?:\(\s*(?:{type_pattern})\s*\))\s*)?"
        rf"\*\s*{re.escape(pointer)}\s*\)*", rhs)
    if direct_load is None:
        return ()
    if re.search(rf"\b{re.escape(pointer)}\b",
                 c89._mask(body[first.end():returned_start])):
        return ()
    fused_rhs = re.sub(
        rf"\b{re.escape(pointer)}\b", f"{pointer}++", rhs, count=1)
    changed_first = (
        f"{first.group('indent')}{first.group('lhs').strip()} = {fused_rhs};")
    changed_body = (
        body[:first.start()] + changed_first +
        body[first.end():returned_start] +
        f"return {returned.group('cast')} {pointer};" +
        body[returned_end:])
    return (Variant(
        "fuse-store-load-return-postincrement",
        _replace_body(source, span, changed_body)),)


def isolated_register_web(source: str, function: str,
                          max_variants: int = 32) -> tuple[Variant, ...]:
    """Enumerate bounded, semantics-preserving register-lifetime C shapes."""
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    declaration_prefix, declarations, declarations_end, indent = \
        _leading_declarations(body)
    tail = body[declarations_end:]
    variants: list[Variant] = []
    seen = {hashlib.sha256(source.encode()).digest()}

    def add(label: str, candidate: str) -> None:
        if len(variants) >= max_variants:
            return
        digest = hashlib.sha256(candidate.encode()).digest()
        if digest not in seen:
            seen.add(digest)
            variants.append(Variant(label, candidate))

    # A register-only residual can come from source order even when IDO later
    # schedules the machine instructions back into the same order. Try this
    # small, semantically guarded family before spending the variant budget on
    # wider declaration/lifetime changes.
    for variant in independent_statement_order(
            source, function, max_variants=max_variants):
        add(variant.label, variant.source)

    for variant in fused_byte_update_lookup(source, function):
        add(variant.label, variant.source)

    for variant in materialize_return_pointer_variants(source, function):
        add(variant.label, variant.source)

    for variant in fuse_load_with_return_postincrement(source, function):
        add(variant.label, variant.source)

    for variant in inline_early_temp_before_disjoint_write(source, function):
        add(variant.label, variant.source)

    for variant in reuse_dead_parameter_for_local(source, function):
        add(variant.label, variant.source)

    for variant in materialize_return_array_load(source, function):
        add(variant.label, variant.source)

    for variant in fuse_store_load_with_return_postincrement(source, function):
        add(variant.label, variant.source)

    for variant in pointer_lifetime_variants(
            source, function, max_variants=max_variants):
        add(variant.label, variant.source)

    # Scope termination is a materially different live-range lever.  Put it
    # before declaration/qualifier spelling so a tight budget cannot hide it.
    for variant in narrow_local_scopes(
            source, function, max_variants=max_variants):
        add(variant.label, variant.source)

    def rebuild(label: str, ordered: list[_Declaration], *, split: bool,
                register_index: int | None = None,
                unregister_index: int | None = None) -> None:
        lines = []
        for index, declaration in enumerate(ordered):
            register = (True if index == register_index else
                        False if index == unregister_index else None)
            lines.append(declaration.render(
                indent, initialize=not split, register=register))
        assignments = []
        if split:
            assignments = [
                f"{indent}{declaration.name} = {declaration.initializer};"
                for declaration in declarations if declaration.initializer
            ]
        replacement = declaration_prefix + "\n".join(lines)
        if assignments:
            replacement += "\n\n" + "\n".join(assignments)
        replacement += tail
        add(label, _replace_body(source, span, replacement))

    if declarations:
        if any(item.initializer for item in declarations):
            rebuild("split-all-initializers", declarations, split=True)
        # Reorder only after splitting initializers, so dependency order stays
        # in the assignments and every declaration is visible first (C89).
        for index in range(len(declarations) - 1):
            ordered = declarations.copy()
            ordered[index], ordered[index + 1] = ordered[index + 1], ordered[index]
            rebuild(f"split-declarations-swap-{index}-{index + 1}",
                    ordered, split=True)
        if len(declarations) > 2:
            rebuild("split-declarations-reverse", list(reversed(declarations)),
                    split=True)
            rebuild("split-declarations-rotate-left",
                    declarations[1:] + declarations[:1], split=True)
            rebuild("split-declarations-rotate-right",
                    declarations[-1:] + declarations[:-1], split=True)
        for index, declaration in enumerate(declarations):
            if declaration.type_text.startswith("register "):
                rebuild(f"remove-register-{declaration.name}", declarations,
                        split=False, unregister_index=index)
            elif declaration.stars or declaration.type_text.split()[-1] in {
                    "char", "short", "int", "long", "u8", "s8", "u16",
                    "s16", "u32", "s32", "u64", "s64"}:
                rebuild(f"register-{declaration.name}", declarations,
                        split=False, register_index=index)

    # Equivalent pre/post increment spellings often change when a value's live
    # range starts or ends without changing the observable value used later.
    for match in list(re.finditer(r"\b([A-Za-z_]\w*)\s*\+\+\s*;", body)):
        name = match.group(1)
        for label, replacement in (
                ("preincrement", f"++{name};"),
                ("add-assign", f"{name} += 1;"),
                ("expanded-add", f"{name} = {name} + 1;")):
            changed = body[:match.start()] + replacement + body[match.end():]
            add(f"{label}-{name}", _replace_body(source, span, changed))

    # Eliminate an address-only local while retaining the explicit value web.
    alias = re.search(
        r"(?P<ptype>[us](?:8|16|32|64)|(?:unsigned\s+)?(?:char|short|int|long))"
        r"\s*\*\s*(?P<p>[A-Za-z_]\w*)\s*=\s*&(?P<value>[^;]+);\s*"
        r"(?P<itype>[us](?:8|16|32|64)|(?:unsigned\s+)?(?:char|short|int|long))"
        r"\s+(?P<i>[A-Za-z_]\w*)\s*=\s*\*(?P=p)\s*;\s*"
        r"(?P=i)\s*\+\+\s*;\s*\*(?P=p)\s*=\s*(?P=i)\s*;",
        body)
    if alias:
        value, item = alias.group("value").strip(), alias.group("i")
        pointer = alias.group("p")
        item_type = alias.group("itype")
        replacements = (
            ("direct-value-web",
             f"{item_type} {item} = {value}; {item}++; {value} = {item};"),
            ("split-direct-value-web",
             f"{item_type} {item}; {item} = {value}; {item}++; {value} = {item};"),
            ("direct-postincrement", f"{value}++;"),
            ("direct-expanded-add", f"{value} = {value} + 1;"),
        )
        for label, replacement in replacements:
            suffix = re.sub(
                rf"\*\s*{re.escape(pointer)}\b", value,
                body[alias.end():])
            changed = body[:alias.start()] + replacement + suffix
            add(label, _replace_body(source, span, changed))

        # Retain the pointer for later reads while spelling the update through
        # the underlying lvalue. This changes the value and pointer live ranges
        # independently without changing which object is observed.
        pointer_declaration = (
            f"{alias.group('ptype')} *{pointer} = &{value}; ")
        retained = (
            ("pointer-retained-direct-value-web",
             f"{pointer_declaration}{item_type} {item} = {value}; "
             f"{item}++; {value} = {item};"),
            ("pointer-retained-direct-postincrement",
             f"{pointer_declaration}{value}++;"),
        )
        for label, replacement in retained:
            changed = body[:alias.start()] + replacement + body[alias.end():]
            add(label, _replace_body(source, span, changed))

        # If the following expression returns a table lookup through the
        # alias, fuse the increment into that index. This is the C shape whose
        # single value web naturally explains load/add/mask/store/index code.
        suffix = body[alias.end():]
        lookup = re.search(
            rf"return\s+(?P<table>[A-Za-z_]\w*)\s*\[\s*\*\s*"
            rf"{re.escape(pointer)}\s*(?:&\s*0x[fF]+)?\s*\]\s*;",
            suffix)
        if lookup:
            fused = (suffix[:lookup.start()] +
                     f"return {lookup.group('table')}[++{value}];" +
                     suffix[lookup.end():])
            add("fused-preincrement-lookup", _replace_body(
                source, span, body[:alias.start()] + fused))

    # Hold the address of a repeatedly accessed scalar global explicitly. This
    # changes only the pointer web; the object's reads and writes are retained.
    for external in _EXTERN.finditer(source[:span[0]]):
        global_name = external.group("name")
        if len(re.findall(rf"\b{re.escape(global_name)}\b", tail)) < 2:
            continue
        pointer = f"principle_{global_name.lower()}"
        replaced_tail = re.sub(
            rf"\b{re.escape(global_name)}\b", f"(*{pointer})", tail)
        for register in (False, True):
            qualifier = "register " if register else ""
            declaration = (
                f"{indent}{qualifier}{external.group('type')} *{pointer} = "
                f"&{global_name};")
            replacement = body[:declarations_end] + "\n" + declaration + replaced_tail
            add(f"{'register-' if register else ''}global-pointer-{global_name}",
                _replace_body(source, span, replacement))

    return tuple(variants)


def _offset_lvalue_macros(source: str) -> dict[str, tuple[int, int]]:
    widths = {
        "s8": 1, "u8": 1, "char": 1,
        "s16": 2, "u16": 2, "short": 2,
        "s32": 4, "u32": 4, "int": 4, "long": 4,
        "s64": 8, "u64": 8,
    }
    return {
        match.group("name"): (
            int(match.group("offset"), 0), widths[match.group("type")])
        for match in _OFFSET_LVALUE_MACRO.finditer(source)
    }


def _direct_struct_member_lvalues(source: str) -> set[str]:
    """Direct member lvalues proven to belong to a non-union typedef.

    Distinct direct fields of one C struct cannot overlap.  This gives the
    statement-order search a safe lever for generated sources such as
    ``arg0->unkC2 = ...; arg0->unkC4 = ...;`` without extending the same claim
    to unions, nested member paths, pointer arithmetic, or unknown types.
    """
    masked = c89._mask(source)
    allowed: set[str] = set()
    typedefs = re.finditer(
        r"\btypedef\s+struct(?:\s+[A-Za-z_]\w*)?\s*\{"
        r"(?P<body>[^{}]*)\}\s*(?P<type>[A-Za-z_]\w*)\s*;",
        masked, re.DOTALL)
    for typedef in typedefs:
        type_name = typedef.group("type")
        fields = {
            match.group(1) for match in re.finditer(
                r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]+\])?\s*;",
                typedef.group("body"))
        }
        if not fields:
            continue
        pointers = {
            match.group(1) for match in re.finditer(
                rf"\b{re.escape(type_name)}\s*\*+\s*([A-Za-z_]\w*)\b",
                masked)
        }
        for pointer in pointers:
            allowed.update(f"{pointer}->{field}" for field in fields)
    return allowed


def _mutation_key(
        match: re.Match, macros: dict[str, tuple[int, int]],
        struct_members: set[str]) -> tuple[str, int, int] | None:
    lhs = match.group("lhs").strip()
    macro = re.match(r"^([A-Za-z_]\w*)\s*\(", lhs)
    if macro:
        region = macros.get(macro.group(1))
        if region is None:
            return None
        return macro.group(1), region[0], region[1]
    if re.fullmatch(r"[A-Za-z_]\w*", lhs):
        return lhs, -1, 0
    normalized = re.sub(r"\s+", "", lhs)
    if normalized in struct_members:
        return normalized, -1, 0
    return None


def independent_statement_order(
        source: str, function: str,
        max_variants: int = 16) -> tuple[Variant, ...]:
    """Swap adjacent mutations only when their written objects are disjoint.

    Raw-offset lvalue macros are proven disjoint by their byte ranges. Bare
    scalar names are allowed when neither statement reads the other scalar.
    Calls and complex lvalues are declined. Every result is still only a search
    candidate and must pass the semantic and exactness oracles.
    """
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    macros = _offset_lvalue_macros(source)
    struct_members = _direct_struct_member_lvalues(source)
    statements = list(_MUTATION_STATEMENT.finditer(body))
    variants: list[Variant] = []
    seen = {hashlib.sha256(source.encode()).digest()}

    for first, second in zip(statements, statements[1:]):
        if len(variants) >= max_variants:
            break
        between = body[first.end():second.start()]
        if between.strip() or first.group("indent") != second.group("indent"):
            continue
        first_key = _mutation_key(first, macros, struct_members)
        second_key = _mutation_key(second, macros, struct_members)
        if first_key is None or second_key is None:
            continue
        first_name, first_offset, first_width = first_key
        second_name, second_offset, second_width = second_key
        if first_name == second_name:
            continue
        first_rhs = first.group("rhs") or ""
        second_rhs = second.group("rhs") or ""
        # A real call in the RHS may have arbitrary effects. Offset macros in
        # RHS expressions are reads, but dependency analysis for those is not
        # yet implemented, so this intentionally declines them too.
        if _CALLISH.search(first_rhs) or _CALLISH.search(second_rhs):
            continue
        if re.search(rf"\b{re.escape(first_name)}\b", second_rhs) or \
                re.search(rf"\b{re.escape(second_name)}\b", first_rhs):
            continue
        if first_width and second_width:
            if max(first_offset, second_offset) < min(
                    first_offset + first_width,
                    second_offset + second_width):
                continue
        elif first_width != second_width:
            # Do not make an alias claim between a byte-offset object and a
            # bare scalar whose storage class is unknown.
            continue

        changed_body = (
            body[:first.start()] + second.group(0) + between +
            first.group(0) + body[second.end():])
        changed = _replace_body(source, span, changed_body)
        digest = hashlib.sha256(changed.encode()).digest()
        if digest in seen:
            continue
        seen.add(digest)
        first_line = source.count("\n", 0, span[0] + first.start()) + 1
        second_line = source.count("\n", 0, span[0] + second.start()) + 1
        variants.append(Variant(
            f"swap-independent-statements-{first_line}-{second_line}",
            changed))
    return tuple(variants)


def materialize_aliased_rhs_before_write(
        source: str, function: str) -> tuple[Variant, ...]:
    """Force a later pointer read to occur before a disjoint struct write.

    This is a semantic experiment, not an unconditional equivalence rewrite:
    two pointer parameters may alias even when their written C lvalues look
    distinct. It is activated only after target-derived alias cases expose that
    the binary reads the later RHS before the candidate's earlier write.
    """
    if re.search(r"\bexactnessTemp\b", c89._mask(source)):
        return ()
    span = _body_span(source, function)
    body = source[span[0]:span[1]]
    macros = _offset_lvalue_macros(source)
    struct_members = _direct_struct_member_lvalues(source)
    statements = list(_MUTATION_STATEMENT.finditer(body))
    variants: list[Variant] = []

    for first, second in zip(statements, statements[1:]):
        between = body[first.end():second.start()]
        if between.strip() or first.group("indent") != second.group("indent"):
            continue
        first_key = _mutation_key(first, macros, struct_members)
        second_key = _mutation_key(second, macros, struct_members)
        if first_key is None or second_key is None or first_key == second_key:
            continue
        if second.group("operator") != "=":
            continue
        rhs = (second.group("rhs") or "").strip()
        if not re.search(r"(?:\*\s*[A-Za-z_]\w*|[A-Za-z_]\w*\s*\[)", rhs):
            continue
        cast = re.match(
            r"^\(\s*(?P<type>(?:unsigned\s+)?(?:char|short|int|long)|"
            r"[us](?:8|16|32|64))\s*\)\s*.+$", rhs)
        temporary_type = cast.group("type") if cast else ""
        if not temporary_type:
            pointer_read = re.fullmatch(
                r"\*\s*(?P<pointer>[A-Za-z_]\w*)", rhs)
            if pointer_read is not None:
                pointer_decl = re.search(
                    r"\b(?P<type>(?:unsigned\s+)?"
                    r"(?:char|short|int|long)|[us](?:8|16|32|64))\s*"
                    rf"\*\s*{re.escape(pointer_read.group('pointer'))}\b",
                    c89._mask(source))
                if pointer_decl is not None:
                    temporary_type = pointer_decl.group("type")
        if not temporary_type or _CALLISH.search(rhs):
            continue
        # Keep C89 declarations at the top. This first version deliberately
        # declines an executable prefix rather than relocating across it.
        if c89._mask(body[:first.start()]).strip():
            continue
        indent = first.group("indent")
        replacement_second = (
            f"{indent}{second.group('lhs').strip()} = exactnessTemp;")
        changed_body = (
            body[:first.start()] +
            f"{indent}{temporary_type} exactnessTemp = {rhs};\n" +
            first.group(0) + between + replacement_second +
            body[second.end():])
        variants.append(Variant(
            "materialize-aliased-rhs-before-disjoint-write",
            _replace_body(source, span, changed_body)))
    return tuple(variants)
