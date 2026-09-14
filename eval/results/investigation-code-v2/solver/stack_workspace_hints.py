"""Bounded workspace capacity hypotheses from loops, header layouts and assembly.

Header array capacity is not a runtime bound on the loop counter. These plans
must pass normal validation and retain that obligation; they are not proof.
"""
import hashlib
import re

from solver import cfg, dataflow, m2c_byte_view, m2c_input, project_headers, repair_context


def propose(source, function, assembly, layouts):
    report={'plans':[], 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
            'authority':'header capacity correspondence hypothesis; not runtime bound proof'}
    definition,end=repair_context.definition(source,function)
    body=project_headers._mask_noncode(source)[definition.end():end-1]
    params=dict((m[2],m[1]) for m in re.finditer(r'\b(\w+)\s*\*\s*(\w+)',definition[2]))
    instructions,_=cfg.parse_assembly(assembly)
    for decl in re.finditer(r'(?m)^\s*(?:\?|M2C_UNK)\s+(sp[0-9A-Fa-f]+)\s*;',body):
        root=decl[1]; offset=int(root[2:],16)
        choices=[]
        for seed in re.finditer(r'\b(var_(s[0-7]))(?:_\d+)?\s*=\s*&'+root+r'\s*;',body):
            # Use the full generated local name, including its epoch suffix.
            cursor=re.match(r'\w+',seed[0])[0]; register=seed[2]
            after=body[seed.end():]
            loop=re.match(r'\s*(?:for\s*\(\s*;\s*;\s*\)|do)\s*\{',after)
            if not loop:
                continue
            start=seed.end()+loop.end()-1
            depth=1; stop=start
            while depth and stop+1<len(body):
                stop+=1
                depth+=(body[stop]=='{')-(body[stop]=='}')
            if depth:
                continue
            region=body[start:stop+1]
            tail=body[stop+1:stop+180]
            counts=re.findall(r'\b(\w+)\s*<\s*(\w+)->(\w+)',region+tail)
            if len(set(counts))!=1:
                continue
            counter,owner,count_member=counts[0]
            if owner not in params or not re.search(r'\b'+counter+r'\s*=\s*0\s*;',body[:seed.start()]):
                continue
            if len(re.findall(r'\b'+counter+r'\s*\+=\s*1\s*;',region))!=1:
                continue
            steps=re.findall(r'\b'+cursor+r'\s*\+=\s*(0x[\da-fA-F]+|\d+)\s*;',region)
            if len(steps)!=1:
                continue
            stride=int(steps[0],0)
            if not 2<=stride<=256 or stride%2:
                continue
            fields={int(x,16) for x in re.findall(r'\b'+cursor+r'->unk([\da-fA-F]+)\b',region)}
            if not fields or any(x%2 or x+2>stride for x in fields):
                continue
            witnesses=[]
            for i,ins in enumerate(instructions):
                if ins.opcode!='addiu' or tuple(dataflow.reg(x) for x in ins.operands[:2])!=(register,'sp'):
                    continue
                try:
                    if int(ins.operands[2],0)!=offset: continue
                except (ValueError,IndexError):
                    continue
                stores=set()
                for j in range(i+1,min(i+513,len(instructions))):
                    nxt=instructions[j]
                    if nxt.opcode=='sh' and len(nxt.operands)==2:
                        memory=re.fullmatch(r'(0x[\da-fA-F]+|\d+)\(\$?'+register+r'\)',nxt.operands[1])
                        if memory: stores.add(int(memory[1],0))
                    if dataflow._destination(nxt)==register:
                        if nxt.opcode=='addiu' and dataflow.reg(nxt.operands[1])==register:
                            try:
                                if int(nxt.operands[2],0)==stride and fields<=stores:
                                    witnesses.append([i,j])
                            except ValueError:
                                pass
                        break
            if not witnesses:
                continue
            measured=layouts.get(params[owner],[])
            bounds=[f for f in measured if f['member']==count_member and not f.get('array') and f.get('width') in (1,2,4)]
            arrays=[]
            for f in measured:
                match=re.fullmatch(r'.+\[(\d+)\]',f.get('canonical',''))
                if f.get('array') and match and re.search(r'\b'+owner+r'->'+re.escape(f['member'])+r'\s*\[',region):
                    capacity=int(match[1])
                    if 1<=capacity<=256 and f.get('width',0)>0:
                        arrays.append((capacity,f))
            if len(bounds)!=1 or not arrays or len({c for c,_ in arrays})!=1:
                continue
            capacity=arrays[0][0]
            hint=(function,offset,'s16',capacity*stride//2)
            try:
                m2c_input.stack_context(assembly,(hint,))
            except ValueError:
                continue
            choices.append({'hint':hint,'cursor':cursor,'stride':stride,
                'counter':counter,'count_field':bounds[0], 'capacity_fields':[f for _,f in arrays],
                'target_witnesses':witnesses,'field_offsets':sorted(fields),
                'debt':['loop count must not exceed associated header array capacity',
                        'source register/loop and workspace lifetime correspondence remain hypotheses']})
        if choices and len({tuple(c['hint']) for c in choices})==1:
            report['plans'].append({'root':root,'stack_variables':[choices[0]['hint']], 'evidence':choices})
    return report


def recover(repo,ws,source,function,target):
    from solver import type_constraints,type_transaction
    abi=type_transaction.contract(repo,source,function)
    if abi['status']!='locked':
        raise ValueError('workspace redraft requires locked public ABI')
    measurement=type_constraints.measure(repo,ws,source,function,target)
    assembly=(ws/'target.s').read_text()
    report=propose(source,function,assembly,measurement['layouts'])
    report['layout_receipt']=measurement['receipt_path']
    report['candidates']=[]
    headers=tuple(dict.fromkeys(re.findall(r'(?m)^\s*#include\s+"([^"\n]+)"',source)))
    for plan in report['plans'][:2]:
        result,meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers,
            stack_variables=tuple(plan['stack_variables']),valid_syntax=True)
        if result.returncode:
            plan['decline']=result.stderr[-1000:] or result.stdout[-1000:]
            continue
        definition,_=repair_context.definition(result.stdout,function)
        if type_transaction.signature(definition[0],function)!=abi['shape']:
            plan['decline']='hinted draft changes public ABI'
            continue
        candidate=''.join(f'#include "{h}"\n' for h in headers)+result.stdout
        lowered=m2c_byte_view.lower(candidate,function,known_types=abi.get('known_header_types',()),target_assembly=assembly)
        report['candidates'].append(lowered['source'])
        plan['draft']=meta
        plan['lowering']={k:v for k,v in lowered.items() if k!='source'}
    return report
