"""Header-backed call projection hypotheses; compiler certificates adjudicate."""
import hashlib
import re
from pathlib import Path

from solver import buildtypes, dataflow, m2c_byte_view, project_headers, repair_context, type_transaction


WORDS = {'s8', 'u8', 's16', 'u16', 's32', 'u32', 'char', 'short', 'int', 'long',
         'signed char', 'unsigned char', 'signed short', 'unsigned short',
         'signed int', 'unsigned int', 'signed long', 'unsigned long', 'signed', 'unsigned'}


def _word(parameter):
    # Pointer values occupy one o32 word. Unknown scalar typedefs, aggregates,
    # floats and 64-bit integers require separate ABI reconstruction.
    return '*' in parameter or ' '.join(parameter) in WORDS


def _atom(expression):
    expression = expression.strip()
    cast = re.match(r'^\(\s*([\w\s*]+)\s*\)\s*', expression)
    if cast:
        tokens = tuple(re.findall(r'\w+|\*', cast[1]))
        if not _word(tokens) or 'volatile' in tokens:
            return False
        expression = expression[cast.end():]
    return bool(re.fullmatch(r'(?:&\s*)?[A-Za-z_]\w*|-?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*', expression))


def _word_aliases(text):
    """Unanimous textual typedefs only; wide/aggregate/unknown aliases stay out."""
    aliases, blocked = {}, set()
    text = project_headers._mask_noncode(text)
    for start in re.finditer(r'\btypedef\b', text):
        depth, stop = 0, start.end()
        while stop < len(text):
            char = text[stop]
            depth += (char == '{') - (char == '}')
            stop += 1
            if char == ';' and depth == 0:
                break
        declaration = text[start.start():stop]
        simple = re.fullmatch(r'typedef\s+([\w\s*]+?)\s+(\w+)\s*;', declaration)
        callback = re.fullmatch(r'typedef\s+[\w\s*]+\(\s*\*\s*(\w+)\s*\)\s*\([^;{}]*\)\s*;', declaration)
        if simple:
            aliases.setdefault(simple[2], set()).add(tuple(re.findall(r'\w+|\*', simple[1])))
        elif callback:
            aliases.setdefault(callback[1], set()).add(('*',))
        else:
            # Conservatively block all names mentioned by an unsupported
            # declarator, including inactive alternatives and comma aliases.
            blocked.update(re.findall(r'\b[A-Za-z_]\w*\b', declaration))
    known = set()
    for _ in range(8):
        added = {name for name, shapes in aliases.items()
                 if name not in blocked and shapes
                 and all(_word(s) or (len(s) == 1 and s[0] in known) for s in shapes)}
        if added <= known:
            break
        known.update(added)
    return known


def propose(repo, source, function, diagnostics, assembly, *, big_endian_o32=False):
    report = {'source': source, 'changes': [], 'declines': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'scope': 'header-assisted call projection; evaluation retained; not recovered ABI or semantic proof'}
    if not big_endian_o32 or 'too many arguments to function call' not in diagnostics:
        return report
    definition, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    body = masked[definition.end():end - 1]
    if re.search(r'(?m)^\s*#', body):
        return report
    declarations = project_headers._included_declarations(Path(repo), source)
    header_paths = set()
    for include in buildtypes.INCLUDE_RE.findall(source):
        header_paths.update(buildtypes.closure(Path(repo), 'include/' + include))
    macro_text = source + '\n' + '\n'.join(p.read_text(errors='replace') for p in sorted(header_paths))
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+(\w+)', project_headers._mask_comments(macro_text)))
    word_aliases = _word_aliases(macro_text)
    flow = dataflow.analyse(assembly)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = {}
    pattern = re.compile(r'^candidate\.c:(\d+):(\d+): error: too many arguments to function call, '
                         r'expected (single argument(?: \'[^\']*\')?|[0-9]+), have ([0-9]+)', re.M)
    for diagnostic in pattern.finditer(diagnostics):
        number, column, expected, have = diagnostic.groups()
        number, column, have = int(number), int(column), int(have)
        expected = 1 if expected.startswith('single') else int(expected)
        if not (1 <= number <= len(lines)) or not 0 <= expected < have <= 4:
            continue
        line = lines[number-1].rstrip('\r\n')
        # The diagnostic may end with "arguments"; bind to its next source line.
        following = diagnostics[diagnostic.end():]
        excerpt = re.match(r'[^\n]*\n\s*' + str(number) + r' \| (.*)', following)
        if not excerpt or excerpt[1] != line or '\t' in line:
            report['declines'].append('diagnostic source line missing, stale, or tab-expanded')
            continue
        at = offsets[number-1]
        position = at + column - 1
        if not definition.end() <= position < end - 1:
            continue
        choices = []
        for call in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', masked[at:offsets[number]]):
            start, opening = at + call.start(), at + call.end() - 1
            try:
                closing = m2c_byte_view.closing(masked, opening)
            except ValueError:
                continue
            if not opening < position <= closing or closing >= offsets[number]:
                continue
            name = call[1]
            if name in macros or masked[:start].rstrip().endswith(('.', '->')):
                continue
            protos = sorted(set(declarations.get(name, [])))
            shapes = [type_transaction.signature(p, name) for p in protos]
            if not shapes or None in shapes or len(set(shapes)) != 1:
                continue
            parameters = shapes[0][1]
            if len(parameters) != expected or not all(_word(p) or (len(p) == 1 and p[0] in word_aliases) for p in parameters):
                continue
            if re.search(r'\b' + re.escape(name) + r'\b', definition[2]):
                continue
            if re.search(r'(?m)^\s*[\w\s*]+\b' + re.escape(name) + r'\s*[;=\[]', body):
                continue
            args = m2c_byte_view.arguments(source[opening+1:closing])
            if len(args) != have or not all(_atom(a) for a in args[expected:]):
                continue
            witnesses = [i.index for i in flow.graph.instructions if i.opcode == 'jal' and i.operands == (name,)]
            source_calls = re.findall(r'\b' + re.escape(name) + r'\s*\(', body)
            mentions = re.findall(r'\b' + re.escape(name) + r'\b', body)
            if not witnesses or len(witnesses) != len(source_calls) or len(mentions) != len(source_calls):
                continue
            replacement = '(' + ', '.join(['(void)(' + a + ')' for a in args[expected:]]
                + [name + '(' + ', '.join(args[:expected]) + ')']) + ')'
            choices.append((start, closing+1, replacement, name, protos, witnesses))
        if len(choices) == 1:
            a, b, replacement, name, protos, witnesses = choices[0]
            edits[a, b] = (replacement, dict(callee=name, header_prototypes=protos,
                target_calls=witnesses, expected=expected, supplied=have))
        else:
            report['declines'].append('no unique supported header/target call at diagnostic')
    ordered = sorted(edits.items())
    if len(ordered) > 128 or any(a[0][1] > b[0][0] for a, b in zip(ordered, ordered[1:])):
        report['declines'].append('overlapping call projections or edit bound')
        return report
    for (a, b), (replacement, detail) in reversed(ordered):
        report['source'] = report['source'][:a] + replacement + report['source'][b:]
    report['changes'] = [dict(start=a, end=b, before=source[a:b], after=replacement,
        kind='header-call-arity-projection', **detail) for (a, b), (replacement, detail) in ordered]
    return report
