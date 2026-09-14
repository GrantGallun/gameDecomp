"""Bounded coordinated type repair, not a type oracle or a C parser.

Uses included header declarations and candidate-local use sites only. The
compiler remains authoritative; unsupported ABI syntax is explicitly reported.
"""
import copy
import re

from solver import buildtypes, dataflow, project_headers

MAX_EDITS = 64
MAX_CHARS = 12000
MAX_GROWTH = 4000
LOOKAHEAD = 2


def schema(base):
    result = copy.deepcopy(base)
    edits = result['properties']['edits']
    edits['maxItems'] = MAX_EDITS
    edits['items'].pop('oneOf', None)
    edits['items']['required'] = ['slot', 'new']
    edits['items']['properties'].pop('old', None)
    return result


def signature(text, function):
    """Canonicalize ordinary prototypes, ignoring parameter names only.

    Complex declarators and unspecified argument lists decline. This is a
    conservative spelling lock, not a claim of arbitrary C type equivalence.
    """
    text = project_headers._mask_noncode(text)
    # These standalone project diagnostic macros decorate a definition; they
    # are not part of its return type. Leave arbitrary identifiers untouched:
    # the compiler still checks the expanded source and the ABI spelling lock
    # must not silently accept a different declaration.
    text = re.sub(r'(?m)^\s*CLANG_DIAGNOSTIC_(?:PUSH|POP|IGNORE_RETURN_TYPE)\s*$', '', text)
    match = re.search(r'\b' + re.escape(function) + r'\s*\(([^()]*)\)\s*(?:;|\{|$)', text)
    if not match:
        return None
    boundary = max(text.rfind(';', 0, match.start()), text.rfind('}', 0, match.start()),
                   text.rfind('{', 0, match.start()))
    returns = text[boundary+1:match.start()].strip()
    returns = re.sub(r'(?m)^\s*#.*$', '', returns).strip()
    returns = re.sub(r'^extern\s+', '', returns)
    if not returns or re.search(r'[^\w\s*]', returns):
        return None
    raw = match.group(1).strip()
    if not raw:
        return None
    params = []
    primitive = {'void','char','short','int','long','float','double','signed','unsigned','const','volatile'}
    for part in ([] if raw == 'void' else raw.split(',')):
        part = part.strip()
        if re.search(r'[^\w\s*]', part):
            return None
        name = re.search(r'\b([A-Za-z_]\w*)$', part)
        if name and name.group(1) not in primitive and part[:name.start()].strip():
            before = part[:name.start()].strip()
            if before not in {'struct', 'union', 'enum', 'const', 'volatile'}:
                part = before
        params.append(tuple(re.findall(r'\w+|\*', part)))
    return tuple(re.findall(r'\w+|\*', returns)), tuple(params)


def contract(repo, source, function):
    declarations = project_headers._included_declarations(repo, source).get(function, [])
    known = set()
    for inc in re.findall(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]', source):
        known.update(buildtypes.type_names(repo, 'include/'+inc))
        for path in buildtypes.closure(repo, 'include/'+inc):
            known.update(typedef_names(path.read_text(errors='replace')))
    shapes = [signature(d, function) for d in declarations]
    usable = {s for s in shapes if s is not None}
    if len(usable) == 1 and all(s is not None for s in shapes):
        return {'status':'locked', 'declarations':declarations, 'shape':next(iter(usable)),
                'known_header_types':sorted(known)}
    return {'status':'ambiguous' if declarations else 'unavailable',
            'declarations':declarations, 'shape':None, 'known_header_types':sorted(known)}


def typedef_names(source):
    # The ordinary top-level declaration scanner deliberately skips braced
    # definitions. Reuse the brace-balanced typedef extractor for those.
    aliases = re.findall(r'\}\s*([A-Za-z_]\w*)\s*;', project_headers._mask_noncode(source))
    names = {name for name in aliases if project_headers._typedef_definition(source, name)}
    for _, _, declaration in project_headers._top_level_declarations(source):
        if declaration.lstrip().startswith('typedef '):
            tail = re.search(r'\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;$', declaration)
            if tail:
                names.add(tail[1])
    return names


