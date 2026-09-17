"""Source mutations aimed at register-allocation signatures, for enumerating search.

Each generator yields (label, family, source) variants of ONE function body. They
are proposals, not repairs: every variant is compiled, and only an object-exact
compile is a match. A wrong parse therefore costs one compile, never a false
match: byte-identical code is by definition the original's behaviour.

Families, keyed to `solver.regalloc_signature` signatures:

    local_type    retype one integer local  (commutative_swap from implicit widening)
    commutative   swap the operands of one  * + & | ^ == !=   (commutative_swap)
    decl_order    swap two adjacent local declarations of the body  (web numbering)
    inline_temp   existing `rewrites.inline_temporary_rewrites`  (temp_vs_variable: extra web)
    stmt_order    existing `rewrites.statement_order_rewrites`, ungated  (colour ties)

The expression parser is deliberately small: C expressions over a token stream,
with casts recognised by a parenthesised type spelling. It declines what it does
not understand instead of guessing.
"""
from __future__ import annotations

import re

from solver import c89

TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<number>0[xX][0-9a-fA-F]+[uUlL]*|\d+\.\d*(?:[eE][-+]?\d+)?[fF]?|\d+[uUlLfF]*|\.\d+[fF]?)
  | (?P<ident>[A-Za-z_]\w*)
  | (?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<op>->|\+\+|--|<<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^!~<>=?:,.;(){}\[\]])
""", re.VERBOSE)

BINARY = {"*": 10, "/": 10, "%": 10, "+": 9, "-": 9, "<<": 8, ">>": 8, "<": 7, ">": 7, "<=": 7, ">=": 7,
          "==": 6, "!=": 6, "&": 5, "^": 4, "|": 3, "&&": 2, "||": 1}
COMMUTATIVE = {"*", "+", "&", "|", "^", "==", "!="}
ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
TYPE_WORDS = {"void", "char", "short", "int", "long", "float", "double", "signed", "unsigned", "struct", "union",
              "enum", "const", "volatile", "u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64", "f32", "f64"}
KEYWORDS = {"if", "else", "while", "for", "do", "switch", "case", "default", "return", "goto", "break", "continue",
            "sizeof"}


class Decline(ValueError):
    pass


def tokens(text: str, base: int = 0) -> list[tuple[str, str, int, int]]:
    rows, position = [], 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match:
            raise Decline(f"untokenisable text at {base + position}")
        kind = match.lastgroup
        if kind != "space":
            rows.append((kind, match.group(), base + match.start(), base + match.end()))
        position = match.end()
    return rows


class Parser:
    """Precedence climbing; records (op, left span, right span) of every binary node."""

    def __init__(self, toks, typedefs):
        self.toks, self.i, self.typedefs, self.binaries = toks, 0, typedefs, []

    def peek(self, offset=0):
        index = self.i + offset
        return self.toks[index] if index < len(self.toks) else ("eof", "", -1, -1)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise Decline(f"expected {value!r}, found {tok[1]!r}")
        self.i += 1
        return tok

    def is_type_start(self, offset):
        kind, value, *_ = self.peek(offset)
        return kind == "ident" and (value in TYPE_WORDS or value in self.typedefs)

    def expression(self):
        start, end = self.assignment()
        while self.peek()[1] == ",":
            self.take()
            _s, end = self.assignment()
        return start, end

    def assignment(self):
        start, end = self.conditional()
        if self.peek()[1] in ASSIGN:
            self.take()
            _s, end = self.assignment()
        return start, end

    def conditional(self):
        start, end = self.binary(1)
        if self.peek()[1] == "?":
            self.take()
            self.expression()
            self.take(":")
            _s, end = self.conditional()
        return start, end

    def binary(self, minimum):
        start, end = self.unary()
        while True:
            op = self.peek()[1]
            precedence = BINARY.get(op)
            if precedence is None or precedence < minimum or self.peek()[0] != "op":
                return start, end
            self.take()
            right = self.binary(precedence + 1)
            self.binaries.append((op, precedence, (start, end), right))
            end = right[1]

    def unary(self):
        kind, value, start, _end = self.peek()
        if value in ("-", "+", "!", "~", "*", "&", "++", "--"):
            self.take()
            _s, end = self.unary()
            return start, end
        if value == "sizeof":
            self.take()
            if self.peek()[1] == "(" and self.is_type_start(1):
                return start, self.type_name_parens()
            _s, end = self.unary()
            return start, end
        if value == "(" and self.is_type_start(1):
            self.type_name_parens()
            _s, end = self.unary()
            return start, end
        return self.postfix()

    def type_name_parens(self):
        self.take("(")
        depth = 1
        while depth:
            tok = self.take()
            if tok[0] == "eof":
                raise Decline("unterminated type name")
            depth += (tok[1] == "(") - (tok[1] == ")")
            if tok[1] in (";", "{", "}"):
                raise Decline("statement inside a type name")
        return self.toks[self.i - 1][3]

    def postfix(self):
        start, end = self.primary()
        while True:
            value = self.peek()[1]
            if value == "(":
                self.take()
                if self.peek()[1] != ")":
                    self.assignment()
                    while self.peek()[1] == ",":
                        self.take()
                        self.assignment()
                end = self.take(")")[3]
            elif value == "[":
                self.take()
                self.expression()
                end = self.take("]")[3]
            elif value in (".", "->"):
                self.take()
                tok = self.take()
                if tok[0] != "ident":
                    raise Decline("member name expected")
                end = tok[3]
            elif value in ("++", "--"):
                end = self.take()[3]
            else:
                return start, end

    def primary(self):
        kind, value, start, end = self.take()
        if kind in ("ident", "number", "string") and value not in KEYWORDS | TYPE_WORDS:
            return start, end
        if value == "(":
            self.expression()
            return start, self.take(")")[3]
        raise Decline(f"unexpected token {value!r}")


def _typedefs(source: str) -> set[str]:
    names = set(re.findall(r"\btypedef\b[^;]*?\b([A-Za-z_]\w*)\s*;", source))
    names |= set(re.findall(r"\bstruct\s+([A-Za-z_]\w*)", source))
    return names | set(re.findall(r"\b([A-Z]\w*)\s*\*", source))


def _body(source: str, function: str):
    from solver import repair_context
    match, end = repair_context.definition(source, function)
    return match.end(), end - 1                        # inside the braces


def _expressions(source: str, begin: int, stop: int):
    """Spans of full expressions in the body: statement text between ; { } and control headers."""
    masked = c89._mask(source)
    toks = [t for t in tokens(masked[begin:stop], begin)]
    chunk = []
    for tok in toks + [("op", ";", stop, stop)]:
        if tok[1] in (";", "{", "}") or tok[1] in ("else", "do"):
            yield chunk
            chunk = []
        else:
            chunk.append(tok)


def commutative_swaps(source: str, function: str, limit: int = 60):
    begin, stop = _body(source, function)
    typedefs = _typedefs(source)
    masked = c89._mask(source)
    seen = set()
    for chunk in _expressions(source, begin, stop):
        # Parse expression after expression; skip one token where parsing fails
        # (keywords, declaration types, a control header's closing parenthesis).
        binaries, index = [], 0
        while index < len(chunk):
            parser = Parser(chunk[index:], typedefs)
            try:
                parser.expression()
                binaries += parser.binaries
                index += max(parser.i, 1)
            except Decline:
                index += 1
        for op, precedence, left, right in binaries:
            if op not in COMMUTATIVE or (left, right) in seen:
                continue
            if op == "*" and masked[left[0]:left[1]].strip() in TYPE_WORDS | typedefs:
                continue                               # `Type *name` in a declaration, not a product
            seen.add((left, right))
            left_text, right_text = source[left[0]:left[1]], source[right[0]:right[1]]
            # The old left operand moves right, where same-precedence operators need grouping.
            inner = Parser(tokens(masked[left[0]:left[1]], left[0]), typedefs)
            try:
                inner.expression()
                # Only the operand's TOP-level operator matters: one spanning it whole.
                needs_group = any(p <= precedence for _o, p, _l, _r in inner.binaries
                                  if _l[0] == left[0] and _r[1] == left[1])
            except Decline:
                needs_group = True
            moved = f"({left_text})" if needs_group else left_text
            variant = source[:left[0]] + right_text + source[left[1]:right[0]] + moved + source[right[1]:]
            yield (f"commutative:{op}@{left[0]}", "commutative", variant)
            if len(seen) >= limit:
                return


def declaration_swaps(source: str, function: str, limit: int = 30):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    lines = body.split("\n")
    offsets, position = [], begin
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    typedefs = _typedefs(source)
    decl = re.compile(r"^\s*(?:(?:unsigned|signed|const|struct|union)\s+)*[A-Za-z_]\w*\s*[\s*]\s*[A-Za-z_]\w*"
                      r"(?:\s*\[[^\]]*\])?(?:\s*=[^;{}]*)?\s*;\s*$")
    rows = [i for i, line in enumerate(lines)
            if decl.match(line) and line.split()[0].strip("*") in TYPE_WORDS | typedefs | {"unsigned", "signed", "const", "struct", "union"}]
    count = 0
    for a, b in zip(rows, rows[1:]):
        if b != a + 1 or "=" in lines[a] or "=" in lines[b]:
            continue                                   # initialisers carry evaluation order; leave them
        swapped = list(lines)
        swapped[a], swapped[b] = swapped[b], swapped[a]
        yield (f"decl_order:{a}", "decl_order", source[:begin] + "\n".join(swapped) + source[stop:])
        count += 1
        if count >= limit:
            return


INTEGER_TYPES = ("s32", "u32", "s16", "u16", "s8", "u8")


def local_types(source: str, function: str, limit: int = 40):
    """Retype one plain integer local of the body.

    Motivating residual: makeFixedRotationXY (2026-09-13) showed `multu v0,a0`
    against `multu a0,v0` -- a commutative_swap signature -- but swapping the C
    operands changed nothing. Declaring the m2c local `s16 temp_v0` as `s32` made
    the object exact: IDO orders the operands around the implicit widening of a
    narrow local, so this signature can have a TYPE cause, not an order cause.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(r"^(?P<indent>[ \t]*)(?P<type>s32|u32|s16|u16|s8|u8|int|short|char)(?P<rest>[ \t]+\**[ \t]*"
                         r"[A-Za-z_]\w*[ \t]*(?:=[^;\n]*)?;)", re.M)
    count = 0
    for match in pattern.finditer(body):
        if "*" in match.group("rest").split("=")[0]:
            continue                                   # pointer locals keep their pointee type
        for replacement in INTEGER_TYPES:
            if replacement == match.group("type"):
                continue
            start, end = begin + match.start("type"), begin + match.end("type")
            yield (f"local_type:{match.group('type')}->{replacement}@{start}", "local_type",
                   source[:start] + replacement + source[end:])
            count += 1
            if count >= limit:
                return


