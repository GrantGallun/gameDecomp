"""Source-bound type plans: the model selects views, deterministic code acts.

Narrow pointer/member dialect, not a general C parser. Offset labels in a draft
are hypotheses, checked against headers by compiler probes and against the target
by ordinary compilation/differential validation. No shared header edits.
"""
import hashlib
import json
import re
from pathlib import Path

from solver import project_headers, repair_context, type_transaction, frontend_check

NAME = r'[A-Za-z_]\w*'
CHAIN = re.compile(r'\b('+NAME+r')((?:\s*->\s*'+NAME+r')+)')
FIELD = re.compile(r'unk_?([0-9A-Fa-f]+)$')
SCHEMA = {'type':'object','required':['hypothesis','types','fields'],'additionalProperties':False,
 'properties':{
  'hypothesis':{'type':'string'},
  'types':{'type':'array','maxItems':32,'items':{'type':'object','required':['variable','type'],
   'additionalProperties':False,'properties':{'variable':{'type':'string'},'type':{'type':'string'}}}},
  'fields':{'type':'array','maxItems':96,'items':{'type':'object','required':['base','field','member'],
   'additionalProperties':False,'properties':{'base':{'type':'string'},'field':{'type':'string'},'member':{'type':'string'}}}}}}


def inventory(source):
    uses = {}
    for match in CHAIN.finditer(project_headers._mask_noncode(source)):
        base = match[1]
        for member in re.findall(r'->\s*('+NAME+')',match[2]):
            if FIELD.fullmatch(member):
                uses[(base,member)] = int(FIELD.fullmatch(member)[1],16)
            base += '->'+member
    return [{'base':b,'field':f,'offset_hypothesis':o} for (b,f),o in sorted(uses.items())]


def prompt(source, headers, abi, errors=''):
    return ('Select a connected TYPE PLAN for this decompiler draft, not source-line edits.\n'
        'Return JSON with hypothesis, types [{variable,type}], fields [{base,field,member}].\n'
        'Types are existing header pointee names (no star). Include void-pointer parameters and locals used in member chains. '
        'Fields map EVERY listed base/unk field to a real header member path; nested embedded fields use dots. '
        'A base may be a chain such as p->unkC, whose pointee comes from the mapped first field. '
        'Do not invent structs. Distinguish a base struct from a larger struct embedding it, and union arms by branch. '
        'The controller creates typed locals without changing public parameters, repairs declarations atomically, '
        'and probes each field offset. You do not write statements, returns, or line numbers.\n'
        'PUBLIC ABI: '+json.dumps({k:v for k,v in abi.items() if k!='known_header_types'})+
        '\nCURRENT SOURCE:\n'+source+'\nREQUIRED FIELD MAPPINGS:\n'+json.dumps(inventory(source))+
        '\nINCLUDED HEADER CONTEXT:\n'+headers+'\nREJECTED PLAN/COMPILER FEEDBACK:\n'+errors[-4000:])


