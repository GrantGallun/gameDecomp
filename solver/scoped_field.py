"""Assignment-scoped cached-field elimination proposals.

Keep other definitions of the local and its declaration. Substituted reads retain
the local's conversion. This bounded source grammar declines calls, intervening
memory writes, escapes and ambiguous control flow. Included headers/macros are
not expanded: source-visible guards are not a semantic equivalence proof. Only
the ordinary compiler/frontend/object certificate may accept a candidate.
"""
from __future__ import annotations

import re

from solver import project_headers, regalloc_mutations as rm, scalar_coalesce


def _owned(toks, index, name):
    return toks[index][0] == 'ident' and toks[index][1] == name and (not index or toks[index - 1][1] not in {'.', '->'})


def _escaped(toks, indices):
    for i in indices:
        j = i - 1
        while j >= 0 and toks[j][1] == '(':
            j -= 1
        if j >= 0 and toks[j][1] == '&':
            return True
    return False


def _statement_start(toks, i):
    return not i or toks[i - 1][1] in {';', '{', '}'}


def _barrier(toks, first, last, name, base, member, locals_):
    # Include the full final statement: its assignment executes after the RHS.
    end = next((i for i in range(last, len(toks)) if toks[i][1] == ';'), None)
    if end is None:
        return True
    for i in range(first, end):
        value = toks[i][1]
        if value in {'++', '--', ',', 'sizeof', '&'}:
            return True
        if value in {')', ']'} and toks[i + 1][1] == '(':
            return True       # indirect/parenthesized calls (also declines some casts)
        if (toks[i][0] == 'ident' and toks[i + 1][1] == '('
                and value not in {'if', 'return'}):
            return True
        if value not in rm.ASSIGN:
            continue
        if i and toks[i - 1][0] == 'ident' and (i < 2 or toks[i - 2][1] not in {'.', '->'}):
            lhs = toks[i - 1][1]
            if lhs in locals_ and lhs not in {name, base} and _statement_start(toks, i - 1):
                continue
        # The sole admitted store is the final field assignment containing the
        # last local read. Other fields/pointers may alias, so decline them too.
        if (value == '=' and i >= 3 and [t[1] for t in toks[i - 3:i]] == [base, '->', member]
                and _statement_start(toks, i - 3) and i < last < end
                and not any(t[1] == ';' for t in toks[i + 1:last])):
            continue
        return True
    return False


def variants(source: str, function: str, limit: int = 8):
    """Offer bounded, conversion-preserving edits of one top-level definition.

    Supported regions are acyclic, with leading plain scalar declarations and
    no nested declarations. A later unconditional definition kills the earlier
    value; a conditional definition does not. Top-level means within this
    function body, not at file scope. Declarations are deliberately retained.
    """
    if limit <= 0:
        return
    try:
        begin, stop = rm._body(source, function)
        masked = project_headers._mask_noncode(source)
        if re.search(r'\bvolatile\b', masked) or '#' in masked[begin:stop]:
            return
        cursor, declarations = begin, {}
        while match := scalar_coalesce._DECL.match(masked, cursor, stop):
            name = match['name']
            if name in declarations:
                return
            declarations[name] = ' '.join(match['type'].split())
            cursor = match.end()
        toks = rm.tokens(masked[cursor:stop], cursor)
        typedefs = scalar_coalesce._visible_types(masked)
    except ValueError:
        return
    if not declarations or scalar_coalesce._later_declaration(toks, typedefs):
        return
    if any(t[1] in {'for', 'while', 'do', 'goto', 'switch', ':'} for t in toks):
        return
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+([A-Za-z_]\w*)', masked.replace('\\\n', '')))
    if macros & ({t[1] for t in toks} | set(declarations.values())):
        return
    depths, depth = [], 0
    for t in toks:
        depths.append(depth)
        depth += (t[1] == '{') - (t[1] == '}')
    emitted = 0
    for i in range(len(toks) - 5):
        name, eq, base, arrow, member, semi = [t[1] for t in toks[i:i + 6]]
        typ = declarations.get(name)
        if (not typ or typ in scalar_coalesce._FLOATS or eq != '=' or arrow != '->' or semi != ';'
                or toks[i + 2][0] != 'ident' or toks[i + 4][0] != 'ident'
                or depths[i] != 0 or not _statement_start(toks, i)):
            continue
        refs = [j for j in range(len(toks)) if _owned(toks, j, name)]
        if _escaped(toks, refs):
            continue
        bound = len(toks)
        for j in refs:
            if j <= i + 5 or j + 1 >= len(toks):
                continue
            if depths[j] == 0 and _statement_start(toks, j) and toks[j + 1][1] == '=':
                end = next((k for k in range(j + 2, len(toks)) if toks[k][1] == ';'), len(toks))
                if not any(j < ref < end for ref in refs):
                    bound = j
                    break
        reads = [j for j in refs if i + 5 < j < bound]
        if not reads or _barrier(toks, i + 6, reads[-1], name, base, member, declarations):
            continue
        # Assignments/updates inside a branch cannot be treated as a kill.
        if any((j + 1 < len(toks) and toks[j + 1][1] in rm.ASSIGN | {'++', '--'})
               or (j and toks[j - 1][1] in {'++', '--'}) for j in reads):
            continue
        field = source[toks[i + 2][2]:toks[i + 4][3]]
        replacement = f'(({typ})({field}))'
        start, end = toks[i][2], toks[i + 5][3]
        blank = ''.join(source[k] if masked[k].isspace() else ' ' for k in range(start, end))
        edits = [(start, end, blank), *((toks[j][2], toks[j][3], replacement) for j in reads)]
        candidate = source
        for start, end, text in sorted(edits, reverse=True):
            candidate = candidate[:start] + text + candidate[end:]
        yield f'scoped_field:{name}@{toks[i][2]}:{typ}:preserve-conversion', 'scoped_field', candidate
        emitted += 1
        if emitted >= limit:
            return