SIMPLE_STATEMENT = re.compile(r"^(?P<indent>[ \t]+)(?!(?:return|goto|break|continue|case|default|if|else|for|while|do|"
                              r"switch)\b)(?![A-Za-z_]\w*\s*:)[^;{}\n]*[^;{}\s][^;{}\n]*;[ \t]*$")
DECLARATION = re.compile(r"^\s*(?:(?:unsigned|signed|const|static|struct|union|enum)\s+)*(?:s32|u32|s16|u16|s8|u8|"
                         r"f32|f64|int|short|char|long|float|double|void|[A-Z]\w*)\b[\s*]+[A-Za-z_]\w*(?:\s*\[[^\]]*\])?"
                         r"(?:\s*=[^;]*)?;\s*$")


def statement_moves(source: str, function: str, distance: int = 3, limit: int = 60):
    """Move one single-line statement up to `distance` places within its run of simple statements.

    Motivating residual: func_8005B49C (2026-09-13). The target loads the
    constant of a later store into t6 before the decrement's temporaries,
    `li t6,2` ahead of `addiu t8,t7,-1`, then IDO places the store in a delay
    slot. Only writing the constant store BEFORE the decrement matched. The
    `rewrites.statement_order_rewrites` family never fired on that body. No
    independence analysis is needed for soundness here: only a byte-identical
    object is accepted.
    """
    begin, stop = _body(source, function)
    lines = source[begin:stop].split("\n")
    simple = [bool(SIMPLE_STATEMENT.match(line)) and not DECLARATION.match(line) for line in lines]
    count = 0
    for index, line in enumerate(lines):
        if not simple[index]:
            continue
        indent = SIMPLE_STATEMENT.match(line).group("indent")
        for step in range(1, distance + 1):
            for target in (index - step, index + step):
                if not 0 <= target < len(lines):
                    continue
                span = range(min(index, target), max(index, target) + 1)
                if not all(simple[i] and SIMPLE_STATEMENT.match(lines[i]).group("indent") == indent for i in span):
                    continue
                moved = list(lines)
                del moved[index]
                moved.insert(target, line)
                yield (f"stmt_move:{index}->{target}", "stmt_move", source[:begin] + "\n".join(moved) + source[stop:])
                count += 1
                if count >= limit:
                    return


