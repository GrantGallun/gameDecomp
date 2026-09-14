"""Read-only caller/callee evidence for missing stack-result aliases.

Assembly observations only: not a recovered C ABI, object extent proof or fix.
"""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def analyse(source, function, assembly, diagnostics, callees):
    report={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'rows':[], 'scope':'extracted assembly observations; no ABI or object-layout proof'}
    missing=set(re.findall(r"undeclared identifier '(sp[0-9A-Fa-f]+)'",diagnostics))
    if not missing:return report
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end]
    flow=dataflow.analyse(assembly)
    for site in re.finditer(r'\b(\w+)\s*\(\s*&\s*(sp[0-9A-Fa-f]+)\s*,',body):
        callee,local=site.groups()
        if callee not in callees:continue
        calls=[c for c in flow.callsites.values() if c.target==callee]
        if len(calls)!=1 or len(re.findall(r'\b'+re.escape(callee)+r'\s*\(',body))!=1:continue
        call=calls[0]
        state=flow.instruction_in.get(call.instruction)
        frame=state.registers.get('sp') if state else None
        if not frame or frame.kind!='address' or frame.name!='stack':continue
        base=frame.offset+int(local[2:],16)
        if call.arguments[0]!=dataflow.Value.address('stack',base):continue
        target=callees[callee]
        peer=dataflow.analyse(target)
        stores=[a for a in peer.accesses.values() if not a.is_load and a.address
                and a.address.kind=='address' and a.address.name=='param0'
                and 0<=a.address.offset<256 and a.width==4]
        writes=sorted({a.address.offset for a in stores})
        aliases=[]
        for name in sorted(missing):
            relative=int(name[2:],16)-int(local[2:],16)
            reads=[a.instruction for a in flow.accesses.values() if a.is_load and a.width==4
                   and a.address==dataflow.Value.address('stack',base+relative)
                   and a.instruction>call.instruction]
            if relative in writes and reads:
                aliases.append({'local':name,'relative_offset':relative,'caller_loads':reads})
        if not aliases:continue
        outgoing=[{'instruction':a.instruction,'entry_sp_offset':a.address.offset,'width':a.width}
                  for a in flow.accesses.values() if not a.is_load and a.address
                  and a.address.kind=='address' and a.address.name=='stack'
                  and frame.offset+16<=a.address.offset<frame.offset+32
                  and call.instruction-12<=a.instruction<=call.instruction+1]
        report['rows'].append({'callee':callee,'callee_assembly_sha256':hashlib.sha256(target.encode()).hexdigest(),
            'source_buffer':local,'caller_instruction':call.instruction,'entry_sp_base':base,
            'callee_first_pointer_word_writes':writes,
            'callee_store_instructions':[a.instruction for a in stores],
            'missing_aliases':aliases,'outgoing_stack_stores':outgoing,
            'argument_word_observations':[str(v) for v in call.arguments],
            'next_action':'reconstruct result storage and validate complete ABI together; do not declare uninitialized aliases or assume all paths write the buffer'})
    return report


def load_callees(repo,source,function,diagnostics):
    callees={}
    if re.search(r"undeclared identifier 'sp[0-9A-Fa-f]+'",diagnostics):
        definition,end=repair_context.definition(source,function)
        body=project_headers._mask_noncode(source)[definition.end():end]
        names=sorted(set(re.findall(r'\b(\w+)\s*\(\s*&\s*sp[0-9A-Fa-f]+\s*,',body)))
        for name in names[:16]:
            paths=list((repo/'asm'/'matchings').rglob(name+'.s'))
            if len(paths)==1:
                callees[name]=paths[0].read_text(errors='replace')
    return callees


def collect(repo,source,function,assembly,diagnostics):
    return analyse(source,function,assembly,diagnostics,load_callees(repo,source,function,diagnostics))


def validate_candidate(before, after, report):
    """Reject new uninitialized aliases disconnected from observed outputs.

    This is a proposal guard, not acceptance of other ABI/storage strategies.
    Explicit assignments, buffer views and address-passed locals remain subject
    to normal compiler and differential checking.
    """
    if report.get('source_sha256')!=hashlib.sha256(before.encode()).hexdigest():
        return
    mask=project_headers._mask_noncode(after)
    for row in report.get('rows',[]):
        for alias in row['missing_aliases']:
            name=alias['local']
            declaration=re.compile(r'(?m)^[ \t]*(?:s32|u32|int|unsigned int)\s+'+re.escape(name)+r'\s*;')
            if declaration.search(project_headers._mask_noncode(before)):
                continue
            if not declaration.search(mask):continue
            uses=declaration.sub('',mask)
            if re.search(r'&\s*\b'+re.escape(name)+r'\b|\b'+re.escape(name)+r'\s*=(?!=)',uses):
                continue
            raise ValueError(f'disconnected stack-result alias {name}: {row["callee"]} writes '
                f'{row["source_buffer"]}+{alias["relative_offset"]}; a new uninitialized scalar '
                'does not receive that output. Reconstruct result storage and complete call ABI together.')
