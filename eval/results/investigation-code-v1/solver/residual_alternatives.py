"""Target-backed representation and expression experiments for semantic-pass C.

Some finite semantic panels miss width/signedness differences. These proposals
are repairs supported by target instructions, not claims of universal source
equivalence. Every candidate still requires compilation and semantic replay.
"""
import re
from solver import code_shapes, c89, principle_variants, residual_sites


def representation(source, function, diff, direct=None):
    mapping = residual_sites.source_map(source, function, diff, direct)
    source_lines = source.splitlines(keepends=True)
    mem = re.compile(r'^(lbu|lb|lhu|lh|lw|sb|sh|sw)\s+\w+,\s*(-?(?:0x[0-9a-f]+|\d+))\(')
    widths = {'lb': 8, 'lbu': 8, 'lh': 16, 'lhu': 16, 'lw': 32, 'sb': 8, 'sh': 16, 'sw': 32}
    rows = {r['id']: r for r in mapping['mismatches']}
    targets = [mem.match(r['instruction']) for r in mapping['mismatches'] if r['side'] == 'target']
    targets = [m for m in targets if m]
    masked = code_shapes._mask(source)
    seen = {source}
    out = []

    def add(label, start, stop, replacement):
        changed = source[:start] + replacement + source[stop:]
        if changed not in seen:
            seen.add(changed)
            out.append(principle_variants.Variant(label, changed))

    for site in mapping['sites']:
        if site['evidence'] != 'direct-compiler-line':
            continue
        row = rows[site['mismatch_ids'][0]]
        # A switch selector can be narrower than the value subsequently stored.
        mask = re.match(r'^andi\s+(\w+),(\w+),(?:0xffff|65535)$', row['instruction'])
        if (mask and any(re.search(r'^andi\s+' + mask[1] + ',' + mask[2] + r',(?:0xff|255)$', r['instruction'])
                    for r in mapping['mismatches'] if r['side'] == 'target')):
            line = source_lines[site['start_line'] - 1]
            assignment = re.search(r'\b(\w+)\s*=\s*\w+\s*;', code_shapes._mask(line))
            if not assignment:
                # IDO can attribute a fused mask/copy to the preceding &=.
                # Follow only the immediately adjacent simple copy, not a
                # guessed opcode-family association elsewhere in the function.
                offset = sum(map(len, source_lines[:site['start_line'] - 1]))
                chain = re.match(r'\s*(\w+)\s*&=\s*(?:0xFFFF|65535)\s*;\s*(\w+)\s*=\s*\1\s*;', masked[offset:])
                if chain:
                    assignment = re.match(r'(\w+)', chain[2])
            if assignment:
                declarations = list(re.finditer(r'\bu16\s+' + re.escape(assignment[1]) + r'\s*;', masked))
                if len(declarations) == 1:
                    d = declarations[0]
                    add('target-local-mask:' + assignment[1], d.start(), d.start() + 3, 'u8')
        candidate = mem.match(row['instruction'])
        if not candidate:
            continue
        matching = [m for m in targets if int(m[2], 0) == int(candidate[2], 0)
                    and m[1].startswith('s') == candidate[1].startswith('s')]
        if not matching:
            continue
        number = site['start_line']
        line_start = sum(map(len, source_lines[:number - 1]))
        line = masked[line_start:line_start + len(source_lines[number - 1])]
        target_widths = {widths[m[1]] for m in matching}
        if len(target_widths) == 1 and widths[candidate[1]] not in target_widths:
            width = next(iter(target_widths))
            for member in re.finditer(r'->\s*([A-Za-z_]\w*)', line):
                declarations = list(re.finditer(r'\b([su])(8|16|32)\s+' + re.escape(member[1]) + r'\s*;', masked))
                if len(declarations) == 1:
                    declaration = declarations[0]
                    if int(declaration[2]) == widths[candidate[1]]:
                        add(f'target-field-width:{member[1]}:{width}', declaration.start(2), declaration.end(2), str(width))
        # The unsigned field is already declared; a signed cast is what selected lh/lb.
        if candidate[1] in ('lh', 'lb') and {m[1] for m in matching} == {candidate[1] + 'u'}:
            cast = 's16' if candidate[1] == 'lh' else 's8'
            for m in re.finditer(r'\(' + cast + r'\)\s*(\w+)->(\w+)', line):
                declared = re.search(r'\bu' + cast[1:] + r'\s+' + re.escape(m[2]) + r'\s*;', masked)
                if declared:
                    add(f'target-unsigned-load:{m[2]}', line_start + m.start(), line_start + m.end(), f'{m[1]}->{m[2]}')
    return tuple(out)