def constant_local_inlines(source: str, function: str):
    """Replace a local that only ever holds one integer constant with that constant.

    m2c materialises constants into named locals (`int tmp; tmp = 2; x = (s16)tmp;`).
    A named local is a web of its own, which moves temporary numbering and
    colouring. Part of the func_8005B49C match (2026-09-13).
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    masked = c89._mask(source)[begin:stop]
    for decl in re.finditer(r"^[ \t]*(?:s32|u32|s16|u16|s8|u8|int|short|char)[ \t]+(?P<name>[A-Za-z_]\w*)"
                            r"(?:[ \t]*=[ \t]*(?P<init>-?(?:0[xX][0-9a-fA-F]+|\d+)))?[ \t]*;[ \t]*\n", masked, re.M):
        name = decl.group("name")
        assigns = list(re.finditer(rf"^[ \t]*{name}[ \t]*=[ \t]*(?P<value>-?(?:0[xX][0-9a-fA-F]+|\d+))[ \t]*;[ \t]*\n",
                                   masked, re.M))
        writes = len(re.findall(rf"\b{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*{name}\b|\b{name}\s*\+\+|--\s*{name}\b|"
                                rf"\b{name}\s*--|&\s*{name}\b", masked))
        value = decl.group("init")
        if value is not None:
            if assigns or writes:
                continue
            removals = [(decl.start(), decl.end())]
        else:
            if len(assigns) != 1 or writes != 1:
                continue
            value = assigns[0].group("value")
            removals = [(decl.start(), decl.end()), (assigns[0].start(), assigns[0].end())]
        text = body
        for start, end in sorted(removals, reverse=True):
            text = text[:start] + text[end:]
        replaced, uses = re.subn(rf"\b{name}\b", f"({value})" if value.startswith("-") else value, text)
        if uses:
            yield (f"const_inline:{name}", "const_inline", source[:begin] + replaced + source[stop:])


def _field_key(text: str) -> str:
    """A field's identity: no whitespace, and m2c's cast-dereference pointee type ignored."""
    compact = re.sub(r"\s+", "", text)
    return re.sub(r"^\(\*\((?:unsigned|signed)?\w+\*\)", "(*(T*)", compact)


# A field: `a->b.c`, `a[i]`, or m2c's cast dereference `(*(u8 *)((u8 *)(arg0) + 0x518))`.
LVALUE = (r"(?:[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*|\s*\[\s*[^\]\n;]+\])+"
          r"|\(\s*\*\s*\([^;=\n]+\)\s*\([^;=\n]+\)\s*\))")
CALL = re.compile(r"\b(?!(?:s32|u32|s16|u16|s8|u8|int|short|char|void|f32|f64|float)\b)[A-Za-z_]\w*\s*\(")


def field_local_eliminations(source: str, function: str):
    """Drop a local that m2c used to cache a field; use the field expression itself.

    Motivating residual: updateEndingSlashSlideToCenter and its 30 siblings in
    the ending-credits code (2026-09-13). The shape is

        var = FIELD + K;  FIELD = var;  if (var >= N) { FIELD = N; ...; var = FIELD; }  use(var);

    The target loads FIELD into a ugen temporary (`lw t6` / `addu a1,t6,at`); the
    local made IDO load straight into a1. Writing `FIELD += K;` and FIELD at every
    use matched exactly, because uopt's own common-subexpression elimination then
    chooses the register.

    Rewrite, only when every assignment to the local is `var = EXPR;` immediately
    followed by `FIELD = var;`, or a reload `var = FIELD;`, all for ONE field:
    the pair becomes `FIELD = EXPR;` (or `FIELD += K;` / `FIELD -= K;`), reloads
    are deleted, remaining uses read FIELD, and the declaration goes. A use after
    FIELD changed without a reload changes meaning; the object oracle rejects it.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    masked = c89._mask(source)[begin:stop]
    # File-scope extern scalars count as fields too: releaseRelocatableHeapBlockMetadata
    # matched as `gRelocatableHeapUsedBlockCount--;` (2026-09-13).
    globals_ = sorted(set(re.findall(r"^\s*extern\s+[^;()]*?\b([A-Za-z_]\w*)\s*;", source[:begin], re.M)), key=len, reverse=True)
    field_re = LVALUE + ("|" + "|".join(rf"\b{re.escape(g)}\b(?!\s*(?:->|\.|\[))" for g in globals_) if globals_ else "")
    for decl in re.finditer(r"^[ \t]*(?:s32|u32|s16|u16|s8|u8|int|short|char|f32|f64|float)[ \t]+(?P<name>[A-Za-z_]\w*)"
                            r"[ \t]*;[ \t]*\n", masked, re.M):
        name = decl.group("name")
        pairs = list(re.finditer(rf"^(?P<i>[ \t]*){name}[ \t]*=[ \t]*(?P<expr>[^;\n]+);[ \t]*\n"
                                 rf"[ \t]*(?P<field>{field_re})[ \t]*=[ \t]*{name}[ \t]*;[ \t]*\n", masked, re.M))
        if not pairs:
            continue
        field = _field_key(pairs[0].group("field"))
        if any(_field_key(p.group("field")) != field for p in pairs) or CALL.search(pairs[0].group("field")):
            continue                                   # one field, and reading it twice must not call anything
        reloads = [m for m in re.finditer(rf"^[ \t]*{name}[ \t]*=[ \t]*(?P<field>{field_re})[ \t]*;[ \t]*\n", masked, re.M)
                   if _field_key(m.group("field")) == field]
        assignments = len(re.findall(rf"\b{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*{name}\b|\b{name}\s*\+\+|"
                                     rf"--\s*{name}\b|\b{name}\s*--|&\s*{name}\b", masked))
        if assignments != len(pairs) + len(reloads):
            continue
        field_text = pairs[0].group("field").strip()
        steps = []
        for pair in pairs:
            step = re.fullmatch(rf"(?P<read>{field_re})\s*(?P<op>[-+])\s*(?P<amount>\S.*)", pair.group("expr").strip())
            steps.append(step if step and _field_key(step.group("read")) == field else None)
        # Reads keep their own spelling: m2c writes `*(s16 *)` but reads `*(u16 *)`,
        # and updateEndingCreditsTommySnowmanEntrance matched only with the READ one.
        read_text = next((step.group("read").strip() for step in steps if step), field_text)
        forms = ["compound", "plain"]
        # Unit steps have two more original spellings, both matched on 2026-09-13:
        # `field++;` then reads of field (randomNextObject), and `++field` inside
        # the first use with m2c's width mask dropped (updateRaceUiSparkle,
        # updateRaceCountdownInitialDelay).
        if all(s and s.group("amount").strip() in ("1", "1U", "1u") for s in steps):
            forms += ["postfix", "prefix_in_use", "prefix_in_use_unmasked"]
        for form in forms:
            edits, prefix_pending = [], form.startswith("prefix")
            for pair, step in zip(pairs, steps):
                indent, expr = pair.group("i"), pair.group("expr").strip()
                if form == "compound" and step:
                    replacement = f"{indent}{read_text} {step.group('op')}= {step.group('amount')};\n"
                elif form == "postfix":
                    replacement = f"{indent}{read_text}{step.group('op') * 2};\n"
                elif prefix_pending and pair is pairs[0]:
                    replacement = ""                    # the step moves into the first use below
                else:
                    replacement = f"{indent}{field_text} = {expr};\n"
                edits.append((pair.start(), pair.end(), replacement))
            edits += [(m.start(), m.end(), "") for m in reloads]
            edits.append((decl.start(), decl.end(), ""))
            text = body
            for start, end, replacement in sorted(edits, reverse=True):
                text = text[:start] + replacement + text[end:]
            if prefix_pending:
                operator = steps[0].group("op") * 2
                use = re.compile(rf"\b{name}\b")
                if not use.search(text, decl.start()):
                    continue
                if form == "prefix_in_use_unmasked":
                    masked_use = re.compile(rf"\(\s*{name}\s*&\s*0x[fF]{{2,4}}\s*\)")
                    found = masked_use.search(text)
                    first = use.search(text)
                    if not found or found.start() > first.start():
                        continue
                    text = text[:found.start()] + f"{operator}{read_text}" + text[found.end():]
                else:
                    first = use.search(text)
                    text = text[:first.start()] + f"({operator}{read_text})" + text[first.end():]
            text = re.sub(rf"\b{name}\b", lambda _m: read_text, text)
            # Two orthogonal refinements, each matched on 2026-09-13:
            #   unmasked  -- m2c's `(field & 0xFFFF)` in later uses goes too
            #                (updateEndingLindaHopRightToPose)
            #   keep_decl -- the now-unused declaration stays, because it still
            #                shifts stack slots (updateEndingSlashStartFinalPose)
            mask = re.compile(rf"\(\s*{re.escape(read_text)}\s*&\s*0x[fF]{{2,4}}\s*\)")
            unmasked = mask.sub(lambda _m: read_text, text)
            declaration = body[decl.start():decl.end()]
            options = [("", text), ("+unmasked", unmasked)] if unmasked != text else [("", text)]
            for suffix, chosen in options:
                for keep in (False, True):
                    final = chosen[:decl.start()] + declaration + chosen[decl.start():] if keep else chosen
                    variant = source[:begin] + final + source[stop:]
                    if variant != source:
                        yield (f"field_local:{name}:{form}{suffix}{'+keep_decl' if keep else ''}", "field_local", variant)


def _statement_ends(text: str):
    """Offsets where a run of whole `...;` statements ends, shortest run first.

    The linear equivalent of matching `(?:[ \\t]*[^;\\n]*;[ \\t]*\\n?)*?` at offset 0.
    As a regex those overlapping quantifiers backtrack exponentially when nothing
    follows: dominant-1 spun for 4.5 hours on initMainMenuSceneModelParts and
    guPerspectiveF (2026-09-14).
    """
    position = 0
    yield position
    while True:
        semicolon, newline = text.find(";", position), text.find("\n", position)
        if semicolon < 0 or 0 <= newline < semicolon:
            return
        position = semicolon + 1
        while position < len(text) and text[position] in " \t":
            position += 1
        if position < len(text) and text[position] == "\n":
            position += 1
        yield position