def validate(source, candidate, function, abi):
    # Type macros were a measured source of signature corruption. This mode
    # permits explicit C views, not preprocessor-based rewrites or checker edits.
    directives = lambda s: re.findall(r'(?m)^\s*#.*$', project_headers._mask_comments(s))
    if directives(candidate) != directives(source):
        raise ValueError('type transaction cannot change preprocessor directives; use typed locals')
    collisions = (typedef_names(candidate) - typedef_names(source)) & set(abi.get('known_header_types', []))
    if collisions:
        raise ValueError('included headers already define these types: '+', '.join(sorted(collisions))+
                         '; reuse their actual fields or give a candidate-local view a distinct name')
    # Slots identify lines, not intent. Catch line-number mistakes that remove
    # a switch/if/loop while attempting a declaration/member-only transaction.
    controls = lambda s: re.findall(r'\b(?:if|else|switch|case|default|for|while|do|goto|break|continue)\b',
                                    project_headers._mask_noncode(s))
    if controls(source) != controls(candidate):
        raise ValueError('type transaction changed control structure; preserve each control line and edit only its typed expressions')
    if abi['status'] == 'locked':
        # Match only a definition, not an extra prototype inserted before it.
        masked = project_headers._mask_noncode(candidate)
        definitions = list(re.finditer(r'\b'+re.escape(function)+r'\s*\([^()]*\)\s*\{', masked))
        if len(definitions) != 1:
            raise ValueError('public ABI lock requires exactly one ordinary function definition')
        start = definitions[0].start()
        boundary = max(masked.rfind(';',0,start), masked.rfind('}',0,start), masked.rfind('{',0,start))
        if signature(masked[boundary+1:definitions[0].end()], function) != abi['shape']:
            raise ValueError('public ABI lock: preserve header return/parameter types; use typed local aliases')


def packet(source, assembly, function, abi):
    uses, assignments, returns = [], [], []
    clean = project_headers._mask_noncode(source)
    for number, line in enumerate(clean.splitlines(), 1):
        members = re.findall(r'\b([A-Za-z_]\w*)((?:\s*->\s*[A-Za-z_]\w*)+)', line)
        for base, chain in members:
            uses.append({'slot':f'L{number}', 'base':base,
                         'members':re.findall(r'->\s*([A-Za-z_]\w*)', chain)})
        assign = re.search(r'\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)((?:\s*->\s*\w+)*)\s*;', line)
        if assign:
            assignments.append({'slot':f'L{number}', 'destination':assign[1],
                                'source':assign[2]+assign[3]})
        if re.search(r'\breturn\b', line):
            returns.append({'slot':f'L{number}', 'statement':line.strip()})
    analysis = dataflow.analyse(assembly)
    exits = []
    for key, block in analysis.graph.blocks.items():
        term = block.terminator
        if term is None or term.opcode != 'jr' or not term.operands or term.operands[0].lstrip('$') != 'ra':
            continue
        state = analysis.block_out.get(key)
        value = state.registers.get('v0') if state is not None else None
        exits.append({'instruction':term.index,
            'v0_after_delay_slot':value.describe(precise=True) if value else 'unresolved',
            'block_instructions':[i.text for i in block.instructions],
            'authority':'must-dataflow register observation; not proof of a C return expression'})
    return {'kind':'coordinated-type-transaction',
        'public_abi':{k:v for k,v in abi.items() if k != 'known_header_types'},
        'member_uses':uses[:160], 'omitted_member_uses':max(0,len(uses)-160),
        'pointer_assignment_candidates':assignments[:100], 'return_sites':returns,
        'target_return_registers':exits,
        'rules':[
            'One connected type hypothesis may require many edits. Apply declarations and all dependent uses atomically.',
            'Do not redeclare header typedefs. Reuse their actual field names or use a distinctly named candidate-local view.',
            'Preserve control lines (if/switch/case/loops); a line slot replaces the whole line, not just an expression on it.',
            'Use only slot/new edits. Replace an opening function line with itself plus typed local declarations to insert locals.',
            'Keep public parameter types; rename parameters if needed, then introduce differently named typed locals.',
            'Match candidate header structures against target offsets; a similarly named base struct may be too small.',
            'The source-use graph is a syntactic index, not binary evidence. Union variants may depend on branch conditions.',
            'Keep byte-copy indexing byte-sized when typing pointers; do not accidentally multiply the stride.',
            'Resolve return values from target control/data flow. Do not add arbitrary return 0 just to silence C errors.',
            'Do not edit headers or use macros to hide types. Compiler and differential results adjudicate every hypothesis.']}
