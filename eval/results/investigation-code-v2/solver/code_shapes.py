"""Bounded, deterministic alternative C implementations (no model calls).

These are compiler experiments, not equivalence certificates. The caller must
compile, replay its semantic panel, and use the object oracle for exactness.
Edits are confined to one named function. Each candidate changes one site;
the existing verified frontier composes changes across expansions.
"""
from __future__ import annotations

import re
from itertools import islice, zip_longest
from heapq import nsmallest

from solver.principle_variants import Variant


def _mask(source: str) -> str:
    # One lexer pass: comment markers inside literals are not comments.
    return re.sub(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: re.sub(r'[^\n]', ' ', m[0]), source)


def _close(text: str, start: int, left: str, right: str) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == left:
            depth += 1
        elif text[i] == right:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _space(text: str, start: int) -> int:
    while start < len(text) and text[start].isspace():
        start += 1
    return start


def _body(source: str, function: str):
    masked = _mask(source)
    # Mask directives, including continued lines, before looking for a definition.
    masked = re.sub(r'(?m)^[ \t]*#(?:[^\n]*\\\n)*[^\n]*',
                    lambda m: re.sub(r'[^\n]', ' ', m[0]), masked)
    for match in re.finditer(r'\b' + re.escape(function) + r'\s*\(', masked):
        end = _close(masked, match.end() - 1, '(', ')')
        if end < 0:
            continue
        start = _space(masked, end + 1)
        if start >= len(masked) or masked[start] != '{':
            continue
        end = _close(masked, start, '{', '}')
        if end < 0 or re.search(r'(?m)^\s*#', source[start:end]):
            return None
        return masked, start + 1, end
    return None


def candidates(source: str, function: str, max_variants: int = 24, *,
               priority=None, allow_do_while=True) -> tuple[Variant, ...]:
    """Round-robin code-shape families, stable order, unique source, hard cap.

    Braced control flow only. For-to-while declines continue/goto/labels and
    declaration initializers; moving an increment past continue is unsound.
    Conditions retain their original evaluation count and use logical negation
    rather than complementing comparisons (which would mishandle NaNs).
    """
    if max_variants <= 0:
        return ()
    region = _body(source, function)
    if region is None:
        return ()
    masked, begin, end = region

    def emit(family, start, stop, replacement):
        return Variant(f'code-shape:{family}@{start}',
                       source[:start] + replacement + source[stop:])

    def controls():
        for m in re.finditer(r'\b(for|while|if)\s*\(', masked[begin:end]):
            start = begin + m.start()
            opening = begin + m.end() - 1
            closing = _close(masked, opening, '(', ')')
            if closing < 0:
                continue
            block = _space(masked, closing + 1)
            if block >= end or masked[block] != '{':
                continue
            stop = _close(masked, block, '{', '}')
            if stop < 0:
                continue
            yield m[1], start, opening, closing, block, stop

    def loops():
        for kind, start, opening, closing, block, stop in controls():
            condition = source[opening + 1:closing]
            body = source[block:stop + 1]
            if kind == 'while':
                yield emit('while-for', start, stop + 1,
                           f'for (; {condition};) {body}')
                if allow_do_while:
                    yield emit('while-guarded-do', start, stop + 1,
                               f'{{ if ({condition}) do {body} while ({condition}); }}')
                else:
                    yield emit('while-top-check', start, stop + 1,
                               f'for (;;) {{ if (!({condition})) break; {body} }}')
            elif kind == 'for':
                header = masked[opening + 1:closing]
                # Exactly two top-level separators; reject GNU statement expressions.
                if header.count(';') != 2 or '{' in header:
                    continue
                cuts = [opening + 1] + [opening + 2 + m.start()
                        for m in re.finditer(';', header)]
                init = source[cuts[0]:cuts[1] - 1].strip()
                cond = source[cuts[1]:cuts[2] - 1].strip() or '1'
                step = source[cuts[2]:closing].strip()
                # Only empty or bare-local assignment initializers.
                if init and not re.match(r'^[A-Za-z_]\w*\s*=(?!=)', _mask(init)):
                    continue
                inner = masked[block:stop + 1]
                if re.search(r'\b(?:continue|goto)\b|(?<!:):(?!:)', inner):
                    continue
                yield emit('for-while', start, stop + 1,
                           f'{{ {init + ";" if init else ""} while ({cond}) '
                           f'{{ {body} {step + ";" if step else ""} }} }}')

    def branches():
        for kind, start, opening, closing, block, stop in controls():
            if kind != 'if':
                continue
            after = _space(masked, stop + 1)
            match = re.match(r'else\b', masked[after:end])
            if not match:
                continue
            other = _space(masked, after + match.end())
            if other >= end or masked[other] != '{':
                continue
            finish = _close(masked, other, '{', '}')
            if finish < 0:
                continue
            yield emit('invert-branch', start, finish + 1,
                       f'if (!({source[opening + 1:closing]})) '
                       f'{source[other:finish + 1]} else {source[block:stop + 1]}')

    def expressions():
        # Full return/RHS expressions only: never rewrite declarations, array
        # extents, partial multidimensional subscripts, or unevaluated sizeof.
        atom = r'[A-Za-z_]\w*'
        rx = re.compile(r'\breturn\s+(?P<base>' + atom + r')\s*\[\s*(?P<index>'
                        + atom + r')\s*\]\s*;')
        for m in rx.finditer(masked, begin, end):
            yield emit('index-pointer', m.start(), m.end(),
                       f'return *({m["base"]} + {m["index"]});')
        # Both arms are int boolean literals, avoiding conditional-operator
        # common-type conversions that change signed/unsigned return values.
        rx = re.compile(r'\breturn\s+([^;{}?:]+)\?\s*([01])\s*:\s*([01])\s*;')
        for m in rx.finditer(masked, begin, end):
            cond = source[m.start(1):m.end(1)]
            yield emit('conditional-return', m.start(), m.end(),
                       f'{{ if ({cond}) return {m[2]}; else return {m[3]}; }}')

    def unique():
        seen = {source}
        for batch in zip_longest(loops(), branches(), expressions()):
            for variant in batch:
                if variant is not None and variant.source not in seen:
                    seen.add(variant.source)
                    yield variant

    if priority is None:
        return tuple(islice(unique(), max_variants))
    # Rank before truncating: a late source region must remain reachable even
    # when early loops would otherwise consume the complete compile budget.
    return tuple(nsmallest(max_variants, unique(), key=priority))
