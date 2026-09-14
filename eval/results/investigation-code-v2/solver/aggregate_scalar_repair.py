"""Measured first-field scalar assignment hypotheses; no aggregate-copy claim."""
import hashlib
import re
from solver import project_headers, repair_context


def propose(source,function,diagnostics,measured, *, target_assembly=''):
    report={'source':source,'changes':[],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'layout_receipt':measured.get('receipt_path'),
            'layout_probe_sha256':measured.get('probe_object_sha256'),
            'scope':'unique zero-offset scalar member hypothesis; compilation and semantic retest required'}
    if (measured.get('kind')!='target-compiler-header-layouts' or
            measured.get('source_sha256')!=report['source_sha256']):
        return {**report,'declines':['missing or stale compiler measurement']}
    definition,end=repair_context.definition(source,function)
    lines=source.splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    edits={}
    for error in re.finditer(r"(?m)^candidate\.c:(\d+):\d+: error: variable has incomplete type 'struct (\w+)'",diagnostics):
        number=int(error[1]);typ=error[2]
        if not 1<=number<=len(lines) or not measured.get('layouts',{}).get(typ):continue
        line=lines[number-1].rstrip('\r\n')
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[error.end():])
        decl=re.fullmatch(r'\s*(struct\s+)'+re.escape(typ)+r'\s+(\w+)\s*;\s*',line)
        if not excerpt or excerpt[1]!=line or not decl:continue
        at=offsets[number-1]+decl.start(1)
        if not definition.end()<=at<end:continue
        # The compiler-measured typedef exists independently of the incomplete
        # tag. Preserve the local and its uses; do not invent a record body.
        before=source[at:at+len(decl[1])]
        edits[at]=''  # removal is applied separately from member insertions
        report.setdefault('tag_removals',[]).append((at,len(before)))
        report['changes'].append({'kind':'measured-typedef-not-tag','type':typ,'local':decl[2]})
    if target_assembly:
        from solver import dataflow
        flow=dataflow.analyse(target_assembly)
        body=project_headers._mask_noncode(source)[definition.end():end-1]
        signed_loads={'lb':('s8',1),'lbu':('u8',1),'lh':('s16',2),'lhu':('u16',2)}
        for glob in measured.get('global_declarations',[]):
            name,typ=glob['name'],glob['spelling']
            peers=[g for g in measured['global_declarations'] if g['name']==name]
            if len(peers)!=1 or typ not in measured.get('layouts',{}):continue
            if re.search(r'\b'+re.escape(name)+r'\b',definition[2]) or re.search(r'(?m)^\s*\w+\s+\**\s*'+re.escape(name)+r'\s*[;=]',body):continue
            accesses=[a for a in flow.accesses.values() if a.address and a.address.name==name]
            loads={a.opcode for a in accesses if a.is_load}
            zero_only=False
            if not loads and len(accesses)==1:
                access=accesses[0]
                uses=list(re.finditer(r'\b'+re.escape(name)+r'\b',body))
                zero_statement=re.search(r'(?m)^[ \t]*'+re.escape(name)+r'\s*=\s*0\s*;',body)
                zero_only=bool(len(uses)==1 and zero_statement and access.width in (1,2) and
                    dataflow.reg(flow.graph.instructions[access.instruction].operands[0])=='zero')
            if zero_only:
                width=accesses[0].width
                # Canonicalize this zero-only write, not the union's signedness.
                # Signed and unsigned integer zero have identical target bits.
                scalar={1:'u8',2:'u16'}[width]
            elif len(loads)==1 and next(iter(loads)) in signed_loads:
                scalar,width=signed_loads[next(iter(loads))]
            else:continue
            if any(a.address!=dataflow.Value.address(name) or a.width!=width or (not a.is_load and a.opcode!={1:'sb',2:'sh'}[width]) for a in accesses):continue
            fields=[f for f in measured['layouts'][typ] if f.get('offset')==0 and f.get('width')==width and f.get('spelling')==scalar and not f.get('pointer') and not f.get('array')]
            if len(fields)!=1 or not re.fullmatch(r'\w+(?:\.\w+)*',fields[0]['member']):continue
            # Require at least one exact current diagnostic involving this
            # symbol/type; compiler locations are not reused across children.
            witnessed=False
            for error in re.finditer(r'(?m)^candidate\.c:(\d+):\d+: error: ([^\n]+)',diagnostics):
                number=int(error[1])
                if not 1<=number<=len(lines) or "'"+typ+"'" not in error[2]:continue
                line=lines[number-1].rstrip('\r\n')
                excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[error.end():])
                if excerpt and excerpt[1]==line and re.search(r'\b'+name+r'\b',line):witnessed=True
            uses=list(re.finditer(r'\b'+re.escape(name)+r'\b',body))
            if not witnessed or not uses:continue
            if any(re.search(r'[&.]\s*$|->\s*$',body[:u.start()]) or re.match(r'\s*[.\[]',body[u.end():]) for u in uses):continue
            for use in uses:edits[definition.end()+use.end()]='.'+fields[0]['member']
            report['changes'].append({'kind':'target-zero-only-global-member' if zero_only else 'target-load-selected-global-member','global':name,'member':fields[0],
                'signedness_inferred':not zero_only,
                'target_accesses':[a.instruction for a in accesses]})
        report['assembly_sha256']=hashlib.sha256(target_assembly.encode()).hexdigest()
    # Closed scalar read/modify/write on a local aggregate is another m2c
    # representation fault. Repair both uses together, only when a measured
    # first array element has the exact requested scalar type and width.
    operand_pattern=r"(?m)^candidate\.c:(\d+):(\d+): error: operand of type '(\w+)'[^\n]* where arithmetic or pointer type is required"
    scalar_width={'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'u32':4}
    for error in re.finditer(operand_pattern,diagnostics):
        number=int(error[1]);typ=error[3]
        if not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n')
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostics.find('\n',error.end()):])
        if not excerpt or excerpt[1]!=line or '\t' in line:continue
        # A scalar cast of a uniquely declared aggregate local can denote its
        # first word in an m2c draft. Never cast the aggregate's address, change
        # the declaration, or infer a first field from its name.
        pos=int(error[2])-1
        body=project_headers._mask_noncode(source)[definition.end():end]
        for cast in re.finditer(r'\((s8|u8|s16|u16|s32|u32)\)\s*(\w+)\b(?!\s*[.\[])',project_headers._mask_noncode(line)):
            scalar,name=cast.groups()
            if not cast.start(2)<=pos<cast.end(2):continue
            declarations=re.findall(r'(?m)^[ \t]*'+re.escape(typ)+r'\s+'+re.escape(name)+r'\s*;',body)
            all_declarations=re.findall(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(name)+r'\s*[;=]',body)
            if len(declarations)!=1 or len(all_declarations)!=1 or re.search(r'\b'+re.escape(name)+r'\b',definition[2]):continue
            fields=[f for f in measured.get('layouts',{}).get(typ,[]) if f.get('offset')==0]
            if len(fields)!=1:continue
            field=fields[0]
            if (field.get('pointer') or field.get('array') or field.get('spelling')!=scalar or
                    field.get('width')!=scalar_width[scalar] or not re.fullmatch(r'\w+(?:\.\w+)*',field['member'])):continue
            at=offsets[number-1]+cast.end(2)
            if not definition.end()<=at<end:continue
            edits[at]='.'+field['member']
            report['changes'].append({'line':number,'aggregate':typ,'scalar':scalar,
                'member':field,'kind':'first-scalar-read','before':cast[0],'suffix':edits[at]})
        operation=re.fullmatch(r'\s*(\w+)\s*=\s*\((s8|u8|s16|u16|s32|u32)\)\s*\(\s*\(\2\)\s*(\1)\s*([+*/-])\s*([1-9][0-9]*)\s*\)\s*;\s*',line)
        if not operation:continue
        name,scalar=operation[1],operation[2]
        body=project_headers._mask_noncode(source)[definition.end():end]
        declarations=re.findall(r'(?m)^[ \t]*'+re.escape(typ)+r'\s+'+re.escape(name)+r'\s*;',body)
        all_declarations=re.findall(r'(?m)^[ \t]*[A-Za-z_]\w*[ \t]+\**\s*'+re.escape(name)+r'\s*[;=]',body)
        if len(declarations)!=1 or len(all_declarations)!=1 or re.search(r'\b'+re.escape(name)+r'\b',definition[2]):continue
        fields=[f for f in measured.get('layouts',{}).get(typ,[]) if f.get('offset')==0]
        if len(fields)!=1:continue
        field=fields[0]
        array=re.fullmatch(re.escape(scalar)+r'\s*\[\s*([1-9][0-9]*)\s*\]',field.get('canonical',''))
        if (not array or not field.get('array') or field.get('pointer') or
                field.get('width')!=int(array[1])*scalar_width[scalar] or not re.fullmatch(r'\w+(?:\.\w+)*',field['member'])):continue
        pos=int(error[2])-1
        if not operation.start(3)<=pos<operation.end(3):continue
        a=offsets[number-1]+operation.end(1);b=offsets[number-1]+operation.end(3)
        if not definition.end()<=a<b<end:continue
        suffix='.'+field['member']+'[0]'
        edits[a]=suffix;edits[b]=suffix
        report['changes'].append({'line':number,'aggregate':typ,'scalar':scalar,'member':field,
            'kind':'first-array-element-read-modify-write','before':line,'suffix':suffix})
    pattern=r"(?m)^candidate\.c:(\d+):(\d+): error: assigning to '(\w+)'[^\n]* from incompatible type '(\w+)'"
    for error in re.finditer(pattern,diagnostics):
        number=int(error[1]); typ=error[3];scalar=error[4]
        if not 1<=number<=len(lines):continue
        line=lines[number-1].rstrip('\r\n')
        excerpt=re.match(r'\n\s*'+str(number)+r' \| (.*)',diagnostics[diagnostics.find('\n',error.end()):])
        if not excerpt or excerpt[1]!=line or '\t' in line:continue
        if scalar not in {'s8','u8','s16','u16','s32','u32','f32','float'}:continue
        fields=[f for f in measured.get('layouts',{}).get(typ,[]) if f.get('offset')==0]
        if len(fields)!=1:continue
        field=fields[0]
        if field.get('pointer') or field.get('array') or field.get('spelling')!=scalar:continue
        if not re.fullmatch(r'\w+(?:\.\w+)*',field['member']):continue
        assignment=re.match(r'\s*(\w+(?:\.\w+)*)\s*=(?!=)',project_headers._mask_noncode(line))
        if not assignment:continue
        at=offsets[number-1]+assignment.end(1)
        if not definition.end()<=at<end:continue
        # Clang points at the assignment operator for this diagnostic.
        pos=int(error[2])-1
        if not assignment.end(1)<=pos<assignment.end():continue
        edits[at]='.'+field['member']
        report['changes'].append({'line':number,'aggregate':typ,'scalar':scalar,'member':field,
                                  'before':assignment[1],'after':assignment[1]+'.'+field['member']})
    if len(edits)>64:return {**report,'changes':[]}
    removals=dict(report.pop('tag_removals',[]))
    for at,text in sorted(edits.items(),reverse=True):
        report['source']=report['source'][:at]+text+report['source'][at+removals.get(at,0):]
    return report
