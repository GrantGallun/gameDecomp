"""Unknown prototypes for closed discarded-result, literal word calls.

Candidate-local ABI projection, not a recovered public callee signature.
"""
from collections import Counter
import hashlib
import re
from solver import dataflow, project_headers, repair_context


def propose(source,function,assembly):
    report={'source':source,'changes':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'scope':'literal word call-site projection; discarded result, not original callee signature'}
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end]
    flow=None;edits=[]
    for proto in re.finditer(r'(?m)^[ \t]*\?\s+(\w+)\s*\((\s*\?(?:\s*,\s*\?){0,3}\s*)\)\s*;',mask[:definition.start()]):
        name=proto[1];count=proto[2].count('?')
        outside=mask[:proto.start()]+mask[proto.end():definition.start()]+mask[end:]
        if re.search(r'\b'+re.escape(name)+r'\b',outside):continue
        calls=list(re.finditer(r'(?m)^[ \t]*'+re.escape(name)+r'\(([^();]+)\)\s*;',body))
        if not calls or len(calls)!=len(re.findall(r'\b'+re.escape(name)+r'\b',body)):continue
        expected=[]
        for call in calls:
            args=[a.strip() for a in call[1].split(',')]
            if len(args)!=count or any(not re.fullmatch(r'0x[0-9A-Fa-f]+|0|[1-9][0-9]*',a) for a in args):break
            values=tuple(int(a,0) for a in args)
            if any(v>0x7fffffff for v in values):break
            expected.append(values)
        if len(expected)!=len(calls):continue
        if flow is None:flow=dataflow.analyse(assembly)
        sites=[]
        for insn in flow.graph.instructions:
            if insn.opcode!='jal' or insn.operands!=(name,):continue
            site=flow.callsites.get(insn.index)
            if site is None:
                # An unresolved indirect jump can hide a reachable switch arm
                # from whole-function flow. Only block-local constants qualify;
                # symbolic entry parameters cannot satisfy the checks below.
                block=flow.graph.blocks[flow.graph.instruction_to_block[insn.index]]
                prefix=[]
                if len(block.predecessors)==1:
                    predecessor=flow.graph.blocks[next(iter(block.predecessors))]
                    if predecessor.id!=block.id:
                        prefix=list(predecessor.instructions)
                local=dataflow.analyse('\n'.join(i.text for i in [*prefix,*block.instructions]))
                matches=[c for c in local.callsites.values() if c.target==name]
                if len(matches)!=1:continue
                site=matches[0]
            sites.append(site)
        target_count=sum(i.opcode=='jal' and i.operands==(name,) for i in flow.graph.instructions)
        if len(sites)!=target_count:continue
        observed=[]
        for site in sites:
            args=site.arguments[:count]
            if any(a is None or a.kind!='constant' for a in args):break
            observed.append(tuple(a.offset for a in args))
        if len(observed)!=len(sites) or Counter(observed)!=Counter(expected):continue
        replacement='void '+name+'('+', '.join(['s32']*count)+');'
        edits.append((proto.start(),proto.end(),replacement))
        report['changes'].append({'callee':name,'before':proto[0],'after':replacement,
            'call_instructions':[i.index for i in flow.graph.instructions if i.opcode=='jal' and i.operands==(name,)],
            'literal_argument_words':expected,'evidence_scope':'whole-function or block-local constants; no reachability proof'})
    for a,b,text in reversed(edits):report['source']=report['source'][:a]+text+report['source'][b:]
    return report