def load_modify_stores(source: str, function: str):
    """`T v = F; ... v = v OP K; F = v;` (or `v = F;`) becomes an update of F itself.

    Motivating residual: updateRaceUiCourseRecordRevealFinalMoney (2026-09-13),
    written by m2c as `s16 tmp = F; tmp = tmp - 1; F = tmp;`. Once F was updated
    directly (`F = F - 1`, `F -= 1` or `F--`) and a constant store moved ahead of
    it, the object matched. Statements between the load and the update are kept.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    # Statements may share a line: m2c wrote `s16 tmp = F; tmp--; F = tmp;` in
    # updateRaceUiTrickPrizePayoutRevealMakeBonus.
    load = re.compile(rf"^(?P<i>[ \t]*)(?:(?P<type>(?:s32|u32|s16|u16|s8|u8|int|short|char)[ \t]+))?(?P<v>[A-Za-z_]\w*)"
                      rf"[ \t]*=[ \t]*(?P<field>{LVALUE}|\*\([^;=\n]+\)\s*\([^;=\n]+\))[ \t]*;[ \t]*\n?", re.M)
    for match in load.finditer(body):
        name, field_text = match.group("v"), match.group("field").strip()
        if CALL.search(field_text):
            continue
        rest = body[match.end():]
        core = re.compile(rf"[ \t]*(?:{name}[ \t]*=[ \t]*{name}[ \t]*(?P<op>[-+])"
                          rf"[ \t]*(?P<k>[^;\n]+)|{name}[ \t]*(?P<op2>[-+])(?P=op2)|(?P<op3>[-+])(?P=op3)[ \t]*{name}|"
                          rf"{name}[ \t]*(?P<op4>[-+])=[ \t]*(?P<k4>[^;\n]+))[ \t]*;[ \t]*\n?"
                          rf"(?P<store>[ \t]*(?P<target>[^;=\n]+?)[ \t]*=[ \t]*{name}[ \t]*;[ \t]*\n?)")
        update = next((found for found in map(lambda at: core.match(rest, at), _statement_ends(rest)) if found), None)
        if not update or _field_key(update.group("target")) != _field_key(field_text):
            continue
        gap = rest[:update.start()]
        if re.search(rf"\b{name}\b", gap):
            continue
        op = update.group("op") or update.group("op2") or update.group("op3") or update.group("op4")
        amount = (update.group("k") or update.group("k4") or "1").strip()
        update_start = update.start()
        tail = rest[update.end():]
        if re.search(rf"\b{name}\b", tail):
            continue                                   # the local is used elsewhere; not a pure load-modify-store
        declared = match.group("type") is not None
        decl_line = re.search(rf"^[ \t]*(?:s32|u32|s16|u16|s8|u8|int|short|char)[ \t]+{name}[ \t]*;[ \t]*\n", body, re.M)
        if not declared and not decl_line:
            continue
        target, indent = update.group("target").strip(), match.group("i")
        spellings = {"plain": f"{indent}{target} = {field_text} {op} {amount};\n",
                     "compound": f"{indent}{target} {op}= {amount};\n"}
        if amount in ("1", "1U", "1u"):
            # `*(s16 *)(p)--` would step the pointer: a dereference needs its own parentheses.
            grouped = target if not target.startswith("*") else f"({target})"
            spellings["postfix"] = f"{indent}{grouped}{op * 2};\n"
        for form, statement in spellings.items():
            new_rest = rest[:update_start] + statement + tail
            text = body[:match.start()] + new_rest
            if not declared:
                text = text.replace(decl_line.group(0), "", 1)
            yield (f"load_modify_store:{name}:{form}", "load_modify_store", source[:begin] + text + source[stop:])


def rotated_loops(source: str, function: str):
    """`if (C) { for (;;) { BODY; if (!(C)) break; } }` back to `while (C) { BODY; }`.

    IDO compiles a `while` as a guarded bottom-test loop, and m2c writes that
    rotation out literally. The rotated spelling numbers webs differently.
    Motivating residual: _collectPVoices (2026-09-13), where s1/s2 were swapped.
    The unrotated `while (var != NULL)` matched exactly.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(
        r"^(?P<i>[ \t]*)if \((?P<cond>[^\n]+)\) \{\n"
        r"(?P=i)[ \t]+for \(;;\) \{\n"
        r"(?P<body>(?:(?!(?P=i)[ \t]+\}\n)[^\n]*\n)*?)"
        r"(?:[ \t]*\n)*"
        r"(?P<j>[ \t]+)if \(!\((?P<cond2>[^\n]+)\)\) break;\n"
        r"(?P=i)[ \t]+\}\n"
        r"(?P=i)\}\n", re.M)
    for match in pattern.finditer(body):
        if re.sub(r"\s+", "", match.group("cond")) != re.sub(r"\s+", "", match.group("cond2")):
            continue
        lines = [line for line in match.group("body").split("\n") if line.strip()]
        inner = match.group("i") + "    "
        dedented = []
        for line in lines:
            stripped = line.lstrip()
            depth = (len(line) - len(stripped)) - len(match.group("i")) - 8
            dedented.append(inner + " " * max(depth, 0) + stripped)
        replacement = f"{match.group('i')}while ({match.group('cond')}) {{\n" + "\n".join(dedented) + f"\n{match.group('i')}}}\n"
        yield (f"rotated_loop@{match.start()}", "rotated_loop",
               source[:begin] + body[:match.start()] + replacement + body[match.end():] + source[stop:])


POINTER_OR_INT = r"(?:void|char|s32|u32|s16|u16|s8|u8|int)\s*\**"


def readonly_field_local_inlines(source: str, function: str):
    """Replace a local that holds a never-written field with the field at every use.

    Part of the Fdrums match (2026-09-13): `temp_a2 = F54;` read twice gave the
    load a user web ranked ahead of the stored sum (v1 against the target's a2).
    Reading F54 directly lets uopt's common-subexpression web take the lower
    priority. Declines when the field is written, or anything called, after the load.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    for decl in re.finditer(rf"^[ \t]*(?P<type>{POINTER_OR_INT})[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*;[ \t]*\n", body, re.M):
        name = decl.group("name")
        assigns = list(re.finditer(rf"^[ \t]*{name}[ \t]*=[ \t]*(?P<field>{LVALUE}|\(\*\(void \*\*\)[^;\n]+\));[ \t]*\n", body, re.M))
        if len(assigns) != 1 or CALL.search(assigns[0].group("field")):
            continue
        field_text = assigns[0].group("field").strip()
        after = body[assigns[0].end():]
        writes = len(re.findall(rf"\b{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*{name}\b|\b{name}\s*\+\+|&\s*{name}\b", body))
        field_written = re.search(re.escape(field_text) + r"\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)", after)
        if writes != 1 or field_written or CALL.search(after):
            continue
        uses = len(re.findall(rf"\b{name}\b", after))
        if uses < 1:
            continue
        text = body[:assigns[0].start()] + re.sub(rf"\b{name}\b", lambda _m: field_text, after)
        text = text[:decl.start()] + text[decl.end():]
        yield (f"readonly_field_local:{name}", "readonly_field_local", source[:begin] + text + source[stop:])


def store_value_locals(source: str, function: str, limit: int = 12):
    """Hold a cast store value in a new local: `LVAL = (T *)(EXPR);` -> `T *v; v = (T *)(EXPR); LVAL = v;`.

    The other half of the Fdrums match (2026-09-13): the stored sum belongs in a
    uopt-coloured user web (v1), not a ugen temporary.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    first_statement = re.search(r"^[ \t]*(?!(?:s32|u32|s16|u16|s8|u8|int|short|char|void|f32|f64|struct|union|unsigned|signed)\b)"
                                r"[^\s{}][^\n]*\n", body, re.M)
    count = 0
    for match in re.finditer(rf"^(?P<i>[ \t]*)(?P<lval>{LVALUE}|\(\*\([^;=\n]+\)\s*\([^;=\n]+\)\s*\))[ \t]*=[ \t]*"
                             rf"\((?P<type>{POINTER_OR_INT})\)\s*(?P<expr>\([^;\n]+\));[ \t]*\n", body, re.M):
        if not first_statement or match.start() < first_statement.start():
            continue
        name = f"regalloc_value{count}"
        declaration = f"{match.group('i')}{match.group('type').strip()} {name};\n"
        replacement = (f"{match.group('i')}{name} = ({match.group('type').strip()}) {match.group('expr')};\n"
                       f"{match.group('i')}{match.group('lval').strip()} = {name};\n")
        text = body[:match.start()] + replacement + body[match.end():]
        text = text[:first_statement.start()] + declaration + text[first_statement.start():]
        yield (f"store_value_local@{match.start()}", "store_value_local", source[:begin] + text + source[stop:])
        count += 1
        if count >= limit:
            return


CONSTANT = r"-?(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*"


