"""Deterministic storage/type input for source repair, not inferred truth."""
import hashlib
import json
from pathlib import Path
import re

from solver import buildtypes, dataflow, project_headers, structgen, typedecl


def analyse(assembly):
    analysis = dataflow.analyse(assembly)
    rows = []
    for site, access in sorted(analysis.accesses.items()):
        rows.append({'instruction': site, 'opcode': access.opcode, 'width': access.width,
                     'address': access.address.describe(precise=True) if access.address else 'unresolved',
                     'text': analysis.graph.instructions[site].text})
    return analysis, rows


def header_types(repo, source, function, max_chars=14000):
    includes = re.findall(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]', source)
    direct, closure = [], set()
    for inc in includes:
        path = repo/'include'/inc
        if path.is_file():
            direct.append(path)
            closure.update(buildtypes.closure(repo, 'include/'+inc))
    definitions = {}
    for path in [*direct, *sorted(closure - set(direct))]:
        text = path.read_text(errors='replace')
        names = re.findall(r'\}\s*([A-Za-z_]\w*)\s*;', project_headers._mask_comments(text))
        for name in names:
            if name not in definitions:
                body = project_headers._typedef_definition(text, name)
                if body:
                    definitions[name] = (str(path.relative_to(repo)), body)
    used = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
    # Lexical family matches are suggestions only; existing source types first.
    family = {w.lower() for w in re.findall(r'[A-Z][a-z]+', function) if len(w) >= 4}
    # Expand each relevant family as a connected packet. A callback return
    # union (e.g. a command-buffer type) must not spend the entire budget on
    # its many arms before the owning object's member types are shown.
    ordered = [n for n in definitions if any(w in n.lower() for w in family)]
    ordered += [n for n in definitions if n in used and n not in ordered]
    result, seen, size = [], set(), 0
    while ordered and len(seen) < 40:
        name = ordered.pop(0)
        if name in seen:
            continue
        seen.add(name)
        path, body = definitions[name]
        if size + len(body) > max_chars:
            continue
        result.append({'type': name, 'header': path, 'definition': body})
        size += len(body)
        dependencies = list(dict.fromkeys(n for n in re.findall(r'\b[A-Za-z_]\w*\b', body)
                                          if n in definitions and n not in seen))
        ordered = dependencies + ordered
    return result


def packet(repo: Path, function: str, source: str, assembly: str):
    analysis, accesses = analyse(assembly)
    from solver import stack_buffers
    _, stack_report = stack_buffers.candidates(source, assembly, function)
    return {'kind': 'source-bound-compile-obligations',
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
        'rules': ['Access width is not a proven C field type; names and candidate layouts are hypotheses.',
                  'Stack offsets are relative to entry SP, not C variable names. Reconstruct arrays/structs before indexing.',
                  'Preserve byte-copy strides when adding a typed struct view; do not rescale pointer arithmetic accidentally.',
                  'Complete opaque tags with padded source-local definitions; never replace read-only header typedefs.',
                  'Use actual included prototypes. A void* parameter can have a typed local view without changing its ABI.'],
        'public_prototypes': [{'header': d.include, 'declaration': d.prototype}
                              for d in project_headers.declarations(repo, function)],
        'memory_accesses': accesses[:200], 'omitted_accesses': max(0, len(accesses)-200),
        'calls': [{'instruction': c.instruction, 'target': c.target,
                   'arguments': [a.describe(precise=True) if a else 'unresolved' for a in c.arguments]}
                  for c in analysis.callsites.values()],
        'stack_buffer_hypotheses': stack_report,
        'read_only_header_types': header_types(repo, source, function)}


def opaque_variant(repo: Path, function: str, source: str, assembly: str):
    analysis, accesses = analyse(assembly)
    layout = {}
    for access in analysis.accesses.values():
        a = access.address
        if not a or a.kind != 'address' or not a.name.startswith('param') or a.offset < 0:
            continue
        if access.opcode in {'lwl', 'lwr', 'swl', 'swr', 'lwc1', 'swc1', 'ldc1', 'sdc1'}:
            continue
        signed = 0 if access.opcode in {'lbu', 'lhu'} else 1
        field = (a.offset, access.width, structgen.field_type(access.width, signed))
        entries = layout.setdefault(a.name, {})
        # Prefer actual loads over stores for the signedness of a candidate view.
        if a.offset not in entries or access.is_load:
            entries[a.offset] = field
    layout = {base: sorted(slots.values()) for base, slots in layout.items()}
    header_text = ''
    for inc in re.findall(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]', source):
        for path in sorted(buildtypes.closure(repo, 'include/'+inc)):
            header_text += path.read_text(errors='replace') + '\n'
    forward = dict((alias, tag) for tag, alias in re.findall(
        r'\btypedef\s+struct\s+(\w+)\s+(\w+)\s*;', header_text))
    plans = typedecl.plan(source, function, layout, set())
    accepted, declined = [], []
    for p in plans:
        tag = forward.get(p['type'])
        if not tag or re.search(r'\bstruct\s+'+re.escape(tag)+r'\s*\{', header_text):
            continue
        names = list(p['named'].values())
        if len(names) != 1 and not all(re.fullmatch(r'(?:unk|field)_?[0-9a-fA-F]+', n) for n in names):
            declined.append({'type': p['type'], 'reason': 'ambiguous field-name mapping'})
            continue
        fields = [f for index in p['params'] for f in layout.get('param'+str(index), [])]
        fields = sorted(set(fields))
        if any(a[0]+a[1] > b[0] for a,b in zip(fields, fields[1:])):
            declined.append({'type': p['type'], 'reason': 'overlapping access views'})
            continue
        p['text'] = p['text'].replace('typedef struct {', 'struct '+tag+' {', 1)
        p['text'] = re.sub(r'\}\s*'+re.escape(p['type'])+r';$', '};', p['text'])
        accepted.append(p)
    return typedecl.apply(source, accepted), {'stage': 'opaque-parameter-layout',
        'authority': 'fresh dataflow addresses; field mapping is a candidate hypothesis',
        'plans': accepted, 'declined': declined, 'memory_accesses': accesses}
