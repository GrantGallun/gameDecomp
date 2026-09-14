"""Small diagnostic-bound C representation hypotheses, never acceptance gates.

Do not weaken frontend policy or change interfaces. Every proposal must be
compiled and (where available) differentially tested by the ordinary pipeline.
"""
import hashlib
import re

from solver import project_headers, repair_context, type_transaction


def big_endian_o32(target):
    if not target.is_file():
        return False
    data = target.read_bytes()[:52]
    return (len(data) == 52 and data[:6] == b'\x7fELF\x01\x02'
            and int.from_bytes(data[18:20], 'big') == 8
            and int.from_bytes(data[36:40], 'big') & 0xf000 == 0x1000)


def propose(repo, source, function, diagnostics, *, big_endian_o32=False):
    report = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'diagnostics_sha256': hashlib.sha256(diagnostics.encode()).hexdigest(),
              'big_endian_o32': big_endian_o32, 'header_prototypes': {},
              'changes': [], 'declines': [], 'source': source,
              'scope': 'diagnostic-bound representation hypotheses; not semantic proof'}
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = {}
    declarations = None
    pattern = re.compile(r'^candidate\.c:(\d+):(\d+): error: (.*)$', re.M)
    for diagnostic in pattern.finditer(diagnostics):
        number, column = int(diagnostic[1]), int(diagnostic[2])
        if not (1 <= number <= len(lines)):
            continue
        line = lines[number-1].rstrip('\r\n')
        # Clang's displayed source is an independent stale-location check.
        excerpt = re.match(r'\n\s*'+str(number)+r' \| (.*)', diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1] != line or '\t' in line:
            report['declines'].append('diagnostic source line missing, stale, or tab-expanded')
            continue
        position = offsets[number-1] + column-1
        if not match.end() <= position < end-1:
            continue
        message = diagnostic[3]
        # Integer literal ROM/address arguments. No identifiers, expressions,
        # pointer dereferences, or inferred public ABI changes.
        if big_endian_o32 and "integer to pointer conversion passing 'int' to parameter of type 'void *'" in message:
            token = re.match(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)\b', masked[position:])
            if token and re.match(r'\s*[,)]', masked[position+token.end():]):
                literal = token[0]
                value = int(literal, 16 if literal.lower().startswith('0x') else 10)
                if 0 < value <= 0xffffffff:
                    edits[position, position+len(literal)] = ('(void *)'+literal, 'literal-address-argument')
        # An object address assigned to a byte cursor, preserving its address.
        # Reject other destination widths and complex RHS expressions.
        if "incompatible pointer types assigning to 'u8 *'" in message or "incompatible pointer types assigning to 'unsigned char *'" in message:
            statement = re.fullmatch(r'\s*\w+\s*=\s*(&?\w+(?:\s*[+-]\s*\([\w\s<>+\-]+\))?)\s*;\s*', line)
            if statement and ' from ' in message and '*' in message.split(' from ', 1)[1]:
                a = offsets[number-1] + statement.start(1)
                b = offsets[number-1] + statement.end(1)
                assignment = offsets[number-1] + line.index('=')
                if assignment <= position < b:
                    edits[a, b] = ('(unsigned char *)('+source[a:b]+')', 'object-address-byte-view')
        if big_endian_o32 and 'too many arguments to function call, expected single argument' in message:
            if declarations is None:
                declarations = project_headers._included_declarations(repo, source)
            # Only a closed, side-effect-free two-word call to a header-declared
            # single 64-bit argument. No guessed names, deleted words or prototypes.
            for call in re.finditer(r'\b(\w+)\s*\(', masked[offsets[number-1]:offsets[number]]):
                name = call[1]
                protos = declarations.get(name, [])
                shapes = [type_transaction.signature(p, name) for p in protos]
                if (not shapes or any(s is None or s[1] not in ((('u64',),), (('s64',),)) for s in shapes)
                        or len(set(shapes)) != 1):
                    continue
                opening = offsets[number-1]+call.end()
                depth, split, cursor = 1, [], opening
                while cursor < offsets[number] and depth:
                    char = masked[cursor]
                    if char == ',' and depth == 1:
                        split.append(cursor)
                    depth += (char == '(') - (char == ')')
                    cursor += 1
                if depth or len(split) != 1 or not opening <= position < cursor:
                    continue
                a, b = source[opening:split[0]].strip(), source[split[0]+1:cursor-1].strip()
                parts = (a, b)
                if any(not re.fullmatch(r'[\w\s()+\-<>&|^~]+', p) or
                       re.search(r'\b\w+\s*\(|\+\+|--', p) for p in parts):
                    continue
                replacement = '(((u64)(u32)('+a+') << 32) | (u32)('+b+'))'
                if shapes[0][1] == (('s64',),):
                    replacement = '(s64)'+replacement
                report['header_prototypes'][name] = protos
                edits[opening, cursor-1] = (replacement, 'o32-u64-argument-pack')
    ordered = sorted(edits.items())
    if len(ordered) > 128 or any(a[0][1] > b[0][0] for a, b in zip(ordered, ordered[1:])):
        report['declines'].append('edit bound or overlapping diagnostics')
        return report
    for (a, b), (replacement, kind) in reversed(ordered):
        report['source'] = report['source'][:a] + replacement + report['source'][b:]
    report['changes'] = [{'start': a, 'end': b, 'before': source[a:b], 'after': replacement, 'kind': kind}
                         for (a, b), (replacement, kind) in ordered]
    return report