def arithmetic_staging(source, function, maximum=16):
    """Preserve arithmetic grouping while changing explicit intermediate lifetimes."""
    region = code_shapes._body(source, function)
    if not region or re.search(r'\b(volatile|goto|asm|__asm__)\b', region[0]):
        return ()
    masked, begin, end = region
    out = []
    name = 'code_shape_stage'
    while re.search(r'\b' + name + r'\b', masked):
        name += '_'
    # Only a cast scalar divided by a float literal: no calls, side effects,
    # mixed-type guessing or reassociation. Preserve each division's rounding.
    floating = r'\(\s*\((f32|float)\)\s*(\w+)\s*/\s*(\d+(?:\.\d*)?[eE]?[+-]?\d*[fF])\s*\)'
    for ret in re.finditer(r'\breturn\s+([^;{}]+);', masked[begin:end]):
        start, stop = begin + ret.start(), begin + ret.end()
        expression = source[begin + ret.start(1):begin + ret.end(1)]
        if re.search(r'\+\+|--|=|\b\w+\s*\(', expression):
            continue
        for m in re.finditer(floating, expression):
            changed = expression[:m.start()] + name + expression[m.end():]
            for qualifier in ('', 'register '):
                replacement = f'{{ {qualifier}{m[1]} {name}; {name} = {m[0]}; return {changed}; }}'
                out.append(principle_variants.Variant('stage-float-division:' + qualifier.strip(),
                    source[:start] + replacement + source[stop:]))
    # q=x/k; r=x%k -> q=x/k; r=x-q*k, for positive signed divisors.
    # Require adjacent statements and all three locals to share signed int type.
    pair = r'\b(\w+)\s*=\s*(\w+)\s*/\s*(0x[0-9a-fA-F]+|[1-9][0-9]*)\s*;\s*(\w+)\s*=\s*\2\s*%\s*\3\s*;'
    for m in re.finditer(pair, masked[begin:end]):
        q, x, divisor, remainder = m.groups()
        if len({q, x, remainder}) != 3 or not 0 < int(divisor, 0) <= 2147483647:
            continue
        declarations = principle_variants._leading_declarations(source[begin:end])[1]
        signed = {d.name: d.type_text.strip() for d in declarations
                  if not d.stars and d.type_text.strip() in ('int', 's32')}
        if any(v not in signed for v in (q, x, remainder)) or len({signed[v] for v in (q, x, remainder)}) != 1:
            continue
        replacement = f'{q} = {x} / {divisor}; {remainder} = {x} - {q} * {divisor};'
        out.append(principle_variants.Variant('quotient-remainder-reuse',
            source[:begin + m.start()] + replacement + source[begin + m.end():]))
    return tuple(out[:maximum])


def masked_parameter_storage(source, function, diff):
    """Expose a target's explicit parameter mask without changing its value.

    Only narrow unsigned parameters whose first body use masks to their
    original width qualify. Update matching local prototypes with the definition.
    """
    region = code_shapes._body(source, function)
    if not region or not re.search(r'(?m)^-\s*sw\s+a[0-3],', diff):
        return ()
    masked, begin, end = region
    out = []
    for m in re.finditer(r'\b(\w+)\s*&=\s*(0xFFFF|0xffff|65535)\s*;', masked[begin:end]):
        name = m[1]
        if re.search(r'\b' + re.escape(name) + r'\b', masked[begin:begin + m.start()]):
            continue
        if not re.search(r'(?m)^-\s*andi\s+.*,(?:0xffff|65535)$', diff):
            continue
        edits = []
        for fn in re.finditer(r'\b' + re.escape(function) + r'\s*\(', masked):
            opening = fn.end() - 1
            closing = code_shapes._close(masked, opening, '(', ')')
            if closing < 0:
                continue
            for param in re.finditer(r'\bu16\s+' + re.escape(name) + r'\b', masked[opening:closing]):
                edits.append(opening + param.start())
        if not edits:
            continue
        changed = source
        for start in reversed(edits):
            changed = changed[:start] + 'u32' + changed[start + 3:]
        out.append(principle_variants.Variant('masked-parameter-storage:' + name, changed))
    return tuple(out)


