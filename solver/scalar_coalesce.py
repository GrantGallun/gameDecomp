"""Bounded scalar-local coalescing hypotheses for compiler search.

Textually ordered uses are a proposal heuristic, not a liveness proof. In
particular, merging different integer types can change conversions. Labels
retain both types, and only the normal frontend/object oracle can accept a
candidate. This module reads no target listing, reference source or workspace.
"""
from __future__ import annotations

import re

from solver import project_headers, regalloc_mutations as rm


_INTEGER = r'(?:[su](?:8|16|32|64)|(?:unsigned|signed)(?:\s+(?:char|short|int|long)(?:\s+(?:int|long))*)?|char|short(?:\s+int)?|int|long(?:\s+(?:int|long))*)'
_SCALAR = rf'(?:{_INTEGER}|f32|f64|float|double)'
_DECL = re.compile(rf'\s*(?P<type>{_SCALAR})\s+(?P<name>[A-Za-z_]\w*)\s*;')
_FLOATS = {'f32', 'f64', 'float', 'double'}


def _visible_types(masked):
    # Over-approximate names in typedef declarations, including comma aliases
    # and struct bodies. A false decline is preferable to editing a shadow.
    names = rm._typedefs(masked)
    for match in re.finditer(r'\btypedef\b', masked):
        depth = 0
        for i in range(match.end(), len(masked)):
            depth += (masked[i] == '{') - (masked[i] == '}')
            if masked[i] == ';' and depth == 0:
                names.update(re.findall(r'[A-Za-z_]\w*', masked[match.end():i]))
                break
    return names


def _later_declaration(toks, typedefs):
    """Decline declaration-like statement starts rather than guess at shadows.

Also catches comma and function-pointer declarators. False declines (e.g. a
standalone multiplication) are intentional in this small proposal grammar.
"""
    for i, (kind, value, *_rest) in enumerate(toks[:-1]):
        if i and toks[i - 1][1] not in {';', '{', '}'}:
            continue
        if kind != 'ident' or value in rm.KEYWORDS:
            continue
        after = toks[i + 1]
        if after[0] == 'ident' or after[1] == '*':
            return True
        if after[1] == '(' and (value in rm.TYPE_WORDS | typedefs
                                or (i + 2 < len(toks) and toks[i + 2][1] == '*')):
            return True
    return False


def _refs(toks, name):
    indices = [i for i, t in enumerate(toks) if t[0] == 'ident' and t[1] == name
               and (not i or toks[i - 1][1] not in {'.', '->'})]
    if not indices:
        return []
    for i in indices:
        j = i - 1
        while j >= 0 and toks[j][1] == '(':
            j -= 1
        if j >= 0 and toks[j][1] == '&':
            return []
    # The first use must be a standalone definition, not a read/compound update.
    first = indices[0]
    if first and toks[first - 1][1] not in {';', '{', '}'}:
        return []
    if first + 1 >= len(toks) or toks[first + 1][1] != '=':
        return []
    end = next((i for i in range(first + 2, len(toks)) if toks[i][1] == ';'), len(toks))
    if any(first < i < end for i in indices):
        return []
    return [toks[i][2:] for i in indices]


def variants(source: str, function: str, limit: int = 12):
    """Yield (label, family, source), at most ``limit`` one-pair edits.

Admit leading plain scalar declarations and acyclic bodies with no further
    declarations. Reject visible address escapes and overlapping textual uses.
    Included headers are not expanded; this is not a preprocessor or CFG proof.
    Integer width/sign changes are explicit hypotheses; floats only merge identical types.
"""
    if limit <= 0:
        return
    try:
        begin, stop = rm._body(source, function)
        masked = project_headers._mask_noncode(source)
        if '#' in masked[begin:stop]:
            return
        cursor, declarations = begin, []
        while match := _DECL.match(masked, cursor, stop):
            declarations.append({'name': match['name'], 'type': ' '.join(match['type'].split()),
                                 'start': match.start('type'), 'end': match.end()})
            cursor = match.end()
        if len(declarations) < 2 or len({d['name'] for d in declarations}) != len(declarations):
            return
        toks = rm.tokens(masked[cursor:stop], cursor)
    except ValueError:
        return
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+([A-Za-z_]\w*)', masked.replace('\\\n', '')))
    names = {t[1] for t in toks} | {d['name'] for d in declarations} | {d['type'] for d in declarations}
    if macros & names:
        return
    if (any(t[1] in {'for', 'while', 'do', 'goto', 'switch', ':'} for t in toks)
            or _later_declaration(toks, _visible_types(masked))):
        return
    refs = {d['name']: _refs(toks, d['name']) for d in declarations}
    emitted = 0
    for first in declarations:
        a = refs[first['name']]
        if not a:
            continue
        for second in declarations:
            b = refs[second['name']]
            if not b or a[-1][1] >= b[0][0]:
                continue
            if ({first['type'], second['type']} & _FLOATS) and first['type'] != second['type']:
                continue
            # Blank only declaration code; preserve comments and line positions.
            start, end = second['start'], second['end']
            blank = ''.join(' ' if not masked[i].isspace() else source[i] for i in range(start, end))
            edits = [(start, end, blank), *((s, e, first['name']) for s, e in b)]
            candidate = source
            for s, e, replacement in sorted(edits, reverse=True):
                candidate = candidate[:s] + replacement + candidate[e:]
            label = (f"scalar_coalesce:{second['name']}->{first['name']}:"
                     f"{second['type']}->{first['type']}")
            yield label, 'scalar_coalesce', candidate
            emitted += 1
            if emitted >= limit:
                return
