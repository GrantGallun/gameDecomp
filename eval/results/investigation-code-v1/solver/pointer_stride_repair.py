"""Measured record cursor steps retaining target byte units, as candidates."""
import hashlib
import re
from solver import cfg,dataflow,project_headers,repair_context,type_constraints


def propose(source,function,assembly,layouts):
    report={'source':source,'changes':[], 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'authority':'source/global/loop correspondence hypothesis; compiler and differential checks required'}
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    graph=cfg.build(assembly); instructions=graph.instructions; loops=graph.natural_loops()
    edits=[]
    for declaration in re.finditer(r'(?m)^\s*(\w+)\s*\*\s*(\w+)\s*;',body):
        typ,name=declaration.groups()
        sizes={f['owner_size'] for f in layouts.get(typ,[]) if 'owner_size' in f}
        if len(sizes)!=1:continue
        size=next(iter(sizes))
        if size<=1:continue
        assignments=re.findall(r'\b'+name+r'\s*=(?!=)\s*([^;]+);',body)
        if len(assignments)!=1 or not re.fullmatch(r'\w+',assignments[0].strip()):continue
        symbol=assignments[0].strip()
        if re.search(r'(?m)^\s*\w+\s+\**\s*'+symbol+r'\s*[;=]',body):continue
        for step in re.finditer(r'\b'+name+r'\s*\+=\s*(0x[\da-fA-F]+|\d+)\s*;',body):
            stride=int(step[1],0)
            if stride!=size:continue
            witnesses=[]
            for seed in instructions:
                if seed.opcode!='addiu' or len(seed.operands)!=3 or seed.operands[2]!='%lo('+symbol+')':continue
                register,base=map(dataflow.reg,seed.operands[:2])
                previous=next((i for i in reversed(instructions[max(0,seed.index-16):seed.index])
                               if dataflow._destination(i)==base),None)
                if not previous or previous.opcode!='lui' or previous.operands[1]!='%hi('+symbol+')':continue
                saved={}; valid=True
                for ins in instructions[seed.index+1:]:
                    if ins.opcode=='sw' and len(ins.operands)==2 and '(sp)' in ins.operands[1].replace('$',''):
                        saved[ins.operands[1].replace('$','')]=valid and dataflow.reg(ins.operands[0])==register
                    if ins.opcode in {'jal','jalr','bal'} and register not in {f's{i}' for i in range(8)}:
                        valid=False
                    if dataflow._destination(ins)!=register:continue
                    if ins.opcode=='lw' and len(ins.operands)==2 and saved.get(ins.operands[1].replace('$','')):
                        valid=True
                        continue
                    if (ins.opcode=='addiu' and len(ins.operands)==3 and
                            dataflow.reg(ins.operands[1])==register and valid):
                        try:amount=int(ins.operands[2],0)
                        except ValueError:break
                        if amount==stride and any(graph.instruction_to_block[ins.index] in loop.nodes
                                and seed.index<graph.blocks[loop.header].start for loop in loops):
                            witnesses.append([seed.index,ins.index])
                    break
            if len(witnesses)!=1:continue
            replacement=f'{name} = ({typ} *)((unsigned char *){name} + {step[1]});'
            edits.append((definition.end()+step.start(),definition.end()+step.end(),replacement))
            report['changes'].append({'local':name,'type':typ,'global':symbol,'record_size':size,
                                      'byte_stride':stride,'instructions':witnesses,'before':step[0],'after':replacement})
    for a,b,replacement in sorted(edits,reverse=True):
        source=source[:a]+replacement+source[b:]
    report['source']=source
    return report


def recover(repo,ws,source,function,assembly,target):
    if not re.search(r'\b\w+\s*\+=\s*(?:0x[\da-fA-F]+|[2-9]\d*)\s*;',source):
        return {'source':source,'changes':[]}
    measured=type_constraints.measure(repo,ws,source,function,target)
    report=propose(source,function,assembly,measured['layouts'])
    report['layout_receipt']=measured['receipt_path']
    return report
