"""Restore a single header-wide parameter split into named big-endian words."""
import hashlib
import re

from solver import buildtypes,project_headers,repair_context,type_transaction


def propose(repo,source,function,*,big_endian_o32=False):
    report={'source':source,'changes':[],
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'authority':'header ABI plus named high/low working-local hypothesis'}
    if not big_endian_o32:return report
    definition,end=repair_context.definition(source,function)
    parts=re.fullmatch(r'\s*(s32|u32)\s+(\w+)_unk0\s*,\s*(s32|u32)\s+\2_unk4\s*',definition[2])
    if not parts:return report
    abi=type_transaction.contract(repo,source,function)
    if abi['status']!='locked':return report
    returns,params=abi['shape']
    if len(params)!=1 or len(params[0])!=1 or tuple(definition[1].strip().split())!=returns:return report
    typ=params[0][0]; root=parts[2]
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    if re.search(r'\b'+root+r'\b',body):return report
    if any(re.search(r'(?m)^\s*\w+\s+\**\s*'+root+suffix+r'\s*[;=]',body) for suffix in ('_unk0','_unk4')):return report
    aliases={}; evidence=[]
    include_root=(repo/'include').resolve()
    paths=set()
    for inc in re.findall(r'(?m)^\s*#include\s*[<"]([^>"\n]+)[>"]',source):
        paths.update(buildtypes.closure(repo,'include/'+inc))
    for path in sorted(paths):
        if path.suffix!='.h' or not path.resolve().is_relative_to(include_root):continue
        text=path.read_text(errors='replace')
        for match in re.finditer(r'\btypedef\s+([\w\s]+?)\s+(\w+)\s*;',project_headers._mask_noncode(text)):
            aliases.setdefault(match[2],set()).add(' '.join(match[1].split()))
        evidence.append({'path':str(path),'sha256':hashlib.sha256(text.encode()).hexdigest()})
    canonical=typ; seen=set()
    while canonical in aliases and canonical not in seen and len(aliases[canonical])==1:
        seen.add(canonical);canonical=next(iter(aliases[canonical]))
    if canonical not in {'unsigned long long','long long','signed long long'}:return report
    locals_=f'\n    {parts[1]} {root}_unk0 = ({parts[1]}) ((unsigned long long){root} >> 32);\n    {parts[3]} {root}_unk4 = ({parts[3]}) {root};'
    result=source[:definition.end()]+locals_+source[definition.end():]
    result=result[:definition.start(2)]+typ+' '+root+result[definition.end(2):]
    report.update(source=result,header_evidence=evidence)
    report['changes']=[{'parameter':root,'type':typ,'canonical':canonical,'old_parameters':definition[2],
                        'body_unchanged':True,'abi':abi['shape']}]
    return report
