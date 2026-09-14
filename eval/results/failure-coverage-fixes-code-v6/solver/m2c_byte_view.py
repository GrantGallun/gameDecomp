"""Experimental lowering of fresh m2c field macros to typed byte accesses.

No layout or C-type facts are created. Global pointer views and inferred bare
dereference types are candidate hypotheses requiring compiler/runtime validation.
"""
import hashlib
import re
from solver import project_headers, repair_context


def closing(text, at):
    depth=0
    for i in range(at,len(text)):
        if text[i]=='(': depth+=1
        elif text[i]==')':
            depth-=1
            if depth==0: return i
    raise ValueError('unbalanced expression')


def arguments(text):
    parts=[]
    start=depth=0
    for i,ch in enumerate(text):
        if ch=='(': depth+=1
        elif ch==')': depth-=1
        elif ch==',' and depth==0:
            parts.append(text[start:i].strip()); start=i+1
    return parts+[text[start:].strip()]


def lower(source,function):
    masked=project_headers._mask_noncode(source)
    definition,end=repair_context.definition(source,function)
    start=definition.end()
    body=masked[start:end-1]
    if 'M2C_FIELD' in masked[:start]+masked[end:]:
        raise ValueError('field macros outside selected function')
    globals_=dict((m[1],m) for m in re.finditer(r'(?m)^extern s32 (\w+);',masked[:start]))
    aliases={name:name for name in globals_}
    hypotheses=[]
    for local in re.findall(r'(?m)^\s*void\s*\*\s*(\w+)\s*;',body):
        assignments=re.findall(r'\b'+re.escape(local)+r'\s*=\s*([^;]+);',body)
        roots=[]
        for expression in assignments:
            match=re.match(r'(\w+)\s*\+',expression)
            roots.append(match[1] if match and match[1] in globals_ else None)
        if roots and None not in roots and len(set(roots))==1:
            aliases[local]=roots[0]
    # Each root must be used only in byte-address additions inside this body.
    for name,decl in globals_.items():
        outside=masked[:decl.start()]+' '*(decl.end()-decl.start())+masked[decl.end():start]+masked[end:]
        if re.search(r'\b'+re.escape(name)+r'\b',outside):
            raise ValueError('global used outside selected function: '+name)
        for use in re.finditer(r'\b'+re.escape(name)+r'\b',body):
            if not re.match(r'\s*\+',body[use.end():]):
                raise ValueError('global has non-address use: '+name)
    fields=[]
    zero_types={}
    for match in re.finditer(r'\bM2C_FIELD\s*\(',body):
        stop=closing(body,match.end()-1)
        args=arguments(body[match.end():stop])
        if len(args)!=3 or not re.fullmatch(r'(?:s8|u8|s16|u16|s32|u32)\s*\*',args[1]):
            raise ValueError('unsupported field macro')
        if not re.fullmatch(r'(?:0x[0-9a-fA-F]+|\d+)',args[2]):
            raise ValueError('nonliteral field offset')
        root=aliases.get(args[0])
        fields.append({'base':args[0],'type':args[1],'offset':int(args[2],0),'root':root})
        if root and int(args[2],0)==0:
            zero_types.setdefault(root,set()).add(args[1])
    candidate=source
    # Bare dereferences are only admitted at a known root's indexed address.
    # The type comes from unanimous offset-zero field uses in the same root family.
    bare=[]
    for match in re.finditer(r'\*\s*\(\s*(\w+)\s*\+',body):
        root=match[1]
        if root not in globals_: continue
        types=zero_types.get(root,set())
        if len(types)!=1:
            raise ValueError('missing/ambiguous zero-offset type: '+root)
        paren=body.index('(',match.start())
        stop=closing(body,paren)
        bare.append((start+paren+1,'('+next(iter(types))+')('))
        bare.append((start+stop+1,')'))
        hypotheses.append({'kind':'bare-dereference-type','root':root,'type':next(iter(types)),
            'source_span':body[match.start():stop+1]})
    for at,text in sorted(bare,reverse=True):
        candidate=candidate[:at]+text+candidate[at:]
    # Expand inner macros first, so nested macro arguments stay balanced.
    while True:
        clean=project_headers._mask_noncode(candidate)
        matches=list(re.finditer(r'\bM2C_FIELD\s*\(',clean))
        if not matches: break
        match=matches[-1]; stop=closing(clean,match.end()-1)
        base,ctype,offset=arguments(candidate[match.end():stop])
        candidate=candidate[:match.start()]+f'(*({ctype})((u8 *)({base}) + {offset}))'+candidate[stop+1:]
    for name in globals_:
        candidate=candidate.replace('extern s32 '+name+';','extern u8 *'+name+';',1)
        hypotheses.append({'kind':'byte-pointer-global-view','name':name})
    return {'source':candidate,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'fields':fields,'hypotheses':hypotheses,'aliases':aliases,
        'scope':'fresh-draft candidate lowering, not binary facts or a semantic certificate'}
