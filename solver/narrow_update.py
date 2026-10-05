"""Bounded increment-and-reread proposals for explicitly narrow memory views.

Compose a stored unsigned byte/halfword increment, later rereads, exact-width
masks and a simple forwarding local. No target names, offsets or source answers
are used. Source-visible guards decline calls, escapes, unknown aliases and
control-flow ambiguity across the rewritten region. Headers are not expanded;
these are hypotheses accepted only by the normal frontend and object oracle.
"""
from __future__ import annotations

import re

from solver import project_headers, regalloc_mutations as rm, scalar_coalesce


def _strip(toks):
    while len(toks) >= 2 and toks[0][1] == '(' and toks[-1][1] == ')':
        depth = 0
        for i, t in enumerate(toks):
            depth += (t[1] == '(') - (t[1] == ')')
            if depth == 0:
                break
        if i != len(toks) - 1:
            break
        toks = toks[1:-1]
    return toks


def _view(toks):
    original = toks
    toks = _strip(toks)
    if len(toks) < 6 or [t[1] for t in toks[:2]] != ['*', '(']:
        return None
    typ = toks[2][1]
    if typ not in {'u8', 'u16', 's8', 's16'} or [t[1] for t in toks[3:5]] != ['*', ')']:
        return None
    address = _strip(toks[5:])
    if any(t[0] == 'ident' and i + 1 < len(address) and address[i + 1][1] == '('
           for i, t in enumerate(address)):
        return None
    # Pure base plus constant byte offset, with optional pointer casts.
    cleaned, i, byte_cast = [], 0, False
    depth = 0
    for t in address:
        depth += (t[1] == '(') - (t[1] == ')')
        if t[1] == '+' and depth != 0:
            return None  # `(u8 *)(p + K)` does not make p's addition byte-based.
    while i < len(address):
        if address[i][1] == '(':
            j = i + 1
            while j < len(address) and address[j][1] != ')':
                j += 1
            words = [t[1] for t in address[i + 1:j]]
            if words and words[-1] == '*' and words[:-1] in [['u8'], ['s8'], ['char'], ['unsigned', 'char'], ['signed', 'char']]:
                byte_cast = True
                i = j + 1
                continue
        if address[i][1] not in {'(', ')'}:
            cleaned.append(address[i])
        i += 1
    if not cleaned or cleaned[0][0] != 'ident' or cleaned[0][1] in rm.TYPE_WORDS:
        return None
    if len(cleaned) not in {1, 3}:
        return None
    if len(cleaned) == 3 and (cleaned[1][1] != '+' or cleaned[2][0] != 'number'):
        return None
    if len(cleaned) == 3 and not byte_cast:
        return None
    try:
        offset = int(re.sub('[uUlL]+$', '', cleaned[2][1]), 0) if len(cleaned) == 3 else 0
    except ValueError:
        return None
    return {'type': typ, 'width': int(typ[1:]), 'key': (cleaned[0][1], offset),
            'start': original[0][2], 'end': original[-1][3]}


def _statements(toks):
    chunks, chunk, path = [], [], []
    for t in toks:
        value = t[1]
        if value == '{':
            chunk = []
            path.append(t[2])
        elif value == '}':
            chunk = []
            if not path:
                raise ValueError('unbalanced body')
            path.pop()
        elif value == ';':
            if chunk:
                chunks.append((chunk, t, tuple(path)))
            chunk = []
        else:
            chunk.append(t)
    if path:
        raise ValueError('unbalanced body')
    return chunks


def _assignment(chunk):
    depth = 0
    for i, t in enumerate(chunk):
        depth += (t[1] in {'(', '['}) - (t[1] in {')', ']'})
        if depth == 0 and t[1] in rm.ASSIGN:
            return chunk[:i], t[1], chunk[i + 1:]
    return None


def _increment(rhs):
    rhs = _strip(rhs)
    if len(rhs) < 3 or rhs[-2][1] != '+' or rhs[-1][1].lower() not in {'1', '1u'}:
        return None
    view = _view(rhs[:-2])
    return view if view and view['type'] in {'u8', 'u16'} else None


