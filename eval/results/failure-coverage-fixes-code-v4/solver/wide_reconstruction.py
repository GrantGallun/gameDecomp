"""Closed unsigned word-pair C idioms over measured header fields.

This emits candidate source, not evidence or a semantic certificate. Unknown
offset names and m2c argument annotations remain hypotheses; the compiler and
differential runner must adjudicate every candidate. No function names are used.
"""
import hashlib
import re

from solver import project_headers


def reconstruct(source, layouts):
    clean=project_headers._mask_noncode(source)
    fields=[]
    for typename,members in layouts.items():
        objects=re.findall(r'(?m)^[ \t]*'+re.escape(typename)+r'\s*\*\s*(\w+)\s*;',clean)
        for obj in objects:
            for field in members:
                if field.get('canonical')!='unsigned long long' or field.get('width')!=8 or field.get('pointer') or field.get('array'):
                    continue
                offset=field['offset']
                if sum(m.get('offset')==offset for m in members)!=1:
                    continue
                if not re.fullmatch(r'\w+',field['member']):
                    continue
                names={int(m[1],16):m[0] for m in re.finditer(r'\b'+re.escape(obj)+r'->unk_?([0-9a-fA-F]+)\b',clean)}
                if offset in names and offset+4 in names:
                    fields.append({'object':obj,'member':field['member'],'type':typename,
                        'offset':offset,'high':names[offset],'low':names[offset+4],
                        'expression':obj+'->'+field['member']})
    result=source
    changes=[]
    def local_word(name,uses):
        masked=project_headers._mask_noncode(result)
        return bool(re.search(r'\b(?:u32|unsigned\s+int)\s+'+re.escape(name)+r'\s*;',masked)) and len(re.findall(r'\b'+re.escape(name)+r'\b',masked))==uses
    def word(name):
        return bool(re.search(r'\b(?:u32|unsigned\s+int)\s+'+re.escape(name)+r'\s*;',project_headers._mask_noncode(result)))
    def apply(pattern,render,label,guard=lambda m:True):
        nonlocal result
        matches=list(re.finditer(pattern,result))
        for match in reversed(matches):
            if not guard(match):
                continue
            # Require the span to begin in code, never a comment/string example.
            masked=project_headers._mask_noncode(result)
            if not masked[match.start():match.end()].strip():
                continue
            before=match[0]
            after=render(match)
            result=result[:match.start()]+after+result[match.end():]
            changes.append({'kind':label,'before':before,'after':after})
    for field in fields:
        hi,lo=map(re.escape,(field['high'],field['low']))
        expr=field['expression']
        pattern=(r'(?P<tmp>\w+)\s*=\s*'+hi+r';\s*if\s*\(\(\s*(?P=tmp)\s*>=\s*0U\s*\)\s*&&\s*'
                 r'\(\(\s*(?P=tmp)\s*>\s*0U\s*\)\s*\|\|\s*\(\s*(?P<sub>\w+)\s*<\s*\(u32\)\s*'+lo+r'\s*\)\)\)')
        apply(pattern,lambda m:'if ('+expr+' > '+m['sub']+')','unsigned-wide-compare',
              lambda m:local_word(m['tmp'],4) and word(m['sub']))
        pattern=(r'(?P<tmp>\w+)\s*=\s*'+lo+r';\s*'+lo+r'\s*=\s*\(u32\)\s*\(\s*(?P=tmp)\s*-\s*(?P<sub>\w+)\s*\);\s*'
                 +hi+r'\s*=\s*\(u32\)\s*\(\(\s*'+hi+r'\s*-\s*0\s*\)\s*-\s*\(\s*(?P=tmp)\s*<\s*(?P=sub)\s*\)\);')
        apply(pattern,lambda m:expr+' -= '+m['sub']+';','unsigned-wide-borrow',
              lambda m:local_word(m['tmp'],4) and word(m['sub']))
        pattern=(r'(?P<call>\b\w+)\(\s*/\*\s*u64\+0x0\s*\*/\s*'+hi+
                 r'\s*,\s*/\*\s*u64\+0x4\s*\*/\s*'+lo+r'\s*\)')
        apply(pattern,lambda m:m['call']+'('+expr+')','annotated-wide-call-argument')
        for dest in fields:
            dh,dl=map(re.escape,(dest['high'],dest['low']))
            pattern=(r'(?P<a>\w+)\s*=\s*'+hi+r';\s*(?P<b>\w+)\s*=\s*'+lo+
                r';\s*if\s*\(\(\s*(?P=a)\s*!=\s*0\s*\)\s*\|\|\s*\(\s*(?P=b)\s*!=\s*0\s*\)\)\s*\{\s*'
                +dh+r'\s*=\s*(?P=a);\s*'+dl+r'\s*=\s*(?P=b);')
            apply(pattern,lambda m:'if ('+expr+' != 0) {\n            '+dest['expression']+' = '+expr+';',
                  'unsigned-wide-nonzero-copy',lambda m:m['a']!=m['b'] and local_word(m['a'],4) and local_word(m['b'],4))
    report={'kind':'measured-header-wide-operation-candidate','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'candidate_sha256':hashlib.sha256(result.encode()).hexdigest(),'fields':fields,'changes':changes,
        'authority':'measured header layout plus closed draft idioms; compiler-checked hypothesis, not binary-only inference',
        'remaining_word_accesses':re.findall(r'\b\w+->unk_?[0-9a-fA-F]+\b',project_headers._mask_noncode(result))}
    return result,report
