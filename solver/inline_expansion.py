"""Bounded source-local expansion of pure integer return-expression helpers.

This is a compiler search operator, not a semantic certificate. Definitions must
already be in the supplied candidate. No reference C or external helper loading.
One call is changed per variant; ordinary compile/replay/exactness gates remain.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from solver import code_shapes
from solver.principle_variants import Variant

_NAME = re.compile(r'\b[A-Za-z_]\w*\b')
_INTEGER = re.compile(r'(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)(?:[uU][lL]?|[lL][uU]?)?\Z')
_TOKENS = re.compile(r'\s+|[A-Za-z_]\w*|0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*|'
                     r'<<|>>|<=|>=|==|!=|\+\+|--|&&|\|\||->|[^\w\s]')
_BINARY = {'|': 1, '^': 2, '&': 3, '==': 4, '!=': 4, '<': 5, '>': 5,
           '<=': 5, '>=': 5, '<<': 6, '>>': 6, '+': 7, '-': 7, '*': 8, '/': 8, '%': 8}


def _integer_type(text, aliases):
    words = text.split()
    if len(words) == 1 and words[0] in aliases:
        return aliases[words[0]]
    if not words or any(w not in {'signed', 'unsigned', 'char', 'short', 'int', 'long'} for w in words):
        return None
    if any(words.count(w) > 1 for w in set(words)) or {'signed', 'unsigned'} <= set(words):
        return None
    if sum(w in words for w in ('char', 'short', 'long')) > 1 or ('char' in words and 'int' in words):
        return None
    return ' '.join(words)


def _parameters(text, aliases, *, all_integer=True):
    if text.strip() == 'void':
        return {}
    # Empty/K&R parameter lists are deliberately not treated as prototypes.
    if not text.strip():
        return None
    result = {}
    for part in text.split(','):
        match = re.fullmatch(r'\s*(.+?[\s*])([A-Za-z_]\w*)\s*', part)
        if not match or match[2] in result:
            return None
        scalar = _integer_type(match[1], aliases)
        if all_integer and scalar is None:
            return None
        result[match[2]] = scalar
    return result


@dataclass(frozen=True)
class _Function:
    name: str
    spec: str
    parameters: str
    begin: int
    end: int


def _definitions(masked):
    top = bytearray(len(masked))
    depth = 0
    for i, char in enumerate(masked):
        top[i] = depth == 0
        depth += (char == '{') - (char == '}')
        if depth < 0:
            return ()
    if depth:
        return ()
    result = []
    for match in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', masked):
        if not top[match.start()]:
            continue
        close = code_shapes._close(masked, match.end() - 1, '(', ')')
        if close < 0:
            continue
        brace = code_shapes._space(masked, close + 1)
        if brace >= len(masked) or masked[brace] != '{':
            continue
        end = code_shapes._close(masked, brace, '{', '}')
        if end < 0:
            return ()
        start = max(masked.rfind(';', 0, match.start()), masked.rfind('}', 0, match.start())) + 1
        result.append(_Function(match[1], masked[start:match.start()].strip(),
                                masked[match.end():close], brace + 1, end))
    return tuple(result)


def _pure_expression(expression, parameters, aliases):
    """Parse a small integer grammar; reject calls, lvalues and hidden effects."""
    if len(expression) > 512:
        return False
    tokens = [m[0] for m in _TOKENS.finditer(expression) if not m[0].isspace()]
    if not tokens or len(tokens) > 96 or ''.join(tokens) != re.sub(r'\s+', '', expression):
        return False
    position = 0
    uses = Counter()

    def parse(minimum=0):
        nonlocal position
        if position >= len(tokens):
            raise ValueError('missing operand')
        token = tokens[position]
        position += 1
        if token in {'+', '-', '~', '!'}:
            parse(9)
        elif token == '(':
            closing = position
            while closing < len(tokens) and re.fullmatch(r'[A-Za-z_]\w*', tokens[closing]):
                closing += 1
            if (closing < len(tokens) and tokens[closing] == ')'
                    and _integer_type(' '.join(tokens[position:closing]), aliases)):
                position = closing + 1
                parse(9)
            else:
                parse()
                if position >= len(tokens) or tokens[position] != ')':
                    raise ValueError('unbalanced expression')
                position += 1
        elif token in parameters:
            uses[token] += 1
        elif not _INTEGER.fullmatch(token):
            raise ValueError('unsupported expression operand')
        while position < len(tokens) and _BINARY.get(tokens[position], -1) >= minimum:
            precedence = _BINARY[tokens[position]]
            position += 1
            parse(precedence + 1)
    try:
        parse()
    except (ValueError, RecursionError):
        return False
    return position == len(tokens) and uses == Counter({name: 1 for name in parameters})


def candidates(source: str, function: str, max_variants: int = 4) -> tuple[Variant, ...]:
    """Expand one direct helper call per candidate, retaining the helper itself.

    Actual arguments are literals or nonvolatile integer caller parameters. Each
    helper parameter occurs exactly once, and explicit casts preserve parameter
    and result conversions. There are no introduced names that can capture locals.
    Literal include directives are retained; other preprocessing is declined.
    Included macros/types remain unknown: only builtin integer types and local
    parameter/literal bindings are used. Downstream compiler gates remain required.
    """
    if max_variants <= 0 or len(source) > 2_000_000:
        return ()
    masked = code_shapes._mask(source)
    for directive in list(re.finditer(r'(?m)^[ \t]*#\s*include\b[^\n]*', masked)):
        original = source[directive.start():directive.end()]
        if not re.fullmatch(r'[ \t]*#\s*include\s*(?:<[^>\n]+>|"[^"\n]+")\s*', original):
            return ()
        masked = masked[:directive.start()] + ' ' * len(directive[0]) + masked[directive.end():]
    if '#' in masked or re.search(r'\b(?:volatile|__asm__|asm|_Pragma)\b', masked):
        return ()
    aliases = {}
    definitions = _definitions(masked)
    counts = Counter(row.name for row in definitions)
    callers = [row for row in definitions if row.name == function]
    if len(callers) != 1:
        return ()
    caller = callers[0]
    actual_types = _parameters(caller.parameters, aliases, all_integer=False)
    if actual_types is None:
        return ()
    body = masked[caller.begin:caller.end]
    variants = []
    for helper in definitions:
        if helper.name == function or counts[helper.name] != 1 or helper.name in actual_types:
            continue
        return_type = _integer_type(re.sub(r'^static\s+', '', helper.spec), aliases)
        parameters = _parameters(helper.parameters, aliases)
        returned = re.fullmatch(r'\s*return\s+(.+?)\s*;\s*',
                                masked[helper.begin:helper.end], re.S)
        if not return_type or parameters is None or len(parameters) > 8 or not returned:
            continue
        expression = returned[1].strip()
        if not _pure_expression(expression, parameters, aliases):
            continue
        # A caller-local object/prototype with this name may shadow the helper.
        references = list(re.finditer(r'\b' + re.escape(helper.name) + r'\b', body))
        calls = list(re.finditer(r'\b' + re.escape(helper.name) + r'\s*\(', body))
        if not calls or len(references) != len(calls):
            continue
        for call in calls:
            prefix = body[:call.start()].rstrip()
            previous = re.search(r'([A-Za-z_]\w*|->|\.)$', prefix)
            if previous and previous[1] not in {'return', 'sizeof'}:
                continue
            opening = caller.begin + call.end() - 1
            closing = code_shapes._close(masked, opening, '(', ')')
            if closing < 0 or closing >= caller.end:
                continue
            argument_text = masked[opening + 1:closing].strip()
            actuals = [s.strip() for s in argument_text.split(',')] if argument_text else []
            if len(actuals) != len(parameters):
                continue
            substitutions = {}
            valid = True
            for (name, type_text), actual in zip(parameters.items(), actuals):
                if not (actual_types.get(actual) or _INTEGER.fullmatch(actual)
                        or (actual[:1] in {'+', '-'} and _INTEGER.fullmatch(actual[1:].strip()))):
                    valid = False
                    break
                # Local redeclarations can change the scalar binding of a caller
                # parameter; leave those sites for a scope-aware expansion pass.
                if actual in actual_types and re.search(r'\b[A-Za-z_]\w*(?:\s+|\s*\*\s*)'
                                                       + re.escape(actual) + r'\s*(?:[;=,\[])', body):
                    valid = False
                    break
                substitutions[name] = f'(({type_text})({actual}))'
            if not valid:
                continue
            expanded = _NAME.sub(lambda m: substitutions.get(m[0], m[0]), expression)
            replacement = f'(({return_type})({expanded}))'
            start = caller.begin + call.start()
            variants.append(Variant(f'inline-expansion:{helper.name}@{start}',
                                    source[:start] + replacement + source[closing + 1:]))
            if len(variants) >= min(max_variants, 16):
                return tuple(variants)
    return tuple(variants)
