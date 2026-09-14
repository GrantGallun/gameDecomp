"""Call-address and post-call-load bounded output storage hypotheses."""
import hashlib
import re
from solver import dataflow, project_headers, repair_context, m2c_byte_view


def propose(source,function,assembly):
    report={'source':source,'changes':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'authority':'call/stack-spacing candidate; extent, lifetime and callee output effects are not proven'}
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    flow=dataflow.analyse(assembly)
    frames={s.registers['sp'].offset for s in flow.instruction_in.values()
            if s.registers.get('sp') and s.registers['sp'].kind=='address'
            and s.registers['sp'].name=='stack' and s.registers['sp'].offset<0}
    if len(frames)!=1:return report
    frame=next(iter(frames))
    decls=list(re.finditer(r'(?m)^[ \t]*(u8|s8|s16|u16|s32|u32)\s+(sp[0-9A-Fa-f]+)\s*;',body))
    for root in decls:
        if root[1]!='u8':continue
        if sum(d[2]==root[2] for d in decls)!=1 or re.search(r'\b'+root[2]+r'\b',definition[2]):continue
        offset=int(root[2][2:],16)
        higher=sorted(int(d[2][2:],16) for d in decls if int(d[2][2:],16)>offset)
        if not higher:continue
        boundary=higher[0];size=boundary-offset
        if offset<16 or not 4<=size<=256 or boundary>-frame:continue
        if not any(a.address==dataflow.Value.address('stack',frame+boundary) for a in flow.accesses.values()):continue
        calls=[]
        for call in re.finditer(r'\b(\w+)\s*\(',body):
            stop=m2c_byte_view.closing(body,call.end()-1)
            args=m2c_byte_view.arguments(body[call.end():stop]);at=call.end()
            for slot,arg in enumerate(args):
                pos=body.find(arg,at,stop);at=pos+len(arg)
                if arg!='&'+root[2] or slot>3:continue
                peers=[c for c in flow.callsites.values() if c.target==call[1] and
                       c.arguments[slot]==dataflow.Value.address('stack',frame+offset)]
                if len(peers)==1:calls.append((pos,at,peers[0]))
        if len(calls)!=1:continue
        aliases={m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b',body)
                 if offset<=int(m[0][2:],16)<boundary}
        if len(aliases)<2:continue
        edits=[(root.start(),root.end(),f'    union {{ u32 alignment; u8 bytes[{size}]; }} {root[2]};'),
               (calls[0][0],calls[0][1],root[2]+'.bytes')]
        rows=[];failed=False
        for alias in aliases:
            off=int(alias[2:],16)-offset
            if alias!=root[2] and re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+alias+r'\s*[;=\[]',body):
                failed=True;break
            reads=[a for a in flow.accesses.values() if a.is_load and a.instruction>calls[0][2].instruction
                   and a.address==dataflow.Value.address('stack',frame+offset+off)]
            types={a.opcode for a in reads}
            if len(types)!=1 or next(iter(types)) not in {'lbu','lhu','lw'}:
                failed=True;break
            width={'lbu':1,'lhu':2,'lw':4}[next(iter(types))]
            if off+width>size:failed=True;break
            uses=[m for m in re.finditer(r'\b'+alias+r'\b',body)
                  if not root.start()<=m.start()<root.end() and not calls[0][0]<=m.start()<calls[0][1]]
            if any(re.search(r'(?:&|\+\+|--)\s*$',body[:m.start()]) or re.match(r'\s*(?:[\[.]|=(?!=)|[+*/%&|^\-]=|<<=|>>=|\+\+|--)',body[m.end():]) for m in uses):
                failed=True;break
            terms=[f'((u32){root[2]}.bytes[{off+i}] << {8*(width-i-1)})' for i in range(width)]
            expr='('+' | '.join(terms)+')'
            if width==4:expr='((s32)'+expr+')'
            edits.extend((m.start(),m.end(),expr) for m in uses)
            rows.append({'alias':alias,'width':width,'load_instructions':[a.instruction for a in reads]})
        if failed:continue
        for a,b,text in sorted(edits,reverse=True):
            source=source[:definition.end()+a]+text+source[definition.end()+b:]
        report.update(source=source,changes=[{'root':root[2],'extent':size,'next_slot':boundary,
                      'call_instruction':calls[0][2].instruction,'aliases':rows}])
        return report
    return report
