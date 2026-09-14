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
    existing_aliases = set(forward)
    bare_tags = set(re.findall(r'\bstruct\s+(\w+)\s*;', header_text))
    aliases = []
    # m2c may drop the struct keyword even though a public header supplies only
    # a tag. Supply the missing alias independently of layout recovery; never
    # make field-name ambiguity silently suppress this spelling repair.
    for _, name, _ in typedecl.pointer_parameters(source, function):
        if (name in bare_tags and name not in existing_aliases
                and not typedecl.declared_in(source, name)
                and not re.search(r'\btypedef\b[^;]*\b'+re.escape(name)+r'\s*;', header_text)):
            aliases.append({'type': name, 'text': f'typedef struct {name} {name};'})
            existing_aliases.add(name)
    # The shared planner consumes typedef-shaped names. Project bare, publicly
    # forward-declared struct pointers only in its private input; insertion is
    # applied to the original source, leaving the public ABI spelling intact.
    planning_source = source
    for tag in bare_tags:
        if re.search(r'\bstruct\s+'+re.escape(tag)+r'\s*\{', header_text+source):
            continue
        forward.setdefault(tag,tag)
        planning_source = re.sub(r'\bstruct\s+'+re.escape(tag)+r'(?=\s*\*)',tag,planning_source)
        planning_source = re.sub(r'\btypedef\s+struct\s+'+re.escape(tag)+r'\s+'
                                 +re.escape(tag)+r'\s*;', '', planning_source)
    plans = typedecl.plan(planning_source, function, layout, set())
    accepted, declined = [], []
    for p in plans:
        tag = forward.get(p['type'])
        if not tag or re.search(r'\bstruct\s+'+re.escape(tag)+r'\s*\{', header_text):
            continue
        names = list(p['named'].values())
        if len(names) != 1 and not all(re.fullmatch(r'(?:unk|field)_?[0-9a-fA-F]+', n) for n in names):
            declined.append({'type': p['type'], 'reason': 'ambiguous field-name mapping',
                'source_members': names,
                'observed_parameter_slots': {str(i): [list(slot) for slot in layout.get('param'+str(i), [])]
                                             for i in p['params']},
                'limitation': 'Textual member order does not establish correspondence to binary offsets.'})
            continue
        fields = [f for index in p['params'] for f in layout.get('param'+str(index), [])]
        fields = sorted(set(fields))
        if any(a[0]+a[1] > b[0] for a,b in zip(fields, fields[1:])):
            declined.append({'type': p['type'], 'reason': 'overlapping access views'})
            continue
        p['text'] = p['text'].replace('typedef struct {', 'struct '+tag+' {', 1)
        p['text'] = re.sub(r'\}\s*'+re.escape(p['type'])+r';$', '};', p['text'])
        accepted.append(p)
    return typedecl.apply(source, aliases + accepted), {'stage': 'opaque-parameter-layout',
        'authority': 'fresh dataflow addresses; field mapping is a candidate hypothesis',
        'plans': accepted, 'tag_aliases': aliases, 'declined': declined, 'memory_accesses': accesses}


