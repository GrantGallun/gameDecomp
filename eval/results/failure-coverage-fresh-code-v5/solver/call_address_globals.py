"""Incomplete byte-array views for closed named addresses passed to calls."""
import hashlib
import re
from solver import dataflow, m2c_byte_view, project_headers, repair_context


def witnesses(assembly, name):
    flow=dataflow.analyse(assembly);ins=flow.graph.instructions;rows=[]
    for call in flow.callsites.values():
        block=flow.graph.instruction_to_block[call.instruction]
        start=max(flow.graph.blocks[block].start,call.instruction-32)
        registers={};product=None
        prior_delays={c.delay_slot for c in flow.callsites.values() if c.instruction<call.instruction}
        for i in ins[start:call.delay_slot+1]:
            state=flow.instruction_in.get(i.index)
            def get(operand):
                r=dataflow.reg(operand)
                if r in registers:return registers[r]
                value=state.registers.get(r) if state else None
                if value==dataflow.Value.address(name):return ('address',0,0)
                if value and value.kind=='constant':return ('constant',value.offset)
                return None
            op=i.opcode;args=i.operands;dest=dataflow._destination(i);value=None
            if op=='lui' and len(args)==2:
                hi=re.fullmatch(r'%hi\('+re.escape(name)+r'(?:\s*\+\s*(0x[0-9a-fA-F]+|\d+))?\)',args[1])
                if hi:value=('high',int(hi[1],0) if hi[1] else 0)
            elif op=='addiu' and len(args)==3:
                source=get(args[1]);lo=re.fullmatch(r'%lo\('+re.escape(name)+r'(?:\s*\+\s*(0x[0-9a-fA-F]+|\d+))?\)',args[2])
                offset=int(lo[1],0) if lo and lo[1] else 0
                amount=dataflow.number(args[2])
                if lo and source==('high',offset):value=('address',offset,0)
                elif amount is not None and source and source[0]=='address':value=('address',source[1]+amount,source[2])
                elif amount is not None and dataflow.reg(args[1])=='zero':value=('constant',amount)
            elif op=='multu' and len(args)==2:
                a,b=get(args[0]),get(args[1]);constants=[v[1] for v in (a,b) if v and v[0]=='constant']
                product=('scaled',constants[0]) if len(constants)==1 and constants[0]>0 else None
            elif op=='mflo':value=product
            elif op in {'addu','or'} and len(args)==3:
                a,b=get(args[1]),get(args[2])
                if dataflow.reg(args[1])=='zero':value=b
                elif dataflow.reg(args[2])=='zero':value=a
                elif op=='addu' and a and b:
                    for base,scale in ((a,b),(b,a)):
                        if base[0]=='address' and base[2]==0 and scale[0]=='scaled':value=('address',base[1],scale[1])
            elif op=='move':value=get(args[1])
            if dest:registers[dataflow.reg(dest)]=value
            if op in {'mult','div','divu','dmult','dmultu','ddiv','ddivu','mthi','mtlo'}:product=None
            if i.index in prior_delays:
                registers.clear();product=None
        for slot in range(4):
            value=registers.get('a'+str(slot))
            if value and value[0]=='address':rows.append({'callee':call.target,'argument':slot,
                'offset':value[1],'stride':value[2],'instruction':call.instruction})
    return rows


def propose(source,function,assembly,symbols):
    report={'source':source,'changes':[],'declines':[],
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest()}
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    edits=[]
    for decl in re.finditer(r'(?m)^extern M2C_UNK (\w+);',mask[:definition.start()]):
        name=decl[1]
        if name not in symbols:continue
        binary=witnesses(assembly,name);accepted=[];used=set()
        for call in re.finditer(r'\b(\w+)\s*\(',body):
            stop=m2c_byte_view.closing(body,call.end()-1)
            args=m2c_byte_view.arguments(body[call.end():stop]);cursor=call.end()
            for slot,arg in enumerate(args):
                at=body.find(arg,cursor,stop);cursor=at+len(arg)
                shape=re.fullmatch(r'&'+name+r'\s*\+\s*(?:\((\w+)\s*\*\s*(0x[0-9a-fA-F]+|\d+)\)\s*\+\s*)?(0x[0-9a-fA-F]+|\d+)',arg)
                if not shape:continue
                stride=int(shape[2],0) if shape[2] else 0;offset=int(shape[3],0)
                matches=[b for b in binary if b['callee']==call[1] and b['argument']==slot and b['stride']==stride and b['offset']==offset and (b['instruction'],slot) not in used]
                if not matches:continue
                used.add((matches[0]['instruction'],slot))
                accepted.append((at,at+len(arg),'(void *)('+arg.replace('&'+name,name,1)+')',[matches[0]]))
        uses=list(re.finditer(r'\b'+name+r'\b',body))
        if not uses or len(uses)!=len(accepted) or any(not any(a<=u.start()<b for a,b,_,_ in accepted) for u in uses):
            report['declines'].append({'global':name,'reason':'not all uses are witnessed call byte addresses'});continue
        outside=mask[:decl.start()]+mask[decl.end():definition.end()]+mask[end:]
        if re.search(r'\b'+name+r'\b',outside):continue
        edits.append((decl.start(),decl.end(),'extern u8 '+name+'[];'))
        edits.extend((definition.end()+a,definition.end()+b,text) for a,b,text,_ in accepted)
        report['changes'].append({'global':name,'address':symbols[name],
            'calls':[m for *_,matches in accepted for m in matches],
            'authority':'incomplete byte-array address candidate; no extent/type or source-index identity proof'})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    report['source']=source;return report