def _blank(source, masked, start, end):
    return ''.join(source[i] if masked[i].isspace() else ' ' for i in range(start, end))


def _view_spans(toks):
    stack, spans = [], []
    for i, t in enumerate(toks):
        if t[1] == '(':
            stack.append(i)
        elif t[1] == ')' and stack:
            first = stack.pop()
            if view := _view(toks[first:i + 1]):
                spans.append((view['start'], view['end']))
    return spans


def _masks(source, function):
    """Remove only unsigned masks exactly covering the explicitly read width."""
    begin, stop = rm._body(source, function)
    toks = rm.tokens(project_headers._mask_noncode(source)[begin:stop], begin)
    edits = []
    stack = []
    for i, t in enumerate(toks):
        if t[1] == '(':
            stack.append(i)
        elif t[1] == ')' and stack:
            left = stack.pop()
            group = toks[left + 1:i]
            if len(group) < 3 or group[-2][1] != '&':
                continue
            view = _view(group[:-2])
            try:
                mask = int(re.sub('[uUlL]+$', '', group[-1][1]), 0)
            except ValueError:
                continue
            if view and view['type'] in {'u8', 'u16'} and mask == (1 << view['width']) - 1:
                edits.append((toks[left][2], t[3], source[view['start']:view['end']]))
    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]
    return source


def _signed_masked_updates(source, masked, declarations, toks, chunks):
    """An adjacent signed increment/mask/store can restore the wide local by reread.

    Unlike the ordinary unsigned-local forward, retain the comparison local
    and all its later definitions. Only its adjacent initial value is moved.
    The signed conversion is observed only through its complete-width mask
    and the corresponding unsigned store; other uses decline.
    """
    for index, (chunk, semi, path) in enumerate(chunks[:-2]):
        loaded = _assignment(chunk)
        if not loaded or loaded[1] != '=' or len(loaded[0]) != 1:
            continue
        name = loaded[0][0][1]
        read = _increment(loaded[2])
        if not read or name not in declarations or declarations[name][0] != 's' + read['type'][1:]:
            continue
        masked_chunk, masked_semi, masked_path = chunks[index + 1]
        store_chunk, store_semi, store_path = chunks[index + 2]
        masked_value, stored = _assignment(masked_chunk), _assignment(store_chunk)
        if (masked_path != path or store_path != path
                or masked[semi[3]:masked_chunk[0][2]].strip()
                or masked[masked_semi[3]:store_chunk[0][2]].strip()
                or not masked_value or masked_value[1] != '=' or len(masked_value[0]) != 1
                or not stored or stored[1] != '='):
            continue
        result = masked_value[0][0][1]
        mask_rhs = _strip(masked_value[2])
        store_view = _view(stored[0])
        if (result not in declarations or declarations[result][0] not in {'s32', 'u32'}
                or result in {name, read['key'][0]} or name == read['key'][0]
                or len(mask_rhs) != 3 or mask_rhs[0][1] != name or mask_rhs[1][1] != '&'
                or len(stored[2]) != 1 or stored[2][0][1] != name
                or not store_view or store_view['type'] != read['type'] or store_view['key'] != read['key']):
            continue
        try:
            mask = int(re.sub('[uUlL]+$', '', mask_rhs[2][1]), 0)
        except ValueError:
            continue
        if mask != (1 << read['width']) - 1:
            continue
        expected = {loaded[0][0][2:], mask_rhs[0][2:], stored[2][0][2:]}
        if set(scalar_coalesce._refs(toks, name)) != expected:
            continue
        # Moving the wide assignment past the store is unsafe if that store
        # can alias the wide local through a previously escaped address.
        # _refs declines address-taking, including parenthesized escapes,
        # and requires a standalone first definition of this scalar.
        if not scalar_coalesce._refs(toks, result):
            continue
        view_text = source[read['start']:read['end']]
        indent = re.match(r'[ \t]*', source[source.rfind('\n', 0, store_chunk[0][2]) + 1:])[0]
        declaration = declarations[name]
        edits = [(declaration[1], declaration[2], _blank(source, masked, declaration[1], declaration[2])),
                 (chunk[0][2], semi[3], _blank(source, masked, chunk[0][2], semi[3])),
                 (masked_chunk[0][2], masked_semi[3], _blank(source, masked, masked_chunk[0][2], masked_semi[3])),
                 (store_chunk[0][2], store_semi[3], f'{view_text} += 1;\n{indent}{result} = {view_text};')]
        candidate = source
        for start, stop, text in sorted(edits, reverse=True):
            candidate = candidate[:start] + text + candidate[stop:]
        yield name, result, candidate