def constant_store_locals(source: str, function: str, limit: int = 12):
    """Store the local that already holds a constant: `v = K; F = K;` -> `v = K; F = v;`.

    Also the other order, `F = K; v = K;` -> `v = K; F = v;`. `v` must be a local declared in the body.

    Motivating residual: the AerialTrick family (updateRacePlayerMode16AerialTrick and 17 siblings,
    2026-09-15 progress census). The clamp `if (t >= 0x401) { var_v0 = 0x400; player->stateTimer = 0x400; }`
    compiles the second constant into its own register and hoists that `li` into an earlier delay slot
    (target `nop`, candidate `li t1,0x400`). The target stores `var_v0`. The edit leaves the register
    gradient unchanged, so the search could not reach it: from the m2c source the search stalled at
    (1, 1, 1) after 300 compiles; after the edit it was object-exact in 83. The edit is an ENABLER, not a
    fix: the exact source drops `var_v0` altogether, via a `field_local` elimination that only fires once the
    store names the local. So it is not a beam family (a gradient-neutral candidate never wins a slot); see
    `enabling_variants` and `regalloc_search.search`.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    locals_ = {m.group("name") for m in re.finditer(
        r"^[ \t]*(?:(?:unsigned|signed|register)\s+)*(?:s32|u32|s16|u16|s8|u8|int|short|char|long|[A-Z]\w*)"
        r"[ \t]*\**[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*;[ \t]*$", body, re.M)}
    pair = re.compile(rf"^(?P<i>[ \t]*)(?P<first>[^;={{}}\n]*[^;={{}}\s])[ \t]*=[ \t]*(?P<k>{CONSTANT})[ \t]*;[ \t]*\n"
                      rf"(?P=i)(?P<second>[^;={{}}\n]*[^;={{}}\s])[ \t]*=[ \t]*(?P=k)[ \t]*;[ \t]*$", re.M)
    count = 0
    position = 0
    while count < limit:
        match = pair.search(body, position)
        if match is None:
            return
        position = match.start() + 1
        first, second, indent, constant = match.group("first"), match.group("second"), match.group("i"), match.group("k")
        if first == second:
            continue
        if first in locals_:
            local, other = first, second
        elif second in locals_:
            local, other = second, first
        else:
            continue
        text = f"{indent}{local} = {constant};\n{indent}{other} = {local};"
        yield (f"const_store_local:{local}@{match.start()}", "const_store_local",
               source[:begin] + body[:match.start()] + text + body[match.end():] + source[stop:])
        count += 1


SELF_UPDATE = re.compile(
    r"^(?P<i>[ \t]*)(?P<lval>[^=;\n]+?)[ \t]*=[ \t]*(?:\((?:s32|u32|s16|u16|s8|u8|int|f32|f64)\)[ \t]*)?"
    r"\((?P=lval)[ \t]*(?P<op>[-+|&^*])[ \t]*(?P<expr>[^;\n]+)\);[ \t]*$", re.M)


def compound_assignments(source: str, function: str):
    """`L = (T) (L op E);` -> `L op= E;` -- every such statement at once, then each alone.

    Motivating residual: updateRacePlayerShockEffect (2026-09-13). Its three
    `arg0->unkNN = (s32) (arg0->unkNN + point[i]);` shifted temporary numbering;
    swapping operands was inert, and only `arg0->unkNN += point[i];` matched.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    matches = [m for m in SELF_UPDATE.finditer(body) if not CALL.search(m.group("lval"))]
    def rewrite(selected):
        text = body
        for m in sorted(selected, key=lambda m: m.start(), reverse=True):
            text = text[:m.start()] + f"{m.group('i')}{m.group('lval').strip()} {m.group('op')}= {m.group('expr').strip()};" + text[m.end():]
        return source[:begin] + text + source[stop:]
    if len(matches) > 1:
        yield ("compound_assign:all", "compound_assign", rewrite(matches))
    for m in matches:
        yield (f"compound_assign@{m.start()}", "compound_assign", rewrite([m]))


def self_update_temps(source: str, function: str):
    """`v = F; F = (T) (v op E(v));` -> `F op= E(F);` -- all at once, then each alone.

    Motivating residual: updateRaceCameraFixedPositionFollow (2026-09-13), three
    `temp = F; F = temp + ((X - temp) >> 1);` whose outer addu operands came out
    reversed. `F += (X - F) >> 1;` matched; the plain spelling did not.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(r"^(?P<i>[ \t]*)(?P<v>[A-Za-z_]\w*)[ \t]*=[ \t]*(?P<field>[^=;\n]+?);[ \t]*\n"
                         r"[ \t]*(?P<lval>[^=;\n]+?)[ \t]*=[ \t]*(?:\((?:s32|u32|s16|u16|s8|u8|int)\)[ \t]*)?"
                         r"\((?P=v)[ \t]*(?P<op>[-+|&^])[ \t]*(?P<expr>[^;\n]+)\);[ \t]*\n", re.M)
    found = [m for m in pattern.finditer(body)
             if _field_key(m.group("field")) == _field_key(m.group("lval")) and not CALL.search(m.group("field"))]
    names = {m.group("v") for m in found}
    def rewrite(selected):
        text = body
        for m in sorted(selected, key=lambda m: m.start(), reverse=True):
            field_text = m.group("lval").strip()
            expr = re.sub(rf"\b{m.group('v')}\b", lambda _x: field_text, m.group("expr").strip())
            if expr.startswith("(") and expr.endswith(")") and expr.count("(") == 1:
                expr = expr[1:-1]
            text = text[:m.start()] + f"{m.group('i')}{field_text} {m.group('op')}= {expr};\n" + text[m.end():]
        for name in names:
            if not re.search(rf"\b{name}\b", re.sub(rf"^[ \t]*[\w\s\*]+\b{name}[ \t]*;[ \t]*\n", "", text, flags=re.M)):
                text = re.sub(rf"^[ \t]*(?:s32|u32|s16|u16|s8|u8|int)[ \t]+{name}[ \t]*;[ \t]*\n", "", text, count=1, flags=re.M)
        return source[:begin] + text + source[stop:]
    if len(found) > 1:
        yield ("self_update:all", "self_update", rewrite(found))
    for m in found:
        yield (f"self_update:{m.group('v')}", "self_update", rewrite([m]))


ELEMENT_TYPES = {1: ("s8", "u8"), 2: ("s16", "u16"), 4: ("s32", "u32")}


def typed_index_scales(source: str, function: str):
    """`(I * K) + TABLE` over a byte table -> `(void *)&((T *)TABLE)[I * (K / sizeof T)]`.

    m2c writes an indexed element address as byte arithmetic. Scaling by a typed
    element costs IDO a different temporary sequence. Motivating residual:
    initTitleMenuSparkle (2026-09-13), every temporary one higher in the target;
    `&((s16 *)titleMenuSparklePositions)[I * 2]` matched. Every element size that
    divides K is proposed.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(r"\((?P<index>\(\*\([^;=\n]+?\)\)|[A-Za-z_][\w>\-.\[\]]*)\s*\*\s*(?P<k>\d+)\)\s*\+\s*(?P<table>[A-Za-z_]\w*)\b(?!\s*[\[(])")
    count = 0
    for match in pattern.finditer(body):
        k = int(match.group("k"))
        for size, types in ELEMENT_TYPES.items():
            if k % size:
                continue
            scale = k // size
            index = match.group("index") if scale == 1 else f"{match.group('index')} * {scale}"
            for element in types:
                replacement = f"(void *)&(({element} *){match.group('table')})[{index}]"
                text = body[:match.start()] + replacement + body[match.end():]
                yield (f"typed_index:{element}x{scale}@{match.start()}", "typed_index", source[:begin] + text + source[stop:])
                count += 1
        if count >= 30:
            return
    # Read form: `*((T *)((u8 *)&TABLE + ((A * 2) + (B * 8))))` -> `((T *)&TABLE)[A + (B * 4)]`.
    # initRaceUiPrizePayout (2026-09-13) matched as `((s16 *)&table)[rank + (course * 4)]`.
    sizes = {"s8": 1, "u8": 1, "s16": 2, "u16": 2, "s32": 4, "u32": 4}
    read = re.compile(r"\*\(\((?P<type>[su](?:8|16|32)) \*\)\(\(u8 \*\)(?P<amp>&?)(?P<table>[A-Za-z_]\w*) \+ \((?P<sum>[^;\n]+)\)\)\)")
    for match in read.finditer(body):
        size = sizes[match.group("type")]
        terms, depth, current = [], 0, ""
        for char in match.group("sum"):
            depth += (char == "(") - (char == ")")
            if char == "+" and depth == 0:
                terms.append(current.strip())
                current = ""
            else:
                current += char
        terms.append(current.strip())
        scaled = []
        for term in terms:
            inner = term[1:-1].strip() if term.startswith("(") and term.endswith(")") else term
            factor = re.fullmatch(r"(?P<x>.+?)\s*\*\s*(?P<k>\d+)", inner)
            k = int(factor.group("k")) if factor else 1
            if k % size:
                break
            x = factor.group("x").strip() if factor else inner
            scaled.append(x if k == size else f"({x} * {k // size})")
        else:
            index = " + ".join(scaled)
            replacement = f"(({match.group('type')} *){match.group('amp')}{match.group('table')})[{index}]"
            text = body[:match.start()] + replacement + body[match.end():]
            yield (f"typed_index_read:{match.group('type')}@{match.start()}", "typed_index", source[:begin] + text + source[stop:])


