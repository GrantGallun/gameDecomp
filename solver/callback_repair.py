"""Measured-ABI callback call candidates; source correspondence remains hypothetical."""
import hashlib
import re
from solver import dataflow, m2c_byte_view, m2c_input, project_headers, repair_context


def propose(source, function, assembly, candidates):
    report={'changes':[],'declines':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'scope':'measured ABI plus binary/source correspondence candidate; not semantic proof'}
    groups=candidates.get('parameters',[])
    if len(groups)!=1 or groups[0]['status']!='abi_consensus_hypothesis':return source,report
    calls=groups[0]['candidates'][0]['calls']
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    params=[]
    for p in m2c_byte_view.arguments(definition[2]):
        m=re.search(r'\b(\w+)\s*$',p)
        if not m:return source,report
        params.append(m[1])
    pattern=r'\(\*\(([^;{}]+?\(\*\*\)\([^()]*\))\)\(\(u8\s*\*\)\((\w+)\)\s*\+\s*(\d+)\)\)\('
    sites=list(re.finditer(pattern,body))
    if len(sites)!=len(calls):return source,report
    flow=dataflow.analyse(m2c_input.normalize_o32_registers(assembly)[0])
    edits=[];plans=[]
    for site,call in zip(sites,sorted(calls,key=lambda c:c['instruction'])):
        at=call['instruction'];binary=flow.callsites[at]
        path=call['contract']['path'];slot=path[-1]
        if int(site[3])!=slot['offset']:return source,report
        closing=m2c_byte_view.closing(body,site.end()-1)
        old_args=m2c_byte_view.arguments(body[site.end():closing])
        # Only discard/reorder simple value expressions, never calls or writes.
        if any(not re.fullmatch(r'(?:\(\w+\s*\**\)\s*)*&?\s*(?:\w+|0x[0-9A-Fa-f]+)',a) for a in old_args):return source,report
        sp=flow.instruction_in[at].registers.get('sp')
        values=[*binary.arguments,*binary.stack_arguments]
        def render(v,position):
            if v and v.kind=='address' and v.name.startswith('param') and v.offset==0:
                n=int(v.name[5:])
                if n>=len(params):return None
                name=params[n]
                if re.search(r'\b'+name+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)',body):return None
                return name
            if v and v.kind=='address' and v.name=='stack' and sp and sp.kind=='address':
                name='sp'+format(v.offset-sp.offset,'X')
                if re.search(r'\b(?:s16|u16|s32|u32)\s+'+name+r'\s*;',body):return '&'+name
            if v and v.kind=='load' and v.width==4 and v.offset==0 and v.inner:
                inner=v.inner
                if inner.kind=='address' and inner.name=='stack' and inner.offset>=16 and inner.offset%4==0:
                    n=inner.offset//4
                    return params[n] if n<len(params) else None
                if inner.kind=='address' and inner.name.startswith('param') and inner.offset==0:
                    n=int(inner.name[5:]);name=site[2]
                    if n<len(params) and re.search(r'\b'+name+r'\s*=\s*\(\*\(\w+\s*\*\*\)\(\(u8\s*\*\)\('+params[n]+r'\)\s*\+\s*0\)\)',body):return name
            if v is None and position<4:
                # m2c's physical-FPR temporary is an explicit candidate bridge.
                for ins in reversed(flow.graph.instructions[max(0,at-24):at]):
                    if ins.opcode in dataflow.WRITES_FIRST and dataflow.reg(ins.operands[0])=='a'+str(position):
                        if ins.opcode!='mfc1':break
                        name='temp_'+dataflow.reg(ins.operands[1])
                        if (re.search(r'\bs32\s+'+name+r'\s*;',body)
                            and len(re.findall(r'\b'+name+r'\s*=(?!=)',body))==1
                            and re.search(r'\b'+name+r'\s*=\s*\(s32\)\s*\w+\s*;',body[:site.start()])
                            and any(re.search(r'\b'+name+r'\b',a) for a in old_args)):
                            return name
                        break
            return None
        count=call['contract']['abi']['argument_words']
        args=[render(v,i) for i,v in enumerate(values[:count])]
        if len(args)!=count or None in args:
            report['declines'].append({'instruction':at,'reason':'unresolved source argument correspondence','arguments':args})
            return source,report
        canonical=slot['canonical'].replace('(*)','(**)',1)
        replacement=f'(*({canonical})((u8 *)({site[2]}) + {slot["offset"]}))('+', '.join(args)+')'
        edits.append((definition.end()+site.start(),definition.end()+closing+1,replacement))
        plans.append({'instruction':at,'before_arguments':old_args,'after_arguments':args,'canonical':slot['canonical']})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    report['changes']=plans
    return source,report