def prepare(source, plan, function, abi):
    if not isinstance(plan,dict) or set(plan) != {'hypothesis','types','fields'}:
        raise ValueError('expected hypothesis/types/fields type plan')
    if not isinstance(plan['hypothesis'],str) or len(plan['hypothesis']) > 6000:
        raise ValueError('invalid or oversized hypothesis')
    if not isinstance(plan['types'],list) or not 1 <= len(plan['types']) <= 32:
        raise ValueError('type plan requires 1..32 pointer views')
    types = {}
    for row in plan['types']:
        if (not isinstance(row,dict) or set(row) != {'variable','type'}
                or not all(isinstance(v,str) for v in row.values())
                or not re.fullmatch(NAME,row['variable']) or not re.fullmatch(NAME,row['type'])):
            raise ValueError('invalid type view')
        if row['variable'] in types or row['type'] not in abi.get('known_header_types',[]):
            raise ValueError('duplicate variable or type not provided by included headers')
        types[row['variable']] = row['type']
    required = {(r['base'],r['field']):r['offset_hypothesis'] for r in inventory(source)}
    if not isinstance(plan['fields'],list) or len(plan['fields']) > 96:
        raise ValueError('too many field mappings')
    mappings = {}
    for row in plan['fields']:
        if (not isinstance(row,dict) or set(row) != {'base','field','member'}
                or not all(isinstance(v,str) for v in row.values())
                or not re.fullmatch(NAME+r'(?:\.'+NAME+r')*',row['member'])):
            raise ValueError('field member must be an existing dotted member path')
        key = row['base'],row['field']
        if key not in required or key in mappings:
            raise ValueError('duplicate or nonexistent source access')
        mappings[key] = row['member']
    if set(mappings) != set(required):
        missing = sorted(set(required)-set(mappings))
        raise ValueError('plan must cover all connected accesses; missing '+str(missing[:20]))
    match,end = repair_context.definition(source,function)
    body_start = match.end()
    params = set(re.findall(r'\bvoid\s*\*\s*('+NAME+r')\b',match[2]))
    mask = project_headers._mask_noncode(source)
    edits, views = [], {}
    for var, typ in types.items():
        if var in params:
            view = 'typed_'+var
            if re.search(r'\b'+view+r'\b',mask): raise ValueError('typed view name collision')
            views[var] = view
        else:
            declarations = list(re.finditer(r'\bvoid\s*\*\s*'+re.escape(var)+r'\s*;',mask[body_start:end]))
            if len(declarations) != 1:
                raise ValueError('requires one uninitialized void-pointer declaration for '+var)
            d = declarations[0]
            edits.append((body_start+d.start(),body_start+d.end(),f'{typ} *{var};'))
    probes = []
    # Only preserve byte arithmetic when the original draft explicitly used
    # this member as a void-pointer local. An unkNN field could also be an
    # integer: casting every member-plus-constant to u8* would corrupt it.
    pointer_sources = set()
    for var in types:
        if var not in params:
            for assignment in re.finditer(r'\b'+re.escape(var)+r'\s*=\s*('+NAME+r'(?:\s*->\s*'+NAME+r')+)\s*;',mask):
                pointer_sources.add(re.sub(r'\s+','',assignment[1]))
    def translated(base, for_probe=False):
        pieces = base.split('->')
        root = pieces[0]
        if root not in types: raise ValueError('missing pointer type for '+root)
        out = f'(({types[root]} *)0)' if for_probe else views.get(root,root)
        original = root
        for field in pieces[1:]:
            member = mappings.get((original,field),field)
            out += '->'+member
            original += '->'+field
        return out
    for (base,field),offset in required.items():
        probes.append(f'typedef char type_plan_offset_{len(probes)}[(__builtin_offsetof(__typeof__(*({translated(base,True)})), {mappings[base,field]}) == {offset}) ? 1 : -1];')
    for use in CHAIN.finditer(mask,body_start,end):
        full = re.sub(r'\s+','',use.group())
        if not any(FIELD.fullmatch(x) for x in full.split('->')[1:]): continue
        replacement = translated(full)
        # Draft void-pointer arithmetic was byte-sized. Preserve that unit when
        # assigning a typed member; do not multiply a byte offset by struct size.
        tail = mask[use.end():end]
        if re.match(r'\s*[+-]\s*(?:0[xX][\da-fA-F]+|\d+)\b',tail):
            if full not in pointer_sources:
                raise ValueError('ambiguous member arithmetic for '+full+'; requires an explicit source-level type/arithmetic hypothesis')
            replacement = '((u8 *)('+replacement+'))'
        edits.append((use.start(),use.end(),replacement))
    local_text = ''.join(f'\n    {types[var]} *{view} = ({types[var]} *){var};' for var,view in views.items())
    if local_text: edits.append((body_start,body_start,local_text))
    candidate = source
    for start,stop,text in sorted(edits,reverse=True): candidate = candidate[:start]+text+candidate[stop:]
    # A local type plan is an explicit view, including union-prefix alternatives.
    # Cast simple pointer assignments to that view; no initializer/call is moved.
    for var,typ in types.items():
        if var in params: continue
        candidate = re.sub(r'(?m)(\b'+re.escape(var)+r'\s*=\s*)('+NAME+r'(?:->'+NAME+r'|\.'+NAME+r')+)\s*;',
                           lambda m:m[1]+f'({typ} *)('+m[2]+');',candidate)
    if len(candidate)-len(source) > type_transaction.MAX_GROWTH:
        raise ValueError('type plan exceeds source growth limit')
    type_transaction.validate(source,candidate,function,abi)
    includes = '\n'.join(re.findall(r'(?m)^\s*#\s*include[^\n]+',source))
    return candidate, includes+'\n'+'\n'.join(probes)+'\n'


def apply(repo, ws, source, plan, function, abi, target):
    candidate, probe = prepare(source,plan,function,abi)
    digest = hashlib.sha256((source+json.dumps(plan,sort_keys=True)).encode()).hexdigest()
    path = ws/('type_plan_'+digest+'.probe.c')
    path.write_text(probe)
    report = frontend_check.check(repo,path,target)
    if report.get('passed') is not True:
        raise ValueError('header layout probe rejected plan: '+report.get('diagnostics','')[:3000])
    return candidate, {'kind':'compiler-checked-type-plan','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'plan':plan,'probe_path':str(path),'probe':report,
        'authority':'header-layout checked; draft offset labels remain hypotheses until target validation'}