def symbol_scale_fixes(source: str, function: str):
    """Undo m2c's double scaling of `&SYM + (E * K)` when SYM is a typed extern.

    `extern u16 D_800D5FF4;` then `*(&D_800D5FF4 + (i * 8))` scales by 16 bytes: C
    pointer arithmetic multiplies by sizeof(u16) again, although the binary steps 8
    bytes (`sll t,t,3`). That is a behaviour difference, not only a register one.
    Motivating residual: initRaceCourseScrollingTexture (2026-09-13), matched once
    both such sites stepped 8 bytes. Proposes, for all sites together, typed
    indexing `((T *)&SYM)[E * (K / sizeof T)]` and byte arithmetic `(u8 *)&SYM + E * K`.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    sizes = {"s8": 1, "u8": 1, "s16": 2, "u16": 2, "s32": 4, "u32": 4, "f32": 4}
    declared = {name: kind for kind, name in re.findall(
        r"^\s*extern\s+(s8|u8|s16|u16|s32|u32|f32)\s+([A-Za-z_]\w*)\s*;", source[:begin], re.M)}

    def close(text, opening):
        depth = 0
        for index in range(opening, len(text)):
            depth += (text[index] == "(") - (text[index] == ")")
            if depth == 0:
                return index
        return -1

    def scaled(group):
        """`(E * K)` with the `*` at top level -> (E, K); nesting of E is unrestricted."""
        inner, depth = group[1:-1], 0
        for index in range(len(inner) - 1, -1, -1):
            depth += (inner[index] == ")") - (inner[index] == "(")
            if depth == 0 and inner[index] == "*":
                k = inner[index + 1:].strip().rstrip("uUlL")
                # Hex scales too: initCourseBillboardMarker multiplies by 0x14 (2026-09-13).
                return (inner[:index].strip(), int(k, 0)) if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", k) else None
        return None

    sites = []                                        # (start, end, name, kind, E, K, deref)
    for name, kind in declared.items():
        if sizes[kind] == 1:
            continue
        for found in re.finditer(rf"&{re.escape(name)}\b", body):
            after = re.match(r"\s*\+\s*\(", body[found.end():])
            if after:
                opening = found.end() + after.end() - 1
                end = close(body, opening)
                pair = scaled(body[opening:end + 1]) if end > 0 else None
                if pair:
                    before = re.search(r"\*\(\s*$", body[:found.start()])
                    trailer = re.match(r"\s*\)", body[end + 1:])
                    if before and trailer:
                        sites.append((before.start(), end + 1 + trailer.end(), name, kind, *pair, True))
                    else:
                        sites.append((found.start(), end + 1, name, kind, *pair, False))
                continue
            prior = re.search(r"\)\s*\+\s*$", body[:found.start()])
            if prior:
                closing = prior.start()
                depth, opening = 0, -1
                for index in range(closing, -1, -1):
                    depth += (body[index] == ")") - (body[index] == "(")
                    if depth == 0:
                        opening = index
                        break
                pair = scaled(body[opening:closing + 1]) if opening >= 0 else None
                if pair:
                    sites.append((opening, found.end(), name, kind, *pair, False))
    if not sites:
        return
    def rewrite(style):
        text = body
        for start, end, name, kind, e, k, deref in sorted(sites, key=lambda s: s[0], reverse=True):
            size = sizes[kind]
            if style == "typed":
                if k % size:
                    return None
                index = e if k == size else f"{e} * {k // size}"
                new = f"(({kind} *)&{name})[{index}]" if deref else f"(void *)&(({kind} *)&{name})[{index}]"
            else:
                new = f"*({kind} *)((u8 *)&{name} + ({e} * {k}))" if deref else f"(void *)((u8 *)&{name} + ({e} * {k}))"
            text = text[:start] + new + text[end:]
        return source[:begin] + text + source[stop:]
    for style in ("typed", "bytes"):
        variant = rewrite(style)
        if variant and variant != source:
            yield (f"symbol_scale:{style}", "symbol_scale", variant)


def negative_scale_splits(source: str, function: str):
    """`(E * -K)` -> `(-E * K)` and `-(E * K)`.

    m2c folds a negation into the constant multiplier, which moves IDO's
    negation from a ugen temporary into `at`. Motivating residual:
    updateRaceCameraIntroPan (2026-09-13); `(-fixedSine(x) * 0xC00)` matched.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(r"\((?P<e>[^()]*?(?:\([^()]*\))?)\s*\*\s*-(?P<k>0[xX][0-9a-fA-F]+|\d+)\)")
    for match in pattern.finditer(body):
        e = match.group("e").strip()
        if not e:
            continue
        for form, text in (("negate_operand", f"(-{e} * {match.group('k')})"), ("negate_product", f"-({e} * {match.group('k')})")):
            variant = body[:match.start()] + text + body[match.end():]
            yield (f"negative_scale:{form}@{match.start()}", "negative_scale", source[:begin] + variant + source[stop:])


def result_local_reuses(source: str, function: str):
    """`return A op (T) (E(v));` -> `v = E(v); return A op v;` for a local v read in E.

    The value is then coloured in v's register. Motivating residual: _doModFunc
    (2026-09-13), where `var_f2 = var_f2 - 1.0; return F1C * var_f2;` matched and
    the single-expression return did not.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    locals_ = re.findall(r"^[ \t]*(?:f32|f64|s32|u32|s16|u16|s8|u8|int|float|double)[ \t]+([A-Za-z_]\w*)[ \t]*;", body, re.M)
    pattern = re.compile(r"^(?P<i>[ \t]*)return (?P<a>[^;\n]+?) (?P<op>[*+])\s*\((?:f32|f64|s32|u32|float|double)\)\s*"
                         r"(?P<e>\((?:[^()]|\([^()]*\))*\));[ \t]*$", re.M)
    for match in pattern.finditer(body):
        for name in locals_:
            if not re.search(rf"\b{name}\b", match.group("e")):
                continue
            expr = match.group("e")[1:-1].strip()
            expr = re.sub(rf"\((?:f64|f32|double|float)\)\s*{name}\b", name, expr)
            replacement = f"{match.group('i')}{name} = {expr};\n{match.group('i')}return {match.group('a')} {match.group('op')} {name};"
            yield (f"result_local:{name}", "result_local",
                   source[:begin] + body[:match.start()] + replacement + body[match.end():] + source[stop:])


def single_use_local_inlines(source: str, function: str):
    """Inline a local assigned once (no call in the value) and read once; also with its width mask dropped.

    randomNextObject (2026-09-13) matched only as `field++; return table[field];`:
    after the field local went, m2c's `temp_idx = field & 0xFF;` still had to
    disappear, and the `& 0xFF` with it (the u8 load already zero-extends).
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    masked = c89._mask(source)[begin:stop]
    for decl in re.finditer(r"^[ \t]*(?:s32|u32|s16|u16|s8|u8|int|short|char)[ \t]+(?P<name>[A-Za-z_]\w*)[ \t]*;[ \t]*\n",
                            masked, re.M):
        name = decl.group("name")
        assigns = list(re.finditer(rf"^[ \t]*{name}[ \t]*=[ \t]*(?P<value>[^;\n]+);[ \t]*\n", masked, re.M))
        writes = len(re.findall(rf"\b{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*{name}\b|\b{name}\s*\+\+|"
                                rf"--\s*{name}\b|\b{name}\s*--|&\s*{name}\b", masked))
        reads = len(re.findall(rf"\b{name}\b", masked)) - 1 - writes
        if len(assigns) != 1 or writes != 1 or reads != 1 or CALL.search(assigns[0].group("value")):
            continue
        value = body[assigns[0].start("value"):assigns[0].end("value")].strip()
        values = [("inline", f"({value})")]
        unmasked = re.fullmatch(r"(.+?)\s*&\s*0x[fF]{2,4}", value)
        if unmasked:
            values.append(("inline_unmasked", f"({unmasked.group(1).strip()})"))
        for form, replacement in values:
            text = body
            for start, end in sorted([(decl.start(), decl.end()), (assigns[0].start(), assigns[0].end())], reverse=True):
                text = text[:start] + text[end:]
            text = re.sub(rf"\b{name}\b", lambda _m: replacement, text, count=1)
            yield (f"single_use:{name}:{form}", "single_use", source[:begin] + text + source[stop:])


