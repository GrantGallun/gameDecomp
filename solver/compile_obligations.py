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
    # Referenced global types often never appear literally in a draft. Put
    # their included-header declarations ahead of lexical family suggestions,
    # otherwise unrelated large record families can exhaust the packet budget.
    declared = project_headers._included_declarations(repo, source)
    global_types = {token for name, rows in declared.items() if name in used
                    for row in rows if row.lstrip().startswith('extern ')
                    for token in re.findall(r'\b[A-Za-z_]\w*\b', row)
                    if token in definitions}
    ordered = [n for n in definitions if n in global_types]
    ordered += [n for n in definitions if n not in ordered and any(w in n.lower() for w in family)]
    ordered += [n for n in definitions if n not in ordered and n in used]
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


def missing_alias_declarations(source: str, function: str, header_text: str) -> list:
    """`typedef struct X X;` for every tag the headers forward-declare but never alias.

    THE GUARD IS `typedecl.typedefs`, NOT A REGEX, and this function exists so it can be tested without an
    assembly. It used to be an inline
    `re.search(r'\\btypedef\\b[^;]*\\bNAME\\s*;', header_text)`, which cannot cross a `;` and therefore
    cannot see `typedef struct RacePlayer { ... } RacePlayer;` -- the way every real struct is written. The
    alias was appended anyway and cfe answered `redeclaration of 'RacePlayer'; previous declaration at line
    243 in race_player_input.h`: measured on the development panel, 3 of 17 compiling candidates became
    uncompilable, and the action was the only one of the catalog that could still fire on a finished
    candidate.

    The tag-only case still repairs, and that is the difference between an ALIAS and a TAG: `struct X;`
    makes `struct X` usable and leaves `X` unusable, which is exactly when m2c's dropped `struct` keyword
    needs this declaration.
    """
    from solver import typedecl

    aliases = set(typedecl.typedefs(header_text))
    declared_tags = set(re.findall(r'\bstruct\s+(\w+)\s*;', header_text))
    out = []
    for _, name, _ in typedecl.pointer_parameters(source, function):
        if (name in declared_tags and name not in aliases
                and not typedecl.declared_in(source, name)):
            out.append({'type': name, 'text': f'typedef struct {name} {name};'})
            aliases.add(name)
    return out


def redeclarations(added: list, source: str, header_text: str) -> list:
    """One entry per declaration that cannot be added, each naming what it would collide with.

    PER DECLARATION, NOT ALL-OR-NOTHING. The first version returned a single reason for the whole set and
    the caller abandoned every declaration because of it. Measured cost: one state that compiled only
    because of its `void *` layout (`osEPiRawReadIo`, 79.15) stopped compiling entirely when an unrelated
    plan for a base type (`s32`) tripped the guard. A bad declaration should cost its own declaration.
    """
    from solver import typedecl

    existing_aliases = set(typedecl.typedefs(source)) | set(typedecl.typedefs(header_text))
    existing_tags = set(re.findall(r'\bstruct\s+(\w+)\s*\{', source + header_text))
    out = []
    for entry in added:
        text = entry.get("text") or ""
        for alias in typedecl.typedefs(text):
            if alias in existing_aliases:
                out.append({"type": entry.get("type") or alias, "text": text[:200],
                            "reason": f"the declaration would redeclare the type name {alias!r}"})
                break
        else:
            for tag in re.findall(r'\bstruct\s+(\w+)\s*\{', text):
                if tag in existing_tags:
                    out.append({"type": entry.get("type") or tag, "text": text[:200],
                                "reason": f"the declaration would redefine the struct tag {tag!r}"})
                    break
    return out


def _redeclaration(added: list, source: str, header_text: str) -> str:
    """The first collision as a sentence, for callers written against the old single-answer shape."""
    found = redeclarations(added, source, header_text)
    return found[0]["reason"] if found else ""


