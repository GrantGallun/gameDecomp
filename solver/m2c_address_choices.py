"""Opt-in finite C hypotheses for verified m2c address-add observations.

The supported grammar is deliberately small. Lexical related uses are not CFG,
alias analysis or instruction ownership. Named result types come from current C,
not invented layouts. Primitive array sizes use the N64 MIPS32 ABI; struct and
unknown typedef layouts decline array alternatives. Every child needs the native
compiler/frontend/object gates; none of these hypotheses establishes semantics.
"""
from __future__ import annotations

import json
import re

from solver import m2c_uncertainty as uncertainty, project_headers, repair_context

IDENT = r'[A-Za-z_]\w*'
TYPE = rf'(?:(?:const|volatile)\s+)*(?:(?:struct|union)\s+{IDENT}|{IDENT}(?:\s+(?:char|short|int|long))?)'


def _unwrap(text):
    text=text.strip()
    while text.startswith('(') and text.endswith(')'):
        depth=0
        for i,c in enumerate(text):
            depth+=(c=='(')-(c==')')
            if depth==0:break
        if i!=len(text)-1:break
        text=text[1:-1].strip()
    return text


def _split(text):
    text=_unwrap(text); depth=0
    additions=[]
    for i,c in enumerate(text):
        depth+=(c in '([')-(c in ')]')
        if c=='+' and depth==0:additions.append(i)
    if len(additions)!=1:return None
    i=additions[0]
    return _unwrap(text[:i]), _unwrap(text[i+1:])


def _size(typ, masked, visited=()):
    typ=' '.join(re.sub(r'\b(?:const|volatile)\b','',typ).split())
    widths={'char':1,'signed char':1,'unsigned char':1,'short':2,'short int':2,
            'unsigned short':2,'unsigned short int':2,'int':4,'signed int':4,
            'unsigned int':4,'long':4,'unsigned long':4,'float':4,'double':8}
    if typ in widths:return widths[typ]
    if typ in visited or len(visited)>=4:return None
    aliases=re.findall(r'\btypedef\s+('+TYPE+r')\s+'+re.escape(typ)+r'\s*;',masked)
    return _size(aliases[0],masked,visited+(typ,)) if len(aliases)==1 else None


def _index(offset, size):
    text=_unwrap(offset)
    if re.fullmatch(r'0x[0-9a-fA-F]+|\d+',text):
        value=int(text,0) if text.startswith('0x') else int(text)
        return str(value//size) if value%size==0 else None
    match=re.fullmatch(r'('+IDENT+r')\s*\*\s*(0x[0-9a-fA-F]+|\d+)',text)
    if match:
        value=int(match[2],0) if match[2].startswith('0x') else int(match[2])
        if value%size==0:
            factor=value//size
            return match[1] if factor==1 else f'{match[1]} * {factor}'
    return None


def _types(masked, definition, end):
    # Ordinary named pointer parameters and leading/local pointer declarations.
    # Function pointers, comma declarators and typedef-to-pointer names decline.
    candidates={}
    for match in re.finditer(r'\b('+TYPE+r')\s*\*\s*('+IDENT+r')\s*(?=[,);])',
                             masked[definition.start():end]):
        candidates.setdefault(match[2],[]).append(' '.join(match[1].split())+' *')
    for match in re.finditer(r'\bextern\s+('+TYPE+r')\s+('+IDENT+r')\s*;',masked[:definition.start()]):
        candidates.setdefault('&'+match[2],[]).append(' '.join(match[1].split())+' *')
    return {name:values[0] for name,values in candidates.items() if len(values)==1}


def _dependencies(source, masked, definition, end, base, offset, start):
    names=list(dict.fromkeys(re.findall(IDENT,base+' '+offset)))
    line_start=masked.rfind('\n',0,start)+1
    prefix=masked[line_start:start]
    dest=re.search(r'\b('+IDENT+r')\s*=\s*$',prefix)
    if dest and dest[1] not in names:names.append(dest[1])
    # Include simple assignment aliases, retaining a visible bounded inventory.
    for _ in range(2):
        for lhs,rhs in re.findall(r'\b('+IDENT+r')\s*=\s*('+IDENT+r')\s*;',masked[definition.end():end]):
            if rhs in names and lhs not in names and len(names)<8:names.append(lhs)
    occurrences=[];callees=[];seen=set();omitted=0
    lines=source.splitlines()
    for name in names:
        for match in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)',masked[definition.start():end]):
            line=source.count('\n',0,definition.start()+match.start())+1
            if line in seen:continue
            seen.add(line)
            if len(occurrences)>=12:omitted+=1;continue
            text=lines[line-1].strip()
            occurrences.append({'line':line,'excerpt':text[:500]})
            callees.extend(re.findall(r'\b('+IDENT+r')\s*\(',project_headers._mask_noncode(text)))
    declarations=[]
    for callee in dict.fromkeys(callees):
        if callee in {'if','while','for','sizeof'}:continue
        pattern=r'(?m)^[ \t]*(?:extern\s+)?[\w *]+\b'+re.escape(callee)+r'\s*\([^;{}]*\)\s*;'
        declarations.extend(m[0].strip()[:500] for m in re.finditer(pattern,masked[:definition.start()]))
    return {'identifiers':names,'occurrences':occurrences,'omitted_occurrences':omitted,
            'callee_declarations':list(dict.fromkeys(declarations))[:6],
            'scope':'lexical uses; no CFG, alias or complete ownership proof',
            'gaps':'headers, indirect calls, nested declarations and aliasing unresolved'}