def byte_pointer_variant(source, function, assembly):
    """Scalar-pointer assignment hypotheses tied to a unique direct callsite.

    A parameter+literal in C may have been incorrectly scaled after completing
    an opaque type. Only propose byte units when the target passes that exact
    parameter-relative byte address at the corresponding direct-call argument.
    """
    from solver import repair_context
    report = {'stage':'call-bound-byte-pointer-view','plans':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'authority':'binary call-argument address; source/call correspondence is a candidate hypothesis'}
    try:
        definition, end = repair_context.definition(source,function)
        analysis, _ = analyse(assembly)
    except ValueError as exc:
        return source,{**report,'reason':str(exc)}
    masked = project_headers._mask_noncode(source)
    body = masked[definition.end():end-1]
    params = {}
    for index, param in enumerate(typedecl.definition_params(source,function) or []):
        match = re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*(\w+)\s*',param)
        if match:
            params[match[1]] = index
    scalar = r's8|u8|s16|u16|s32|u32|f32|char|short|int|float'
    locals = {}
    for match in re.finditer(r'\b('+scalar+r')\s*\*\s*(\w+)\s*;',body):
        locals.setdefault(match[2],[]).append(match[1])
    edits = []
    for match in re.finditer(r'(?m)^[ \t]*(\w+)\s*=\s*(\w+)\s*\+\s*(0x[0-9a-fA-F]+|[0-9]+)\s*;',body):
        dest, base, literal = match.groups()
        if base not in params or len(locals.get(dest,[])) != 1:
            continue
        # Restrict to a single assignment and an unmodified public base. More
        # involved SSA/alias/control-flow correspondence belongs to another pass.
        if len(re.findall(r'\b'+re.escape(dest)+r'\s*(?:[+\-*/%&|^]?=|<<=|>>=|\+\+|--)',body)) != 1:
            continue
        if re.search(r'\b'+re.escape(base)+r'\s*(?:[+\-*/%&|^]?=(?!=)|<<=|>>=|\+\+|--)|(?:\+\+|--|&)\s*\b'+re.escape(base)+r'\b',body):
            continue
        offset = int(literal,16 if literal.lower().startswith('0x') else 10)
        if len(literal)>1 and literal[0]=='0' and not literal.lower().startswith('0x'):
            continue  # C octal is outside this literal dialect.
        evidence = []
        for call in analysis.callsites.values():
            if not call.target:
                continue
            peers = [c for c in analysis.callsites.values() if c.target == call.target]
            sites = list(re.finditer(r'\b'+re.escape(call.target)+r'\s*\(([^()]*)\)',body))
            if len(peers)!=1 or len(sites)!=1 or sites[0].start() < match.end():
                continue
            for index, arg in enumerate(sites[0][1].split(',')):
                if arg.strip()!=dest or index>=len(call.arguments):
                    continue
                address = call.arguments[index]
                if address and address.kind=='address' and address.name=='param'+str(params[base]) and address.offset==offset:
                    evidence.append({'callee':call.target,'argument_word':index,'address':address.describe(precise=True)})
        if not evidence:
            continue
        ctype = locals[dest][0]
        replacement = f'{dest} = ({ctype} *)((unsigned char *){base} + {literal});'
        edits.append((definition.end()+match.start(),definition.end()+match.end(),replacement))
        report['plans'].append({'before':source[definition.end()+match.start():definition.end()+match.end()],
                                'after':replacement,'evidence':evidence})
    # Also cover the direct-call form, without requiring an artificial local.
    # Match a unique binary argument address, not merely a repeated callee name.
    for site in re.finditer(r'\b(\w+)\s*\(([^()]*)\)',body):
        callee=site[1]
        peers=[c for c in analysis.callsites.values() if c.target==callee]
        if not peers:
            continue
        cursor=site.start(2)
        for index,arg in enumerate(site[2].split(',')):
            match=re.fullmatch(r'\s*(\w+)\s*\+\s*(0x[0-9a-fA-F]+|[1-9][0-9]*|0)\s*',arg)
            if match and match[1] in params:
                base,literal=match.groups()
                modified=re.search(r'\b'+re.escape(base)+r'\s*(?:[+\-*/%&|^]?=(?!=)|<<=|>>=|\+\+|--)|(?:\+\+|--|&)\s*\b'+re.escape(base)+r'\b',body)
                offset=int(literal,0)
                evidence=[c for c in peers if index<len(c.arguments) and c.arguments[index]
                    and c.arguments[index].kind=='address' and c.arguments[index].name=='param'+str(params[base])
                    and c.arguments[index].offset==offset]
                expression=arg.strip()
                if not modified and len(evidence)==1 and body.count(expression)==1:
                    replacement=f'(void *)((unsigned char *){base} + {literal})'
                    begin=definition.end()+cursor
                    edits.append((begin,begin+len(arg),replacement))
                    report['plans'].append({'kind':'direct-call-byte-address','before':arg,'after':replacement,
                        'evidence':[{'callee':callee,'argument_word':index,
                            'address':evidence[0].arguments[index].describe(precise=True)}]})
            cursor+=len(arg)+1
    for start,end,replacement in sorted(edits,reverse=True):
        source = source[:start]+replacement+source[end:]
    return source,report