WORD_COPY = re.compile(
    r"^(?P<i>[ \t]*)\(\*\(s32 \*\)\(\(u8 \*\)\((?P<dst>[^()]+)\) \+ (?P<doff>0x[0-9A-Fa-f]+|\d+)\)\) = "
    r"\(s32\) \(\*\(s32 \*\)\(\(u8 \*\)\((?P<src>[^()]+)\) \+ (?P<soff>0x[0-9A-Fa-f]+|\d+)\)\);[ \t]*$")


def struct_copy_merges(source: str, function: str):
    """Merge consecutive word copies back into one struct assignment.

    Motivating residual: initRaceCourseTripleParticle and its two siblings
    (2026-09-13). The target copies through `at` (`lw at,0(t9)` / `sw at,0x18(a0)`
    / `lw t1,4(t9)` / ...), which is IDO's struct-assignment idiom; m2c had
    emitted three separate s32 copies. `*(Words3 *)(dst + 0x18) = *(Words3 *)(E);`
    with the source pointer expression written inline matched exactly. A file-scope
    typedef of N s32 words is added before the function.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    lines = body.split("\n")
    index = 0
    while index < len(lines):
        first = WORD_COPY.match(lines[index])
        if not first:
            index += 1
            continue
        run = [first]
        while index + len(run) < len(lines):
            nxt = WORD_COPY.match(lines[index + len(run)])
            k = len(run)
            if not (nxt and nxt.group("dst") == first.group("dst") and nxt.group("src") == first.group("src")
                    and int(nxt.group("doff"), 0) == int(first.group("doff"), 0) + 4 * k
                    and int(nxt.group("soff"), 0) == int(first.group("soff"), 0) + 4 * k):
                break
            run.append(nxt)
        if len(run) < 2:
            index += 1
            continue
        words, dst, src = len(run), first.group("dst"), first.group("src").strip()
        typename = f"RegallocWords{words}"
        typedef = f"typedef struct {{ {' '.join(f's32 w{n};' for n in range(words))} }} {typename};\n"
        dest = f"*({typename} *)((u8 *)({dst}) + {first.group('doff')})"
        soff = first.group("soff")
        forms = [("pointer", f"*({typename} *)((u8 *)({src}) + {soff})", None)]
        # The source pointer is often an m2c local assigned once and used only here: inline it.
        assign = re.search(rf"^[ \t]*{re.escape(src)}[ \t]*=[ \t]*(?P<expr>[^;\n]+);[ \t]*$", body, re.M)
        if re.fullmatch(r"[A-Za-z_]\w*", src) and assign and \
                len(re.findall(rf"\b{re.escape(src)}\b", body)) == 2 + words:
            expr = assign.group("expr").strip()
            offset = "" if int(soff, 0) == 0 else f" + {soff}"
            forms.append(("inline", f"*({typename} *)((u8 *)({expr}){offset})", assign))
        for form, value, removed in forms:
            new_lines = lines[:index] + [f"{first.group('i')}{dest} = {value};"] + lines[index + words:]
            text = "\n".join(new_lines)
            if removed is not None:
                text = re.sub(rf"^[ \t]*{re.escape(src)}[ \t]*=[ \t]*[^;\n]+;[ \t]*\n", "", text, count=1, flags=re.M)
                text = re.sub(rf"^[ \t]*[A-Za-z_][\w \t]*\*?[ \t]*\*?{re.escape(src)}[ \t]*;[ \t]*\n", "", text,
                              count=1, flags=re.M)
            header = source[:begin]
            definition = header.rfind("\n", 0, header.rfind(function + "(")) + 1
            yield (f"struct_copy:{words}@{index}:{form}", "struct_copy",
                   header[:definition] + typedef + header[definition:] + text + source[stop:])
        index += words


def store_loops(source: str, function: str):
    """Fold `X[0] = C; X[1] = C; ... X[n-1] = C;` back into a counted loop that IDO unrolls.

    Motivating residual: initRaceTypeSelectOptionIcons (2026-09-13), where the
    constant sat in v0 instead of v1. Only `{ s32 i = 0; while (i < 4) { X[i] = C;
    i++; } }` matched: a `for` loop fixed every register but rotated the unrolled
    store order. Both loop spellings are proposed. do-while is refused by the
    project frontend, so it is not generated.
    """
    begin, stop = _body(source, function)
    lines = source[begin:stop].split("\n")
    pattern = re.compile(r"^(?P<i>[ \t]*)(?P<array>[A-Za-z_][\w>.\-]*)\[(?P<k>\d+)\] = (?P<value>[^;=\n]+);[ \t]*$")
    index = 0
    while index < len(lines):
        first = pattern.match(lines[index])
        if not first or first.group("k") != "0":
            index += 1
            continue
        run = 1
        while index + run < len(lines):
            nxt = pattern.match(lines[index + run])
            if not (nxt and nxt.group("array") == first.group("array") and nxt.group("value") == first.group("value")
                    and int(nxt.group("k")) == run):
                break
            run += 1
        if run >= 2:
            indent, array, value = first.group("i"), first.group("array"), first.group("value")
            inner = indent + "    "
            loops = {
                "while": [f"{indent}{{", f"{inner}s32 i = 0;", f"{inner}while (i < {run}) {{",
                          f"{inner}    {array}[i] = {value};", f"{inner}    i++;", f"{inner}}}", f"{indent}}}"],
                "for": [f"{indent}{{", f"{inner}s32 i;", f"{inner}for (i = 0; i < {run}; i++) {{",
                        f"{inner}    {array}[i] = {value};", f"{inner}}}", f"{indent}}}"],
            }
            for form, block in loops.items():
                text = "\n".join(lines[:index] + block + lines[index + run:])
                yield (f"store_loop:{array}x{run}:{form}", "store_loop", source[:begin] + text + source[stop:])
        index += max(run, 1)


def guard_before_load(source: str, function: str):
    """Test the global itself in an early-return guard, and copy it into the local afterwards.

    Motivating residual: reserveSoundEffectQueueReadIndex (2026-09-13), where the
    address register was a0 instead of a1. m2c wrote `v = G; if (v == H) { return -1; }`;
    the match reads G in the guard and assigns `v = G;` after it. (It also needed
    the `==` operands swapped, which the commutative family proposes next.)
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    # Guard-body lines are `(?P=i)[ \t][^\n]*`: one indentation character, then anything. The
    # earlier `[ \t]+[^\n]*` let both quantifiers claim the same spaces and backtracked
    # exponentially when no closing brace matched (osEPiRawStartDma spun a campaign worker, 2026-09-14).
    pattern = re.compile(r"^(?P<i>[ \t]*)(?P<v>[A-Za-z_]\w*) = (?P<g>[A-Za-z_]\w*);[ \t]*\n"
                         r"(?P<guard>(?P=i)if \((?P<cond>[^\n]*)\) \{\n(?:(?P=i)[ \t][^\n]*\n)*?(?P=i)\}\n)", re.M)
    for match in pattern.finditer(body):
        name, glob = match.group("v"), match.group("g")
        guard = match.group("guard")
        if not re.search(rf"\b{name}\b", match.group("cond")) or re.search(r"\breturn\b", guard) is None:
            continue
        rewritten = guard.replace(match.group("cond"), re.sub(rf"\b{name}\b", glob, match.group("cond")), 1)
        if re.search(rf"\b{name}\b", rewritten):
            continue                                   # the guard body still needs the local
        text = body[:match.start()] + rewritten + f"{match.group('i')}{name} = {glob};\n" + body[match.end():]
        yield (f"guard_before_load:{name}", "guard_before_load", source[:begin] + text + source[stop:])


