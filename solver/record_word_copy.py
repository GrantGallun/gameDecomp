"""Measured whole-record copy candidates for misrendered byte-member stores."""
import hashlib
import re
import subprocess
from solver import dataflow, m2c_byte_view, project_headers, repair_context, type_constraints

PATTERN=re.compile(r'(?m)^[ \t]*((\w+)->(\w+)\[(\w+)\.(\w+)\])\.(\w+)\s*=\s*M2C_UNALIGNED32\((\w+)\)\s*;')


def record_type(spelling):
    return re.sub(r'^(?:struct|union)\s+', '',re.sub(r'\[[0-9]+\]', '',spelling).strip())


def propose(source,function,assembly,measurement):
    report={'source':source,'changes':[],'declines':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'authority':'measured record plus binary copy candidate; not semantic or byte-exact proof'}
    if measurement.get('source_sha256')!=report['source_sha256'] or measurement.get('kind')!='target-compiler-header-layouts':
        report['declines'].append('missing/stale measurement');return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    flow=dataflow.analyse(assembly);ins=flow.graph.instructions
    globals_=measurement.get('global_declarations',[]);layouts=measurement.get('layouts',{})
    edits=[]
    for match in PATTERN.finditer(body):
        expr,dest,member,index,index_member,field,src=match.groups()
        def declaration(name):
            rows={g['canonical'] for g in globals_ if g['name']==name}
            return next(iter(rows)) if len(rows)==1 else ''
        def one_field(typ,name):
            rows=[f for f in layouts.get(typ,[]) if f['member']==name]
            return rows[0] if len(rows)==1 else {}
        dest_type,index_type=map(declaration,(dest,index))
        table=one_field(record_type(dest_type),member)
        array=re.fullmatch(r'(?:struct\s+)?(\w+)\[([0-9]+)\]',table.get('canonical','').replace(' [','['))
        record=array[1] if array else ''
        first=one_field(record,field)
        idx=one_field(record_type(index_type),index_member)
        if (not re.fullmatch(r'(?:struct\s+)?\w+\[[0-9]+\]',dest_type.replace(' [','['))
                or not array
                or first.get('offset')!=0 or first.get('width')!=1 or first.get('owner_size')!=4
                or table.get('width')!=4*int(array[2]) or not table.get('array')
                or idx.get('width')!=2 or idx.get('spelling')!='s16'):
            report['declines'].append({'destination':expr,'reason':'record/array/index layout mismatch'});continue
        if any(re.search(r'\b'+n+r'\b',definition[2]) or re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+n+r'\s*[;=\[]',body) for n in (dest,src,index)):
            continue
        witnesses=[]
        for at in range(len(ins)-4):
            load,shift,add,left,right=ins[at:at+5]
            if at<2:continue
            hi,lo=ins[at-2:at]
            load_mem=dataflow.MEMORY.fullmatch(', '.join(load.operands))
            if (hi.opcode!='lui' or lo.opcode!='addiu' or hi.operands[1]!=f'%hi({src})'
                    or lo.operands[2]!=f'%lo({src})' or not load_mem
                    or dataflow.reg(hi.operands[0])!=dataflow.reg(lo.operands[1])
                    or dataflow.reg(lo.operands[0])!=dataflow.reg(load_mem['base'])):continue
            if [i.opcode for i in (load,shift,add,left,right)]!=['lw','sll','addu','swl','swr']:continue
            if len({flow.graph.instruction_to_block[i.index] for i in (load,shift,add,left,right)})!=1:continue
            access=flow.accesses.get(load.index)
            a=dataflow.MEMORY.fullmatch(', '.join(left.operands));b=dataflow.MEMORY.fullmatch(', '.join(right.operands))
            if not access or access.address!=dataflow.Value.address(src) or not a or not b:continue
            if (a['value']!=b['value'] or dataflow.reg(a['value'])!=dataflow.reg(load.operands[0])
                    or a['base']!=b['base'] or dataflow.number(a['offset'])!=table.get('offset')
                    or dataflow.number(b['offset'])!=table.get('offset')+3 or dataflow.number(shift.operands[2])!=2):continue
            shift_state=flow.instruction_in[shift.index];add_state=flow.instruction_in[add.index]
            value=shift_state.registers.get(dataflow.reg(shift.operands[1]))
            if value!=dataflow.Value.loaded(dataflow.Value.address(index,idx['offset']),width=2,signed=True):continue
            destreg,ar,br=map(dataflow.reg,add.operands)
            shifted=dataflow.reg(shift.operands[0])
            if destreg!=dataflow.reg(a['base']):continue
            if not ((ar==shifted and add_state.registers.get(br)==dataflow.Value.address(dest))
                    or (br==shifted and add_state.registers.get(ar)==dataflow.Value.address(dest))):continue
            if dataflow.reg(load.operands[0]) in {shifted,destreg}:continue
            witnesses.append([i.index for i in (load,shift,add,left,right)])
        if len(witnesses)!=1:
            report['declines'].append({'destination':expr,'reason':'requires unique matching indexed word copy'});continue
        suffix=0
        while re.search(r'\b_record_copy_'+str(suffix)+r'_',mask):suffix+=1
        prefix='_record_copy_'+str(suffix)
        replacement='{ u32 '+prefix+'_word = (u32)('+m2c_byte_view.byte_word_expression('&'+src,0)+'); '
        replacement+='u8 *'+prefix+'_dst = (u8 *)&('+expr+'); '
        replacement+=' '.join(prefix+f'_dst[{n}] = (u8)('+prefix+f'_word >> {24-8*n});' for n in range(4))+' }'
        edits.append((definition.end()+match.start(),definition.end()+match.end(),replacement))
        report['changes'].append({'before':match[0],'after':replacement,'record':record,
            'record_size':4,'array_field':table,'first_member':first,'target_instructions':witnesses})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    report['source']=source;return report


def recover(repo,ws,source,function,assembly,target):
    matches=list(PATTERN.finditer(project_headers._mask_noncode(source)))
    if not matches:return {'source':source,'changes':[],'declines':[]}
    roots=tuple(dict.fromkeys(n for m in matches for n in (m[2],m[4])))
    try:
        measured=type_constraints.measure(repo,ws,source,function,target,layout_globals=roots)
        result=propose(source,function,assembly,measured)
        result['measurement_receipt']=measured['receipt_path']
        return result
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return {'source':source,'changes':[],'declines':[str(exc)]}