def build(source, report, *, function, max_choices=6):
    if type(max_choices) is not int or not 1<=max_choices<=12:raise ValueError('choice cap must be 1..12')
    result={'source_sha256':uncertainty.sha(source),'choices':[],'declines':[],
            'omitted_choices':0,'retain_root':True,'training_eligible':False,
            'scope':'finite single-expression hypotheses; compiler gates required'}
    if report.get('source_sha256')!=result['source_sha256']:
        return {**result,'status':'stale','declines':['stale source report']}
    try:definition,end=repair_context.definition(source,function)
    except ValueError:
        return {**result,'status':'unavailable','declines':['ambiguous function']}
    masked=project_headers._mask_noncode(source)
    types=_types(masked,definition,end);seen=set()
    packet=uncertainty.packet(source,report,max_regions=12)
    for region in packet['regions']:
        instruction=region.get('instruction') or {}
        reason=None
        if (region['kind']!='unrecovered-address-add' or instruction.get('synthetic')
                or instruction.get('byte_attribution_status')!='verified-target-word'):
            reason='unverified operation'
        elif len(region['c_locations'])!=1 or region.get('omitted_c_locations'):
            reason='ambiguous expression occurrences'
        if reason:
            result['declines'].append(reason);continue
        location=region['c_locations'][0];start,stop=location['start'],location['stop']
        if not definition.end()<=start<stop<end:
            result['declines'].append('expression outside function body');continue
        old=source[start:stop];parts=_split(old)
        if not parts:
            result['declines'].append('unsupported address grammar');continue
        left,right=parts
        if left in types:base,offset=left,right
        elif right in types:base,offset=right,left
        else:
            result['declines'].append('unknown current pointer result type');continue
        # No calls, writes, dereferences, address escapes or member-based offsets.
        pure=re.fullmatch(r'(?:'+IDENT+r'|0x[0-9a-fA-F]+|\d+)(?:\s*\*\s*(?:0x[0-9a-fA-F]+|\d+))?',_unwrap(offset))
        if not pure:
            result['declines'].append('unsupported or effectful offset');continue
        typ=types[base]
        alternatives=[('byte-view',f'(({typ})((unsigned char *)({base}) + ({offset})))',
                       ['current named result type is a hypothesis; machine byte-add association is partial'])]
        size=_size(typ.rstrip(' *'),masked)
        index=_index(offset,size) if size and size==region.get('target_size_hypothesis') else None
        if index is not None:
            alternatives.append(('array-view',f'({base} + ({index}))',
                ['N64 primitive size from current C; offset factor divisible by element size; no struct layout inferred']))
        dependencies=_dependencies(source,masked,definition,end,base,offset,start)
        for kind,new,assumptions in alternatives:
            child=source[:start]+new+source[stop:]
            digest=uncertainty.sha(child)
            if child==source or digest in seen:continue
            seen.add(digest)
            edit_old,edit_new=old,new
            if source.count(edit_old)!=1:
                a=source.rfind('\n',0,start)+1;b=source.find('\n',stop)
                if b<0:b=len(source)
                edit_old=source[a:b];edit_new=source[a:start]+new+source[stop:b]
            if source.count(edit_old)!=1 or len(edit_old)+len(edit_new)>1800:
                result['declines'].append('ambiguous textual edit');continue
            if len(result['choices'])>=max_choices:
                result['omitted_choices']+=1;continue
            result['choices'].append({'id':digest[:16],'kind':kind,'old':edit_old,'new':edit_new,
                'child_source_sha256':digest,'instruction':instruction,'dependencies':dependencies,
                'assumptions':assumptions,'result_type_authority':'current C declaration, not original layout'})
    if not packet['regions']:result['declines'].append('no surviving observed expressions')
    result['status']='partial' if result['omitted_choices'] or result['declines'] or packet['status']=='partial' else 'bounded'
    return result


def validate(source, candidate, menu):
    if uncertainty.sha(source)!=menu['source_sha256']:raise ValueError('stale choice parent')
    if candidate==source:return 'retain'
    match=[c for c in menu['choices'] if uncertainty.sha(candidate)==c['child_source_sha256']]
    if len(match)!=1:raise ValueError('candidate is outside the fixed address-choice menu')
    return match[0]['id']


def render(source, menu):
    if uncertainty.sha(source)!=menu['source_sha256']:raise ValueError('stale choice parent')
    return ('\nFINITE M2C ADDRESS CHOICES:\n'+json.dumps(menu,separators=(',',':'))+
        '\nUse the existing edit schema to select ONE exact old/new alternative above. '
        'Inspect its related assignments and calls. Do not change declarations, add casts elsewhere, '
        'combine choices or invent layouts. Other edits will be rejected and logged. '
        'Explain the predicted compiler effect in hypothesis. Retaining the root is always allowed '
        '(an unchanged old/new replacement is a duplicate). These are hypotheses, not semantic proofs.\n')