SIGNEDNESS_FLIP = {"s8": "u8", "u8": "s8", "s16": "u16", "u16": "s16", "s32": "u32", "u32": "s32"}
TYPED_DEREF = r"\(\*\((?P<ty>[su](?:8|16|32)) \*\)\((?P<base>[^;\n]*?)\)\)"


def typed_field_rereads(source: str, function: str):
    """`t = E; (*(T *)(P)) = t; ... t ...` -> `(*(T *)(P)) = E; ... (*(T' *)(P)) ...`, T' = T with flipped signedness.

    Motivating residuals: the five SlideIn popups (updateRaceUiScorePopupSlideIn and siblings, 2026-09-14).
    The uopt trace showed `E` held in a coloured range (v1) where the target keeps it in a ugen
    temporary (t8). uopt forwards a store into a reload of the same type, which recreates the
    shared value; a reload through the other signedness is not forwarded, so `E` stays a ugen
    temporary and as1 removes the reload. All five became object-exact.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    for local in re.finditer(r"^[ \t]*(?:s32|u32|s16|u16|s8|u8)[ \t]+(?P<name>[A-Za-z_]\w*);[ \t]*\n", body, re.M):
        name = local.group("name")
        word = re.compile(rf"\b{re.escape(name)}\b")
        assigns = list(re.finditer(rf"^[ \t]*{re.escape(name)}[ \t]*=[ \t]*(?P<expr>[^;\n]+);[ \t]*\n", body, re.M))
        if len(assigns) != 1:
            continue
        assign = assigns[0]
        after = body[assign.end():]
        store = re.search(rf"^(?P<i>[ \t]*)(?P<lv>{TYPED_DEREF})[ \t]*=[ \t]*(?:\([su](?:8|16|32)\)[ \t]*)?"
                          rf"{re.escape(name)};[ \t]*\n", after, re.M)
        if not store or word.search(after[:store.start()]) or word.search(assign.group("expr")):
            continue                                   # used before the store, or self-referential
        rest = after[store.end():]
        if not word.search(rest):
            continue
        lvalue, ty = store.group("lv"), store.group("ty")
        reread = lvalue.replace(f"({ty} *)", f"({SIGNEDNESS_FLIP[ty]} *)", 1)
        # the declaration and the assignment go; statements between assignment and store stay in place
        between = after[:store.start()]
        text = (body[:local.start()] + body[local.end():assign.start()] + between +
                f"{store.group('i')}{lvalue} = {assign.group('expr').strip()};\n" + word.sub(reread, rest))
        yield (f"typed_reread:{name}", "typed_reread", source[:begin] + text + source[stop:])


def narrow_truth_tests(source: str, function: str):
    """`if (x != 0)` -> `if (x)` and `if (x == 0)` -> `if (!x)` for a narrow (8/16-bit) local `x`.

    Motivating residual: updateCharacterSelectRosterIcons (2026-09-14). The uopt trace showed the
    widened copy of a u8 local live to the end of the function because the final test used it; the
    local outranked it and took v0. Testing the u8 itself shortens the widened copy's range, its
    priority rises above the local's, and the colours swap to the target's. Object-exact.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    narrow = {m.group("name") for m in re.finditer(
        r"^[ \t]*(?:s16|u16|s8|u8)[ \t]+(?P<name>[A-Za-z_]\w*);[ \t]*$", body, re.M)}
    for match in re.finditer(r"\bif[ \t]*\([ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*(?P<op>!=|==)[ \t]*0[uU]?[ \t]*\)", body):
        if match.group("name") not in narrow:
            continue
        test = match.group("name") if match.group("op") == "!=" else f"!{match.group('name')}"
        text = body[:match.start()] + f"if ({test})" + body[match.end():]
        yield (f"truth_test@{match.start()}", "truth_test", source[:begin] + text + source[stop:])


def enabling_variants(source: str, function: str, limit: int = 2):
    """Gradient-neutral edits that unlock other families; the search adds them as extra roots, not beam members."""
    try:
        for index, item in enumerate(constant_store_locals(source, function)):
            if index >= limit:
                return
            yield item
    except (Decline, ValueError):
        return


def existing(source: str, diff: str):
    from solver import rewrites
    for rewrite in rewrites.inline_temporary_rewrites(source, diff):
        yield (f"inline_temp:{rewrite.label}", "inline_temp", rewrite(source))
    for rewrite in rewrites.statement_order_rewrites(source, diff, gate=False):
        yield (f"stmt_order:{rewrite.label}", "stmt_order", rewrite(source))
    # The INVERSE of the lowering the removed `do` ban used to force. It is proposed as one candidate
    # among the others and the oracle decides: on drawRaceSplitscreenSelectOption2Frame the lowered
    # form of the ROM-verified body scores 99.395 where the `do` spelling is byte-exact, so a candidate
    # can be one edit away from exact in a direction no other family proposes. See
    # `solver/rewrites.do_while_restore_rewrites` and `eval/remove_do_ban.py`.
    for rewrite in rewrites.do_while_restore_rewrites(source, diff):
        yield (f"do_restore:{rewrite.label}", "do_restore", rewrite(source))


def variants(source: str, function: str, diff: str = "", prefer: tuple[str, ...] = ()):
    """Every mutation of one source, de-duplicated, stable order, round-robin across families.

    `prefer` names family kinds (e.g. from `solver.uopt_diagnosis.preferred_families`) that go
    first, round-robin in that order, before the remaining families. Empty: the original order.
    """
    families = [(("field_local",), field_local_eliminations(source, function)),
                (("struct_copy",), struct_copy_merges(source, function)),
                (("store_loop",), store_loops(source, function)),
                (("guard_before_load",), guard_before_load(source, function)),
                (("load_modify_store",), load_modify_stores(source, function)),
                (("rotated_loop",), rotated_loops(source, function)),
                (("readonly_field_local",), readonly_field_local_inlines(source, function)),
                (("store_value_local",), store_value_locals(source, function)),
                (("compound_assign",), compound_assignments(source, function)),
                (("self_update",), self_update_temps(source, function)),
                (("typed_index",), typed_index_scales(source, function)),
                (("symbol_scale",), symbol_scale_fixes(source, function)),
                (("negative_scale",), negative_scale_splits(source, function)),
                (("result_local",), result_local_reuses(source, function)),
                (("single_use",), single_use_local_inlines(source, function)),
                (("typed_reread",), typed_field_rereads(source, function)),
                (("truth_test",), narrow_truth_tests(source, function)),
                (("local_type",), local_types(source, function)),
                (("commutative",), commutative_swaps(source, function)),
                (("const_inline",), constant_local_inlines(source, function)),
                (("stmt_move",), statement_moves(source, function)),
                (("decl_order",), declaration_swaps(source, function)),
                (("inline_temp", "stmt_order"), existing(source, diff))]
    rank = {kind: index for index, kind in enumerate(prefer)}
    preferred = sorted((f for f in families if rank.keys() & set(f[0])),
                       key=lambda f: min(rank[k] for k in f[0] if k in rank))
    ordered = [preferred, [f for f in families if not rank.keys() & set(f[0])]]
    seen = {source}
    for tier in ordered:
        tier = [generator for _kinds, generator in tier]
        while tier:
            for family in list(tier):
                try:
                    label, kind, variant = next(family)
                except StopIteration:
                    tier.remove(family)
                    continue
                except (Decline, ValueError):
                    tier.remove(family)
                    continue
                if variant not in seen:
                    seen.add(variant)
                    yield label, kind, variant