def _primitive_type_names() -> frozenset:
    """Every name that is already a type before any declaration: the C keywords plus the build's own
    scalar typedefs (`s32`, `u8`, ...). A plan whose type is one of these is not a missing declaration."""
    from solver import typedecl

    names = set(typedecl.PRIMITIVE_TYPES)
    names |= {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64", "size_t", "uintptr_t"}
    return frozenset(names)


_PRIMITIVE_TYPE_NAMES = None


def _is_primitive_type(name: str) -> bool:
    global _PRIMITIVE_TYPE_NAMES
    if _PRIMITIVE_TYPE_NAMES is None:
        _PRIMITIVE_TYPE_NAMES = _primitive_type_names()
    return name in _PRIMITIVE_TYPE_NAMES


def plan_declarations(source: str, function: str, plans: list, layout: dict, header_text: str,
                      forward: dict, bare_tags: set, existing_aliases: set = frozenset()) -> tuple:
    """Which recovered layouts may be inserted, which may not, and WHY NOT for every one that may not.

    Four outcomes, and the difference between the first two is the whole point:

    * DERIVED (`void *` parameter) -- the type has no name at all, so a tag is manufactured from the
      parameter position and the parameter is respelled to point at it. Both halves or neither: a struct
      with no parameter pointing at it resolves nothing.
    * ACCEPTED as an ORPHAN (type named, no header supplies it) -- the planner's anonymous typedef already
      names exactly the type the source uses, so it is inserted unchanged. This used to be DISCARDED,
      silently, because the code asked the headers for a tag, found none and moved on. Reproduced: with
      `UnseenActor *arg0` dereferenced at 0xC the planner returns
      `typedef struct { char pad00[0xc]; s32 unkC; } UnseenActor;` -- valid C, correct offsets, naming the
      type the source already spells -- and the action threw it away while repairing the equivalent
      `void *` case. The header lookup is for COMPLETING A TAG THE HEADERS DECLARED, not a precondition for
      declaring anything at all.
    * ACCEPTED with a header tag -- the header declares the tag and not its body, so the anonymous typedef
      is rewritten to define that tag, keeping the header's spelling.
    * DECLINED, with a named reason. Ambiguous field names, overlapping access views, and a type the header
      already completes all produce a receipt entry that says so; none of them returns silently.
    """
    derived: list[tuple[str, str, str]] = []          # (tag, parameter variable, struct text)
    for index, var in ((i, v) for i, _t, v in typedecl.pointer_parameters(source, function)):
        tag = f"{function}_arg{index}"
        for p in plans:
            if p["type"] == "void" and index in p["params"] and var not in {v for _t, v, _x in derived}:
                text = re.sub(r"\}\s*void\s*;$", "};", p["text"])
                text = text.replace("typedef struct {", f"struct {tag} {{", 1)
                derived.append((tag, var, text))
    accepted, declined = [], []
    for p in plans:
        if p["type"] == "void":
            continue                               # handled above, by tag rather than by lookup
        tag = forward.get(p['type'])
        if tag and re.search(r'\bstruct\s+' + re.escape(tag) + r'\s*\{', header_text):
            declined.append({'type': p['type'], 'tag': tag,
                             'reason': 'the header already defines this type',
                             'limitation': 'nothing to add: the members are visible in the translation unit '
                                           'already, so a second definition would be a redefinition'})
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
            declined.append({'type': p['type'], 'reason': 'overlapping access views',
                             'observed': [list(slot) for slot in fields],
                             'limitation': 'Two accesses overlap, so no single field layout explains both; '
                                           'one of them is a wider or differently typed view.'})
            continue
        if not tag:
            # THE ORPHAN. No header supplies this name, so there is no tag to complete and nothing to
            # rename: the planner's anonymous typedef is already the declaration the source needs.
            #
            # UNLESS THE NAME IS ALREADY A TYPE. `typedecl.plan` will happily plan for a parameter written
            # `s32 *arg0` and dereferenced, and `typedef struct { ... } s32;` is not a repair, it is a
            # redeclaration of a base type. Measured: letting it through cost a state that used to compile
            # (`osEPiRawReadIo`, 79.15 -> 0.0) because the collision guard below then abandoned the WHOLE
            # action, including the `void *` declaration that had made it compile. The plan is refused here,
            # by name, and the rest of the action proceeds.
            if p["type"] in existing_aliases or _is_primitive_type(p["type"]):
                declined.append({'type': p['type'],
                                 'reason': 'the name is already a type in this translation unit',
                                 'limitation': 'declaring a struct with this name would redeclare the type '
                                               'rather than repair a missing one'})
                continue
            accepted.append({**p, "orphan": True})
            continue
        p = {**p}
        p['text'] = p['text'].replace('typedef struct {', 'struct ' + tag + ' {', 1)
        p['text'] = re.sub(r'\}\s*' + re.escape(p['type']) + r';$', '};', p['text'])
        accepted.append(p)
    return accepted, derived, declined


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
    forward = dict((alias, tag) for alias, tag in typedecl.typedefs(header_text).items() if tag)
    existing_aliases = set(typedecl.typedefs(header_text))
    bare_tags = set(re.findall(r'\bstruct\s+(\w+)\s*;', header_text))
    # m2c may drop the struct keyword even though a public header supplies only
    # a tag. Supply the missing alias independently of layout recovery; never
    # make field-name ambiguity silently suppress this spelling repair.
    aliases = missing_alias_declarations(source, function, header_text)
    existing_aliases |= {entry["type"] for entry in aliases}
    # The shared planner consumes typedef-shaped names. Project bare, publicly
    # forward-declared struct pointers only in its private input; insertion is
    # applied to the original source, leaving the public ABI spelling intact.
    #
    # SOURCE-LEVEL BARE TAGS TOO, not only the headers'. `struct Unseen *arg0` where nothing forward-declares
    # `struct Unseen` leaves the planner with nothing to key on -- measured above: `plan()` returns zero
    # plans for that spelling while returning a complete, valid one for `UnseenActor *arg0`. The projection
    # is the same mechanism and stays confined to the planner's private input: the emitted text defines
    # `struct Unseen { ... };`, which is exactly what the source's spelling needs.
    planning_tags = set(bare_tags) | set(re.findall(r'\bstruct\s+(\w+)\s*\*', source))
    planning_source = source
    for tag in planning_tags:
        if re.search(r'\bstruct\s+'+re.escape(tag)+r'\s*\{', header_text+source):
            continue
        forward.setdefault(tag,tag)
        planning_source = re.sub(r'\bstruct\s+'+re.escape(tag)+r'(?=\s*\*)',tag,planning_source)
        planning_source = re.sub(r'\btypedef\s+struct\s+'+re.escape(tag)+r'\s+'
                                 +re.escape(tag)+r'\s*;', '', planning_source)
    plans = typedecl.plan(planning_source, function, layout, set())
    accepted, derived, declined = plan_declarations(planning_source, function, plans, layout,
                                                    header_text, forward, planning_tags,
                                                    existing_aliases=existing_aliases)
    # The derived `void *` blocks and the parameter respelling both go in, or neither does: a struct with
    # no parameter pointing at it cannot resolve a member access.
    added = aliases + accepted + [{"text": text} for _tag, _var, text in derived]
    # AN ACTION THAT EMITS INVALID C IS NOT A HYPOTHESIS, IT IS A REGRESSION. Every caller ranks the
    # candidate against the incumbent, so an uncompilable proposal is normally dropped -- but dropping it
    # costs a compile, and any caller that applies actions without ranking gets a broken candidate. The
    # check is decidable without a compiler: the declarations this action is about to ADD must not
    # redeclare a name the translation unit already has. Measured: forgetting it turned 3 of 17 compiling
    # development candidates into uncompilable ones with `redeclaration of 'RacePlayer'`.
    #
    # AND IT COSTS ONLY ITS OWN DECLARATION. The first version abandoned the whole action on the first
    # collision, which took a compiling state down with it (`osEPiRawReadIo`, 79.15 -> 0.0, because an
    # unrelated plan collided while the `void *` declaration that state needed was fine).
    blocked = redeclarations(added, source, header_text)
    if blocked:
        dropped = {entry["text"] for entry in blocked}
        declined.extend(blocked)
        added = [entry for entry in added if (entry.get("text") or "") not in dropped]
        accepted = [entry for entry in accepted if (entry.get("text") or "") not in dropped]
        derived = [(tag, var, text) for tag, var, text in derived if text not in dropped]
    out = source
    for tag, var, text in derived:
        out = re.sub(r"\bvoid\s*\*\s*" + re.escape(var) + r"\b", f"struct {tag} *{var}", out, count=1)
    out = typedecl.apply(out, added)
    return out, {'stage': 'opaque-parameter-layout',
        'authority': 'fresh dataflow addresses; field mapping is a candidate hypothesis',
        'plans': accepted,
        'derived_void_parameters': [{'tag': tag, 'parameter': var} for tag, var, _t in derived],
        'tag_aliases': aliases, 'declined': declined, 'memory_accesses': accesses}


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