def independent_pairs(source, variants, maximum=12):
    """Compose disjoint edits against one parent; ordinary replay gates apply."""
    if maximum <= 0:
        return ()
    edits = []
    for variant in variants[:12]:
        e = residual_sites.edit_region(source, variant.source)
        start, stop = e['start'], e['stop']
        if start == stop:
            continue
        replacement_stop = len(variant.source) - (len(source) - stop)
        edits.append((start, stop, variant.source[start:replacement_stop], variant.label))
    out, seen = [], {source}
    for i, left in enumerate(edits):
        for right in edits[i + 1:]:
            a, b = sorted((left, right))
            if a[1] > b[0] or a[3] == b[3]:
                continue
            changed = source[:a[0]] + a[2] + source[a[1]:b[0]] + b[2] + source[b[1]:]
            if changed not in seen:
                out.append(principle_variants.Variant('paired:' + a[3] + '+' + b[3], changed))
                seen.add(changed)
            if len(out) >= maximum:
                return tuple(out)
    return tuple(out)


def expression_lifetimes(source, function, maximum=24):
    region = code_shapes._body(source, function)
    if region is None:
        return ()
    masked, begin, end = region
    body = source[begin:end]
    if re.search(r'\bvolatile\b|\b(goto|asm|__asm__)\b', masked):
        return ()
    out, seen = [], {source}

    def add(label, changed):
        if changed not in seen and len(out) < maximum:
            seen.add(changed)
            out.append(principle_variants.Variant(label, changed))

    # Materialize explicitly typed subexpressions without reassociating floating
    # arithmetic. No calls, mutation, or volatile expressions are reordered.
    for ret in re.finditer(r'\breturn\s+([^;{}]+);', masked[begin:end]):
        start, stop = begin + ret.start(), begin + ret.end()
        expression_start = begin + ret.start(1)
        expression = source[expression_start:begin + ret.end(1)]
        if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b\w+\s*\(', code_shapes._mask(expression)):
            continue
        for cast in re.finditer(r'\((f32|float|s32|u32|int|unsigned int)\)\s*', expression):
            atom_start = cast.end()
            if atom_start >= len(expression):
                continue
            if expression[atom_start] == '(':
                atom_end = code_shapes._close(expression, atom_start, '(', ')') + 1
                if not atom_end:
                    continue
            else:
                atom = re.match(r'[A-Za-z_]\w*(?:->\w+|\.\w+)*', expression[atom_start:])
                if not atom:
                    continue
                atom_end = atom_start + atom.end()
                # Never cut a partial indexed/called expression.
                if expression[atom_end:].lstrip().startswith(('[', '(')):
                    continue
            local = 'code_shape_value'
            while re.search(r'\b' + local + r'\b', masked):
                local += '_'
            value = expression[cast.start():atom_end]
            replacement = expression[:cast.start()] + local + expression[atom_end:]
            changed = source[:start] + f'{{ {cast[1]} {local}; {local} = {value}; return {replacement}; }}' + source[stop:]
            add(f'materialize-cast:{cast[1]}@{expression_start + cast.start()}', changed)

    # Same-typed locals whose lexical use intervals do not overlap. Require
    # the later interval to start with a plain assignment, no address escape,
    # and no loop backedges. These are still verified compiler experiments.
    if not re.search(r'\b(for|while|do)\b', masked[begin:end]):
        prefix, declarations, declarations_end, indent = principle_variants._leading_declarations(body)
        tail = body[declarations_end:]
        masked_tail = code_shapes._mask(tail)
        eligible = [d for d in declarations if not d.initializer and
                    not re.search(r'\b(static|volatile|const)\b', d.type_text)]
        for first in eligible:
            a = list(re.finditer(r'(?<![\w.>])\b' + re.escape(first.name) + r'\b', masked_tail))
            for second in eligible:
                if first is second or (first.type_text, first.stars) != (second.type_text, second.stars):
                    continue
                b = list(re.finditer(r'(?<![\w.>])\b' + re.escape(second.name) + r'\b', masked_tail))
                if not a or not b or a[-1].end() >= b[0].start():
                    continue
                if not re.match(r'\s*=(?!=)', masked_tail[b[0].end():]):
                    continue
                if any(re.search(r'&\s*\b' + re.escape(d.name) + r'\b', masked_tail) for d in (first, second)):
                    continue
                # Nested shadow declarations make identifier-only rewriting ambiguous.
                if re.search(c89.TYPE_WORD + r'\s+\**\s*' + re.escape(second.name) + r'\b', masked_tail):
                    continue
                changed_tail = tail
                for match in reversed(b):
                    changed_tail = changed_tail[:match.start()] + first.name + changed_tail[match.end():]
                rebuilt = prefix + '\n'.join(d.render(indent) for d in declarations if d is not second) + changed_tail
                add(f'coalesce-local:{second.name}-into-{first.name}', source[:begin] + rebuilt + source[end:])
    return tuple(out)
