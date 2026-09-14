"""Target-backed byte-array address-unit candidates; not semantic proof."""
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def propose(source,function,assembly,diagnostics):
    report={'source':source,'changes':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'scope':'stack-name/base plus unscaled binary index correspondence; source index identity unproven'}
    if 'incompatible pointer types assigning' not in diagnostics:return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end]
    flow=dataflow.analyse(assembly)
    if not flow.graph.instructions:return report
    first=flow.graph.instructions[0]
    if first.opcode!='addiu' or tuple(map(dataflow.reg,first.operands[:2]))!=('sp','sp'):return report
    frame=dataflow.number(first.operands[2])
    if frame is None or frame>=0:return report
    for decl in re.finditer(r'(?m)^[ \t]*u8\s+(sp[0-9A-Fa-f]+)\s*\[\s*(0x[0-9a-fA-F]+|[1-9][0-9]*)\s*\]\s*;',body):
        name=decl[1];extent=int(decl[2],0);base=frame+int(name[2:],16)
        if extent>256 or base+extent>0:continue
        assignments=list(re.finditer(r'(?m)^[ \t]*(\w+)\s*=\s*&'+name+r'\s*\+\s*\w+\s*;',body))
        if not assignments:continue
        valid=False
        for assignment in assignments:
            line=source.count('\n',0,definition.end()+assignment.start())+1
            text=source.splitlines()[line-1]
            if (re.search(r'candidate\.c:'+str(line)+r":\d+: error: incompatible pointer types assigning to 'u8 \*'",diagnostics)
                    and re.search(r'\n\s*'+str(line)+r' \| '+re.escape(text)+r'(?:\n|$)',diagnostics)):
                valid=True
        if not valid:continue
        witnesses=[]
        for insn in flow.graph.instructions:
            state=flow.instruction_in.get(insn.index)
            if insn.opcode!='addu' or not state:continue
            inputs=[state.registers.get(dataflow.reg(x)) for x in insn.operands[1:]]
            if dataflow.Value.address('stack',base) in inputs:
                witnesses.append(insn.index)
        if not witnesses:continue
        uses=list(re.finditer(r'&'+name+r'\b(?=\s*\+\s*\w+\s*[,;)])',body))
        if not uses or re.search(r'\b'+name+r'\b',definition[2]):continue
        edits=[(definition.end()+m.start(),definition.end()+m.start()+1) for m in uses]
        candidate=source
        for a,b in reversed(edits):candidate=candidate[:a]+candidate[b:]
        report.update(source=candidate,changes=[{'array':name,'extent':extent,'target_base':base,'add_instructions':witnesses,'edits':edits}])
        return report
    return report
