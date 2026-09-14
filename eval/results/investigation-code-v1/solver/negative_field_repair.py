"""Typed negative-offset pseudo-field lowering; compile-tested hypotheses only."""
import hashlib
import re
from solver import project_headers, repair_context


def propose(source, function, assembly, diagnostics):
    report = {'source': source, 'changes': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'scope': 'typed byte-view hypothesis; target offset correspondence is not root proof'}
    if 'member reference base type' not in diagnostics:
        return report
    definition, end = repair_context.definition(source, function)
    mask = project_headers._mask_noncode(source)
    body = mask[definition.end():end-1]
    types = {'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'u32':4,'f32':4,'float':4}
    widths = {'lb':1,'lbu':1,'sb':1,'lh':2,'lhu':2,'sh':2,'lw':4,'sw':4,'lwc1':4,'swc1':4}
    observed = set()
    for m in re.finditer(r'\b(lb|lbu|sb|lh|lhu|sh|lw|sw|lwc1|swc1)\s+\$?\w+\s*,\s*-(0x[\da-fA-F]+|\d+)\(\$?(\w+)\)', assembly):
        if m[3] not in ('sp','fp','s8'):
            observed.add((int(m[2],0), widths[m[1]], m[1] in ('lwc1','swc1')))
    edits=[]
    for use in re.finditer(r'\b(\w+)\s*->\s*unk-([\da-fA-F]+)\b',body):
        name, literal = use.groups()
        declarations = list(re.finditer(r'(?m)^[ \t]*('+ '|'.join(types)+r')\s*(?:\*\s*'+re.escape(name)+r'|\(\s*\*\s*'+re.escape(name)+r'\s*\)\s*\[\s*[1-9]\d*\s*\])\s*;',body))
        all_declarations = re.findall(r'(?m)^[ \t]*[\w ]+\s*(?:\*\s*'+re.escape(name)+r'|\(\s*\*\s*'+re.escape(name)+r'\s*\)\s*\[\s*[1-9]\d*\s*\]|\b'+re.escape(name)+r')\s*[;=]',body)
        if len(declarations)!=1 or len(all_declarations)!=1 or re.search(r'\b'+re.escape(name)+r'\b', definition[2]):
            continue
        typ=declarations[0][1]; offset=int(literal,16)
        if not 0 < offset <= 65536 or (offset,types[typ],typ in ('f32','float')) not in observed:
            continue
        replacement=f'(*({typ} *)((unsigned char *){name} - 0x{offset:X}))'
        start, stop=definition.end()+use.start(),definition.end()+use.end()
        edits.append((start,stop,replacement))
        report['changes'].append({'base':name,'type':typ,'byte_offset':-offset,'before':source[start:stop],'after':replacement})
    if len(edits)>128:
        report['changes']=[]
        return report
    for start,stop,replacement in reversed(edits):
        report['source']=report['source'][:start]+replacement+report['source'][stop:]
    return report