def variants(source: str, function: str, limit: int = 4):
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
            declarations[name] = (match['type'], match.start('type'), match.end())
            cursor = match.end()
        toks = rm.tokens(masked[cursor:stop], cursor)
        if (len(toks) > 2048 or any(t[1] in {'for', 'while', 'do', 'goto', 'switch', ':'} for t in toks)
                or scalar_coalesce._later_declaration(toks, scalar_coalesce._visible_types(masked))):
            return
        chunks = _statements(toks)
    except ValueError:
        return
    emitted = 0
    for name, result, candidate in _signed_masked_updates(source, masked, declarations, toks, chunks):
        yield f'narrow_update:masked_signed:{name}:{result}', 'narrow_update', candidate
        emitted += 1
        if emitted >= limit:
            return
    edits, transformed, updated_keys = [], [], set()
    for index, (chunk, semi, path) in enumerate(chunks):
        assignment = _assignment(chunk)
        if not assignment:
            continue
        lhs, op, rhs = assignment
        view = _view(lhs)
        if view and view['type'] in {'u8', 'u16'} and op == '+=' and len(rhs) == 1 and rhs[0][1].lower() in {'1', '1u'}:
            text = source[view['start']:view['end']]
            if text.lstrip().startswith('*'):
                text = f'({text})'
            edits.append((chunk[0][2], semi[3], f'{text}++;'))
            transformed.append('direct')
            updated_keys.add(view['key'])
            continue
        if len(lhs) != 1 or lhs[0][1] not in declarations or op != '=':
            continue
        name = lhs[0][1]
        read = _increment(rhs)
        if not read or declarations[name][0] != read['type']:
            continue
        ref_spans = set(scalar_coalesce._refs(toks, name))
        if not ref_spans:
            continue
        refs = [t for t in toks if t[2:] in ref_spans]
        # One definition and an unconditional store within the same branch.
        stores = []
        for j in range(index + 1, len(chunks)):
            c, s, p = chunks[j]
            a = _assignment(c)
            if a and a[1] == '=' and len(a[2]) == 1 and a[2][0][1] == name:
                stored = _view(a[0])
                if stored and stored['key'] == read['key'] and stored['width'] == read['width'] and p == path:
                    stores.append((j, c, s))
        if len(stores) != 1:
            continue
        j, store, store_semi = stores[0]
        if len([r for r in refs if chunk[0][2] <= r[2] <= store_semi[3]]) != 2:
            continue
        later = [r for r in refs if r[2] > store_semi[3]]
        if not later:
            continue
        last = later[-1][2]
        last_index = next(i for i, t in enumerate(toks) if t[2] == last)
        terminator = next((t[2] for t in toks[last_index:] if t[1] in {';', '{', '}'}), stop)
        region = [t for t in toks if semi[3] <= t[2] < terminator]
        # Intervening stores may only reset a provably disjoint interval of the
        # same base. Between the store and last reread, allow only scalar forwards.
        safe, allowed_assignments = True, {t[2] for t in store if t[1] == '='}
        for k in range(index + 1, len(chunks)):
            c, s, p = chunks[k]
            if c[0][2] >= terminator:
                break
            if k == j:
                continue
            a = _assignment(c)
            if not a:
                continue
            target = _view(a[0])
            if (k < j and target and a[1] == '=' and len(a[2]) == 1 and a[2][0][0] == 'number'
                    and target['key'][0] == read['key'][0]
                    and abs(target['key'][1] - read['key'][1]) >= max(target['width'], read['width']) // 8):
                allowed_assignments.update(t[2] for t in c if t[1] in rm.ASSIGN)
                continue
            if len(a[0]) == 1 and a[0][0][1] in declarations and a[0][0][1] not in {name, read['key'][0]}:
                # A scalar assignment is admitted only at the top level of that
                # expression. Nested writes can overwrite the view or its base.
                if any(t[1] in rm.ASSIGN for t in a[2]):
                    safe = False
                allowed_assignments.add(c[len(a[0])][2])
                continue
            safe = False
        views = _view_spans(region)
        uncovered = [t for t in region if not any(a <= t[2] < b for a, b in views)]
        if (not safe or any(t[1] in rm.ASSIGN and t[2] not in allowed_assignments for t in region)
                or any(t[1] in {'++', '--', 'sizeof'} for t in region)
                or any(t[1] == '&' and n + 1 < len(region) and region[n + 1][1] == name for n, t in enumerate(region))
                or any(t[1] == '(' and n and (uncovered[n - 1][0] == 'ident' and uncovered[n - 1][1] not in {'if', 'return', 'u8', 'u16', 's8', 's16', 'char'}
                                            or uncovered[n - 1][1] in {')', ']'}) for n, t in enumerate(uncovered))):
            continue
        # Do not carry the local across a branch end or a later definition.
        if any(t[1] == '}' for t in region) or any(r[2] < chunk[0][2] for r in refs):
            continue
        text = source[read['start']:read['end']]
        edits.append((chunk[0][2], semi[3], _blank(source, masked, chunk[0][2], semi[3])))
        increment = f'({text})' if text.lstrip().startswith('*') else text
        edits.append((store[0][2], store_semi[3], f'{increment}++;'))
        for r in later:
            edits.append((r[2], r[3], text))
        decl = declarations[name]
        edits.append((decl[1], decl[2], _blank(source, masked, decl[1], decl[2])))
        transformed.append(name)
        updated_keys.add(read['key'])
    if not edits:
        return
    # Avoid unsupported overlapping transformations.
    intervals = sorted((a, b) for a, b, _ in edits)
    if any(b > c for (_a, b), (c, _d) in zip(intervals, intervals[1:])):
        return
    candidate = source
    for start, end, text in sorted(edits, reverse=True):
        candidate = candidate[:start] + text + candidate[end:]
    candidate = _masks(candidate, function)
    # Existing pure-local inlining completes a single-use narrow index forward.
    # Restrict it to a reread of a memory view this proposal already increments.
    rewritten = project_headers._mask_noncode(candidate)
    a, b = rm._body(candidate, function)
    for name in sorted(declarations.keys() - set(transformed)):
        assignments = list(re.finditer(rf'\b{re.escape(name)}\s*=\s*([^;]+);', rewritten[a:b]))
        if len(assignments) != 1:
            continue
        match = assignments[0]
        rhs = rm.tokens(match[1])
        stripped = _strip(rhs)
        if len(stripped) > 2 and stripped[-2][1] == '&':
            stripped = stripped[:-2]
        view = _view(stripped)
        if not view or declarations[name][0] != view['type'] or view['key'] not in updated_keys:
            continue
        # A single read after its definition avoids moving multiple reloads.
        if len(re.findall(rf'\b{re.escape(name)}\b', rewritten[a:b])) != 3:
            continue
        # The next complete statement must be a return containing its sole read.
        # Do not forward through stores, calls, or another control-flow edge.
        tail = rewritten[a + match.end():b].strip()
        if (not tail.startswith('return ') or tail.count(';') != 1
                or re.search(r'\b[A-Za-z_]\w*\s*\(|[)\]]\s*\(|\+\+|--|\bif\b|[{},]', tail)
                or any(t[1] in rm.ASSIGN for t in rm.tokens(tail))):
            continue
        inline = next((v for v in rm.pure_local_inlines(candidate, function) if v[0] == f'pure_inline:{name}'), None)
        if inline:
            candidate = _masks(inline[2], function)
    yield 'narrow_update:' + '+'.join(transformed), 'narrow_update', candidate
