"""Closed o32 output-buffer + two-wide-operand reconstruction candidates."""
import hashlib
import re
from solver import dataflow, liveness, project_headers, repair_context, stack_result_evidence


def propose(source,function,assembly,diagnostics,callees):
    evidence=stack_result_evidence.analyse(source,function,assembly,diagnostics,callees)
    report={'source':source,'changes':[],'evidence':evidence,
            'scope':'coordinated o32 candidate; complete ABI and semantic validation still required'}
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source); body=mask[definition.end():end]
    flow=dataflow.analyse(assembly);insns=flow.graph.instructions
    def stack_source(at,register):
        for i in range(at-1,max(-1,at-16),-1):
            insn=insns[i]
            if insn.opcode in {'jal','jalr'}:
                if i==at-1 and flow.callsites.get(i) and flow.callsites[i].delay_slot==at:continue
                return None
            if dataflow._destination(insn)!=register:continue
            if insn.opcode!='lw':return None
            mem=re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)\(\$?sp\)',insn.operands[1])
            if not mem:return None
            offset=int(mem[1],0)
            names=re.findall(r'(?m)^[ \t]*s32\s+(sp[0-9A-Fa-f]+)\s*;',body)
            matches=[n for n in names if int(n[2:],16)==offset]
            return matches[0] if len(matches)==1 else None
        return None
    for row in evidence['rows']:
        callee,root=row['callee'],row['source_buffer']
        if row['callee_first_pointer_word_writes']!=[0,4,8,12]:continue
        call=next(c for c in flow.callsites.values() if c.instruction==row['caller_instruction'])
        # Two register words and exactly two outgoing stack words. Only source
        # locals explicitly associated with direct target stack loads qualify.
        words=[stack_source(call.instruction,'a2'),stack_source(call.instruction,'a3')]
        stores=row['outgoing_stack_stores']
        frame=flow.instruction_in[call.instruction].registers['sp'].offset
        for offset in (16,20):
            peers=[s for s in stores if s['entry_sp_offset']==frame+offset and s['width']==4]
            if len(peers)!=1:break
            store=insns[peers[0]['instruction']]
            words.append(stack_source(store.index,dataflow.reg(store.operands[0])))
        if len(words)!=4 or any(w is None for w in words):continue
        # Require the callee's own entry stack loads at o32 words 4/5 and
        # saved incoming a2/a3; this is not inferred from its function name.
        peer=dataflow.analyse(callees[callee])
        if any((('a1' in liveness._def_use(i)[1]) or i.opcode in {'jal','jalr'})
               and peer.instruction_in.get(i.index)
               and peer.instruction_in[i.index].registers.get('a1')==dataflow.Value.address('param1')
               for i in peer.graph.instructions):continue
        entry_reads={a.address.offset for a in peer.accesses.values() if a.is_load and a.width==4
                     and a.address and a.address.kind=='address' and a.address.name=='stack' and a.address.offset>=0}
        if not {16,20}<=entry_reads:continue
        proto=list(re.finditer(r'(?m)^\s*void\s+'+re.escape(callee)+r'\(s32 \*\w+, s32 \w+, s32 \w+\);',mask[:definition.start()]))
        sites=list(re.finditer(r'\b'+re.escape(callee)+r'\(\s*&'+root+r'\s*,\s*(sp[0-9A-Fa-f]+)\s*,\s*(sp[0-9A-Fa-f]+)\s*\)',body))
        decls=list(re.finditer(r'(?m)^[ \t]*s32\s+'+root+r'\s*;',body))
        if len(proto)!=1 or len(sites)!=1 or len(decls)!=1 or list(sites[0].groups())!=words[2:]:continue
        outside=mask[:proto[0].start()]+mask[proto[0].end():definition.start()]+mask[end:]
        if re.search(r'\b'+re.escape(callee)+r'\b',outside):continue
        aliases={a['local']:a['relative_offset']//4 for a in row['missing_aliases']}
        base=int(root[2:],16)
        span_names={m[0] for m in re.finditer(r'\bsp[0-9A-Fa-f]+\b',body) if base<=int(m[0][2:],16)<base+16}
        if span_names!={root,*aliases}:continue
        if any(re.search(r'(?m)^\s*\w+\s+\**\s*'+name+r'\s*[;=]',body) for name in aliases):continue
        if re.search(r'\b'+root+r'\b',definition[2]):continue
        def pack(hi,lo):return f'(s64)(((u64)(u32){hi} << 32) | (u32){lo})'
        replacement=f'{callee}({root}, {pack(*words[:2])}, {pack(*words[2:])})'
        edits=[(proto[0].start(),proto[0].end(),f'void {callee}(s32 *, s64, s64);'),
               (definition.end()+decls[0].start(),definition.end()+decls[0].end(),f'    s32 {root}[4];'),
               (definition.end()+sites[0].start(),definition.end()+sites[0].end(),replacement)]
        for use in re.finditer(r'\b(?:'+ '|'.join([root,*aliases])+r')\b',body):
            if decls[0].start()<=use.start()<decls[0].end() or sites[0].start()<=use.start()<sites[0].end():continue
            edits.append((definition.end()+use.start(),definition.end()+use.end(),f'{root}[{aliases.get(use[0],0)}]'))
        candidate=source
        for a,b,text in sorted(edits,reverse=True):candidate=candidate[:a]+text+candidate[b:]
        report.update(source=candidate,changes=[{'callee':callee,'buffer':root,'words':words,
            'aliases':aliases,'prototype':proto[0][0],'call':replacement}])
        return report
    return report
