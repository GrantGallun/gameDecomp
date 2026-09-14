"""Bounded offset-named private record hypotheses, not inferred type truth.

Only pointer-used candidate typedefs made entirely of padding and offset-named
fields qualify. Target offsets/widths are a coarse hypothesis filter, not proof
that two accesses belong to the same object. The ordinary compiler and semantic
gates must adjudicate the coordinated candidate.
"""
import hashlib
import re
from solver import project_headers, source_layout


def propose(source, assembly):
    report = {'source': source, 'changes': [], 'declines': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'authority': 'offset-name plus target access hypothesis; not root-bound type proof'}
    mask = project_headers._mask_noncode(source)
    loads = {}
    types = {'lb': ('signed char', 1), 'lbu': ('unsigned char', 1),
             'lh': ('short', 2), 'lhu': ('unsigned short', 2), 'lw': ('int', 4)}
    for m in re.finditer(r'\b(lb|lbu|lh|lhu|lw)\s+\$?\w+\s*,\s*(0x[\da-fA-F]+|\d+)\(\$?(\w+)\)', assembly):
        if m[3] not in ('sp', 'fp', 's8'):
            loads.setdefault(int(m[2], 0), set()).add(types[m[1]])
    edits = []
    structs = list(re.finditer(r'\btypedef\s+struct\s*\{([^{}]*)\}\s*(\w+)\s*;', mask))
    for record in structs[:16]:
        name = record[2]
        outside = mask[:record.start()] + ' '*(record.end()-record.start()) + mask[record.end():]
        uses = list(re.finditer(r'\b'+re.escape(name)+r'\b', outside))
        if not uses or any(not re.match(r'\s*\*', outside[u.end():]) for u in uses):
            report['declines'].append({'record': name, 'reason': 'requires pointer-only uses'})
            continue
        cursor, lines, changes = 0, [], []
        valid = True
        for declaration in record[1].split(';'):
            if not declaration.strip():
                continue
            pad = re.fullmatch(r'\s*char\s+(pad\w*)\s*\[[\s\dxa-fA-F()+\-]+\]\s*', declaration)
            if pad:
                # Padding names routinely repeat in distinct private structs.
                # Other record declarations are not accesses to this member.
                nonrecord = outside
                for other in reversed(structs):
                    nonrecord = nonrecord[:other.start()]+' '*(other.end()-other.start())+nonrecord[other.end():]
                if re.search(r'\b'+re.escape(pad[1])+r'\b', nonrecord):
                    valid = False
                    break
                continue
            field = re.fullmatch(r'\s*([\w\s]+?\s*\*?)\s*(unk[\da-fA-F]+|field_?[\da-fA-F]+)\s*', declaration)
            if not field:
                valid = False
                break
            ctype, member = ' '.join(field[1].split()), field[2]
            offset = int(re.search(r'(?:unk|field_?)([\da-fA-F]+)$', member)[1], 16)
            observed = loads.get(offset, set())
            if not observed or offset > 65536:
                valid = False
                break
            if '*' in ctype:
                if {width for _, width in observed} != {4}:
                    valid = False
                    break
                width, alignment = 4, 4
            else:
                if ctype not in source_layout._SCALARS or len(observed) != 1:
                    valid = False
                    break
                inferred, width = next(iter(observed))
                old_width, alignment = source_layout._SCALARS[ctype]
                if old_width != width:
                    changes.append({'field': member, 'old_type': ctype, 'type_hypothesis': inferred})
                    ctype, alignment = inferred, width
            if offset < cursor or offset % alignment:
                valid = False
                break
            if offset > cursor:
                lines.append(f'    char pad{cursor:X}[0x{offset-cursor:X}];')
            lines.append(f'    {ctype} {member};')
            changes.append({'field': member, 'offset_hypothesis': offset, 'width': width})
            cursor = offset + width
        if not valid or not changes:
            report['declines'].append({'record': name, 'reason': 'unsupported, ambiguous, unobserved or overlapping field'})
            continue
        body = '\n'+'\n'.join(lines)+'\n'
        # Formatting alone is not a useful experiment. Compare understood field
        # offsets/types; expression padding may make the old layout unavailable.
        old = source_layout.layouts(source[record.start():record.end()])
        new = source_layout.layouts('typedef struct {'+body+'} '+name+';')
        def useful(layouts):
            return [(f.name, f.offset, f.size) for l in layouts for f in l.fields if not f.name.startswith('pad')]
        if old and useful(old) == useful(new):
            continue
        edits.append((record.start(1), record.end(1), body))
        report['changes'].append({'record': name, 'fields': changes})
    for start, end, body in reversed(edits):
        report['source'] = report['source'][:start]+body+report['source'][end:]
    return report
