"""Small diagnostic-bound C representation hypotheses, never acceptance gates.

Do not weaken frontend policy or change interfaces. Every proposal must be
compiled and (where available) differentially tested by the ordinary pipeline.
"""
import hashlib
import re
from pathlib import Path

from solver import project_headers, repair_context, type_transaction


def big_endian_o32(target):
    if not target.is_file():
        return False
    data = target.read_bytes()[:52]
    return (len(data) == 52 and data[:6] == b'\x7fELF\x01\x02'
            and int.from_bytes(data[18:20], 'big') == 8
            and int.from_bytes(data[36:40], 'big') & 0xf000 == 0x1000)


def propose(repo, source, function, diagnostics, *, big_endian_o32=False, target_assembly=''):
    report = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'diagnostics_sha256': hashlib.sha256(diagnostics.encode()).hexdigest(),
              'big_endian_o32': big_endian_o32, 'header_prototypes': {},
              'assembly_sha256':hashlib.sha256(target_assembly.encode()).hexdigest() if target_assembly else None,
              'changes': [], 'declines': [], 'source': source,
              'scope': 'diagnostic-bound representation hypotheses; not semantic proof'}
    match, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = {}
    declarations = None
    first_parameter_offsets = set()
    if target_assembly and 'incompatible pointer types assigning' in diagnostics:
        from solver import dataflow
        flow=dataflow.analyse(target_assembly)
        for instruction in flow.graph.instructions:
            if instruction.opcode!='addiu' or len(instruction.operands)!=3:continue
            state=flow.instruction_in.get(instruction.index)
            if state is None:continue
            base=state.registers.get(dataflow.reg(instruction.operands[1]))
            amount=dataflow.number(instruction.operands[2])
            if base==dataflow.Value.address('param0') and amount is not None:
                first_parameter_offsets.add(amount)
    pattern = re.compile(r'^candidate\.c:(\d+):(\d+): error: (.*)$', re.M)
    for diagnostic in pattern.finditer(diagnostics):
        number, column = int(diagnostic[1]), int(diagnostic[2])
        if not (1 <= number <= len(lines)):
            continue
        line = lines[number-1].rstrip('\r\n')
        # Clang's displayed source is an independent stale-location check.
        excerpt = re.match(r'\n\s*'+str(number)+r' \| (.*)', diagnostics[diagnostic.end():])
        if not excerpt or excerpt[1] != line or '\t' in line:
            report['declines'].append('diagnostic source line missing, stale, or tab-expanded')
            continue
        position = offsets[number-1] + column-1
        if position < match.start():
            # A header field macro cannot also be declared as a standalone
            # extern. Bind removal to the compiler's expansion note, the exact
            # source line, and the current header definition/backing object.
            declaration = re.fullmatch(r'\s*extern\s+(?:[A-Za-z_]\w*\s+)+\**\s*([A-Za-z_]\w*)\s*;', masked[offsets[number-1]:offsets[number-1]+len(line)])
            if declaration:
                name = declaration[1]
                notes = re.finditer(r"(?m)^(include/[\w./-]+\.h):\d+:\d+: note: expanded from macro '"+re.escape(name)+r"'$", diagnostics)
                for note in notes:
                    header = (Path(repo)/note[1]).resolve()
                    if not header.is_relative_to((Path(repo)/'include').resolve()) or not header.is_file():
                        continue
                    header_text = header.read_text(errors='replace')
                    header_mask = project_headers._mask_noncode(header_text)
                    macro = re.search(r'(?m)^\s*#define[ \t]+'+re.escape(name)+r'[ \t]+\(([A-Za-z_]\w*)\.[A-Za-z_]\w*\)[ \t]*$', header_mask)
                    if not macro or not re.search(r'(?m)^\s*extern[ \t]+[A-Za-z_]\w*[ \t]+'+re.escape(macro[1])+r'[ \t]*;', header_mask):
                        continue
                    edits[offsets[number-1],offsets[number-1]+len(line)] = ('', 'header-field-macro-extern')
                    report.setdefault('macro_headers', {})[name] = {
                        'path': note[1], 'sha256': hashlib.sha256(header_text.encode()).hexdigest(),
                        'backing_object': macro[1]}
        if not match.end() <= position < end-1:
            continue
        message = diagnostic[3]
        if big_endian_o32 and 'indirection requires pointer operand' in message:
            from solver import indexed_address_repair, m2c_adapter
            indexed=re.compile(r'\*\((0x[0-9A-Fa-f]+)\s*\+\s*\(\(\*\(u16\s*\*\)\(\(u8\s*\*\)\((\w+)\)\s*\+\s*(0x[0-9A-Fa-f]+|[0-9]+)\)\)\s*\*\s*(0x[0-9A-Fa-f]+|[1-9][0-9]*)\)\)')
            for expression in indexed.finditer(line):
                base,param,offset,stride=expression.groups()
                if not expression.start()<=column-1<expression.end():continue
                slots=[i for i,p in enumerate(match[2].split(',')[:4]) if re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+param+r'\s*',p)]
                body=masked[match.end():end]
                if len(slots)!=1 or re.search(r'(?m)^\s*\w+\s+\**\s*'+param+r'\s*[;=]',body) or re.search(r'\b'+param+r'\s*(?:[+\-*/]?=(?!=)|\+\+|--)',body):continue
                peers=indexed_address_repair.absolute_load_witnesses(target_assembly,m2c_adapter.absolute_symbols(repo),slots[0],int(offset,0),int(base,0),int(stride,0))
                if len(peers)!=1:continue
                at=offsets[number-1]+expression.start()+1
                edits[at,at]=('('+peers[0]['type']+' *)','target-indexed-absolute-load')
                report.setdefault('indexed_absolute_loads',[]).append(peers[0])
        if big_endian_o32 and re.search(r"incompatible pointer types passing '(?:struct )?\w+ \*'.* to parameter of type '(?:struct )?\w+ \*'",message):
            from solver import dataflow, m2c_byte_view
            flow=dataflow.analyse(target_assembly)
            parameters=match[2].split(',')
            body=masked[match.end():end]
            for call in re.finditer(r'\b(\w+)\s*\(',line):
                try:stop=m2c_byte_view.closing(line,call.end()-1)
                except ValueError:continue
                args=m2c_byte_view.arguments(line[call.end():stop]);cursor=call.end()
                for word,arg in enumerate(args[:4]):
                    at=line.find(arg,cursor,stop);cursor=at+len(arg)
                    offset=re.fullmatch(r'(\w+)\s*\+\s*(0x[0-9A-Fa-f]+|[1-9][0-9]*)',arg)
                    if not offset or not at<=column-1<cursor:continue
                    base,literal=offset.groups()
                    slots=[i for i,p in enumerate(parameters[:4]) if re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+base+r'\s*',p)]
                    if len(slots)!=1 or re.search(r'(?m)^\s*\w+\s+\**\s*'+base+r'\s*[;=]',body) or re.search(r'\b'+base+r'\s*(?:[+\-*/]?=(?!=)|\+\+|--)',body):continue
                    peers=[c for c in flow.callsites.values() if c.target==call[1] and c.arguments[word]==dataflow.Value.address('param'+str(slots[0]),int(literal,0))]
                    if len(peers)!=1:continue
                    edits[offsets[number-1]+at,offsets[number-1]+cursor]=(f'(void *)((u8 *){base} + {literal})','target-call-parameter-byte-offset')
                    report.setdefault('parameter_offset_calls',[]).append({'callee':call[1],'argument':word,'parameter':slots[0],'offset':int(literal,0),'instruction':peers[0].instruction})
        table_assignment=re.fullmatch(r'\s*(\w+)\s*=\s*(\((\w+)\s*\*\s*(0x[0-9A-Fa-f]+|[1-9][0-9]*)\)\s*\+\s*&\s*(\w+))\s*;\s*',line)
        if big_endian_o32 and table_assignment and "incompatible pointer types assigning" in message and "(*)[" in message:
            from solver import indexed_address_repair
            dest,expression,index,literal,table=table_assignment.groups()
            body=masked[match.end():end]
            shadow=any(re.search(r'\b'+n+r'\b',match[2]) or re.search(r'(?m)^\s*\w+\s+\**\s*'+n+r'\s*[;=]',body) for n in (dest,index,table))
            witnesses=indexed_address_repair.affine_table_stores(target_assembly,index,table,dest,int(literal,0))
            if not shadow and len(witnesses)==1:
                a,b=offsets[number-1]+table_assignment.start(2),offsets[number-1]+table_assignment.end(2)
                edits[a,b]=(f'(void *)((u8 *)&{table} + ({index} * {literal}))','affine-table-byte-address')
                report.setdefault('affine_table_stores',[]).append({'destination':dest,'index':index,'table':table,'stride':int(literal,0),'instructions':witnesses})
        no_args = re.search(r'too many arguments to function call, expected 0, have ([1-9][0-9]*)',message)
        if no_args and big_endian_o32:
            from solver import dataflow, m2c_byte_view
            call=re.fullmatch(r'\s*(\w+)\s*\((.*)\)\s*;\s*',line)
            if call:
                callee=call[1]
                declarations_=sorted(set(project_headers._included_declarations(repo,source).get(callee,[])))
                flow=dataflow.analyse(target_assembly)
                witnesses=[c for c in flow.callsites.values() if c.target==callee]
                if declarations_==['void '+callee+'(void);'] and len(witnesses)==1:
                    args=m2c_byte_view.arguments(call[2])
                    # Preserve evaluation (including volatile reads) rather
                    # than silently deleting draft argument expressions.
                    if len(args)==int(no_args[1]) and len(args)<=4 and all(re.fullmatch(r'\s*(?:&\s*)?\w+\s*|-?(?:0x[0-9a-fA-F]+|[0-9]+)',a) for a in args):
                        replacement='('+', '.join(['(void)('+a+')' for a in args]+[callee+'()'])+');'
                        edits[offsets[number-1],offsets[number-1]+len(line)]=(replacement,'header-zero-arity-preserve-evaluation')
                        report.setdefault('zero_arity_calls',[]).append({'callee':callee,'declaration':declarations_[0],
                            'instruction':witnesses[0].instruction,'scope':'header contract projection; argument evaluation retained; not callee-body proof'})
        callback = re.search(r"incompatible function pointer types passing 'void \(\*\)\((?:struct )?([A-Za-z_]\w*) \*\)'[^\n]* to parameter of type '([A-Za-z_]\w*)' \(aka 'void \(\*\)\(void \*\)'\)",message)
        callback_call = re.fullmatch(r'\s*(\w+)\s*\(\s*(&\s*(\w+))\s*,[^;]*\);\s*',line)
        if big_endian_o32 and callback and callback_call:
            from solver import dataflow
            callee,_,symbol=callback_call.groups()
            flow=dataflow.analyse(target_assembly)
            witnesses=[c for c in flow.callsites.values() if c.target==callee and c.arguments[0]==dataflow.Value.address(symbol)]
            body=masked[match.end():end]
            if (len(witnesses)==1 and not re.search(r'\b'+symbol+r'\b',match[2]) and
                not re.search(r'(?m)^\s*\w+\s+\**\s*'+symbol+r'\s*[;=]',body)):
                at=offsets[number-1]+callback_call.start(2)
                if at<=position<offsets[number-1]+callback_call.end(2):
                    edits[at,at]=('(void (*)(void *)) ', 'o32-single-pointer-callback-view')
                    report.setdefault('callback_abi_hypotheses',[]).append({'callee':callee,'callback':symbol,
                        'instruction':witnesses[0].instruction,
                        'scope':'target o32 one-object-pointer ABI view; not portable C callback compatibility or runtime proof'})
        array_store = re.search(r"array type '(s8|u8|s16|u16|s32|u32)\[([1-9][0-9]*)\]'[^\n]* is not assignable", message)
        zero_assignment = re.fullmatch(r'\s*(\w+)\s*=\s*0\s*;\s*', line)
        if big_endian_o32 and array_store and zero_assignment:
            from solver import dataflow
            name=zero_assignment[1]
            body=masked[match.end():end]
            shadowed=(re.search(r'\b'+name+r'\b',match[2]) or
                      re.search(r'(?m)^\s*\w+\s+\**\s*'+name+r'\s*[;=\[]',body))
            assignments=re.findall(r'\b'+name+r'\s*=(?!=)',body)
            flow=dataflow.analyse(target_assembly)
            stores=[a for a in flow.accesses.values() if not a.is_load and a.address and a.address.name==name]
            width={'s8':1,'u8':1,'s16':2,'u16':2,'s32':4,'u32':4}[array_store[1]]
            if not shadowed and len(assignments)==1 and len(stores)==1:
                access=stores[0]
                instruction=flow.graph.instructions[access.instruction]
                if (access.address==dataflow.Value.address(name) and access.width==width and
                    access.opcode=={1:'sb',2:'sh',4:'sw'}[width] and
                    dataflow.reg(instruction.operands[0])=='zero'):
                    at=offsets[number-1]+zero_assignment.end(1)
                    edits[at,at]=('[0]', 'target-zero-store-array-element')
                    report.setdefault('array_zero_stores',[]).append({'global':name,'width':width,'instruction':access.instruction})
        array_address = re.search(r"incompatible pointer types assigning to '([A-Za-z_]\w*) \*'[^\n]* from '\1 \(\*\)\[([1-9][0-9]*)\]'", message)
        if array_address:
            # &array and array decay designate the same first address, but
            # have different pointer types. No arithmetic is admitted here:
            # scaling corrections need their own target-backed hypotheses.
            assignment = re.fullmatch(r'\s*\w+\s*=\s*(&\s*\w+)\s*;\s*', line)
            if assignment:
                a = offsets[number-1]+assignment.start(1)
                edits[a,a+1] = ('', 'one-dimensional-array-address-decay')
        pointer_parameter=re.search(r"integer to pointer conversion passing 's32'.*parameter of type '(\w+) \*'",message)
        if big_endian_o32 and pointer_parameter:
            from solver import dataflow, m2c_byte_view, indexed_address_repair, cfg
            flow=dataflow.analyse(target_assembly)
            binary=indexed_address_repair.witnesses(target_assembly)
            body=masked[match.end():end]
            for call in re.finditer(r'\b(\w+)\s*\(',line):
                stop=m2c_byte_view.closing(line,call.end()-1)
                args=m2c_byte_view.arguments(line[call.end():stop]);cursor=call.end()
                for slot,arg in enumerate(args):
                    start=line.find(arg,cursor,stop);cursor=start+len(arg)
                    table_load=re.fullmatch(r'\*\(&\s*(\w+)\s*\+\s*\((\w+)\s*\*\s*4\)\)',arg)
                    if not table_load or slot>3 or not offsets[number-1]+start<=position<offsets[number-1]+cursor:continue
                    table,index=table_load.groups()
                    declarations_=re.findall(r'(?m)^\s*extern\s+(s8|u8|s16|u16|s32|u32)\s+'+index+r'\s*;',masked[:match.start()])
                    if len(declarations_)!=1:continue
                    index_type=declarations_[0];width={'8':1,'16':2,'32':4}[re.search(r'\d+',index_type)[0]]
                    signed=None if width==4 else index_type.startswith('s')
                    if any(re.search(r'\b'+n+r'\b',match[2]) or re.search(r'(?m)^\s*\w+\s+\**\s*'+n+r'\s*[;=]',body) for n in (table,index)):continue
                    if re.search(r'\b'+index+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)|&\s*\b'+index+r'\b',body):continue
                    witnesses=[]
                    for w in binary:
                        if not (w['kind']=='scalar-load' and w['table']==table and w['index_global']==index and w['index_offset']==0 and w['index_width']==width and w['index_signed']==signed and w['stride']==4 and w['load_width']==4):continue
                        load=flow.graph.instructions[w['load_instruction']]
                        register='a'+str(slot)
                        if dataflow.reg(load.operands[0])!=register:continue
                        for instruction in flow.graph.instructions[load.index+1:load.index+9]:
                            if instruction.opcode in cfg.CALL_OPS:
                                c=flow.callsites.get(instruction.index)
                                delay=flow.graph.instructions[c.delay_slot] if c else None
                                if c and c.target==call[1] and dataflow._destination(delay)!=register:
                                    witnesses.append({**w,'call_instruction':c.instruction,'argument':slot})
                                break
                            if cfg.is_control_transfer(instruction.opcode) or dataflow._destination(instruction)==register:break
                    if not witnesses:continue
                    replacement=f'*({pointer_parameter[1]} **)((u8 *)&{table} + ({index} * 4))'
                    a,b=offsets[number-1]+start,offsets[number-1]+cursor
                    edits[a,b]=(replacement,'target-indexed-pointer-call-load')
                    report.setdefault('pointer_table_calls',[]).append({'table':table,'index':index,'witnesses':witnesses})
        if big_endian_o32 and "integer to pointer conversion passing 's32'" in message and "parameter of type 'void *'" in message:
            from solver import dataflow, m2c_byte_view
            flow=dataflow.analyse(target_assembly)
            for call in re.finditer(r'\b(\w+)\s*\(',line):
                stop=m2c_byte_view.closing(line,call.end()-1)
                if stop<call.end():continue
                args=m2c_byte_view.arguments(line[call.end():stop]);cursor=call.end()
                for word,arg in enumerate(args):
                    start=line.find(arg,cursor,stop);cursor=start+len(arg)
                    load=re.fullmatch(r'\(\*\(s32\s*\*\)\(\(unsigned char\s*\*\)(\w+)\s*\+\s*(0x[0-9a-fA-F]+|[0-9]+)\)\)',arg)
                    if not load or word>3:continue
                    base,offset=load.groups()
                    if not re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+re.escape(base)+r'\s*',match[2].split(',')[0]):continue
                    body=masked[match.end():end]
                    if re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body):continue
                    expected=dataflow.Value.address('param0',int(offset,0))
                    peers=[c for c in flow.callsites.values() if c.target==call[1]
                           and c.arguments[word] and c.arguments[word].kind=='load'
                           and c.arguments[word].width==4 and c.arguments[word].offset==0
                           and c.arguments[word].inner==expected]
                    if len(peers)!=1:continue
                    value=peers[0].arguments[word]
                    if not value or value.kind!='load' or value.width!=4 or value.offset!=0 or value.inner!=dataflow.Value.address('param0',int(offset,0)):continue
                    a,b=offsets[number-1]+start,offsets[number-1]+cursor
                    if a<=position<b:
                        edits[a,b]=(arg.replace('(s32 *','(void **',1),'call-bound-pointer-word-load')
                        report.setdefault('pointer_load_calls',[]).append({'callee':call[1],'argument':word,'instruction':peers[0].instruction,'offset':int(offset,0)})
        if re.search(r"member reference base type '(?:u8|s8|unsigned char|signed char)\[[0-9]+\]'",message):
            # The candidate already declares the byte buffer and its extent;
            # only replace an offset-named pseudo-member inside those bounds.
            for field in re.finditer(r'\b(\w+)\.unk([0-9A-Fa-f]+)\b',masked[offsets[number-1]:offsets[number]]):
                a,b=offsets[number-1]+field.start(),offsets[number-1]+field.end()
                if not a<=position<b:continue
                name=field[1]; index=int(field[2],16)
                body=masked[match.end():end]
                arrays=re.findall(r'(?m)^[ \t]*(?:u8|s8|unsigned char|signed char)\s+'+re.escape(name)+r'\s*\[\s*(0x[0-9a-fA-F]+|[1-9][0-9]*)\s*\]\s*;',body)
                if len(arrays)!=1 or index>=int(arrays[0],0) or re.search(r'\b'+re.escape(name)+r'\b',match[2]):continue
                edits[a,b]=(name+'[0x'+field[2]+']','declared-byte-array-field')
        typed_argument = re.search(r"incompatible pointer types passing '(?:s8|u8|signed char|unsigned char) \*'.* to parameter of type '(\w+) \*'", message)
        if typed_argument:
            # A closed byte-cursor offset already has byte units. A header-
            # bound object-pointer view changes neither the address nor ABI.
            from solver import m2c_byte_view
            if declarations is None:
                declarations = project_headers._included_declarations(repo, source)
            body = masked[match.end():end]
            for call in re.finditer(r'\b(\w+)\s*\(', line):
                protos = declarations.get(call[1], [])
                shapes = [type_transaction.signature(p, call[1]) for p in protos]
                if not shapes or any(s is None for s in shapes) or len(set(shapes))!=1:
                    continue
                stop = m2c_byte_view.closing(line, call.end()-1)
                if stop<call.end():
                    continue
                args = m2c_byte_view.arguments(line[call.end():stop])
                if len(args)!=len(shapes[0][1]):
                    continue
                cursor = call.end()
                for word,arg in enumerate(args):
                    start = line.find(arg,cursor,stop)
                    cursor = start+len(arg)
                    expr = re.fullmatch(r'(\w+)\s*([+-])\s*(0x[0-9A-Fa-f]+|[0-9]+)',arg)
                    if not expr or shapes[0][1][word]!=(typed_argument[1],'*'):
                        continue
                    name=expr[1]
                    locals_=re.findall(r'(?m)^[ \t]*(?:s8|u8|signed char|unsigned char)\s*\*\s*'+re.escape(name)+r'\s*;',body)
                    if (len(locals_)!=1 or re.search(r'\b'+re.escape(name)+r'\b',match[2])
                            or int(expr[3],0)>65536):
                        continue
                    a,b=offsets[number-1]+start,offsets[number-1]+cursor
                    if a<=position<b:
                        edits[a,b]=('('+typed_argument[1]+' *)('+arg+')','header-bound-byte-cursor-argument')
                        report['header_prototypes'][call[1]]=protos
        if big_endian_o32 and "integer to pointer conversion assigning to 'void *'" in message:
            # Closed m2c table load: source retains a byte stride but gives the
            # table a scalar declaration, causing a second C sizeof scaling.
            shape = re.fullmatch(r'\s*(\w+)\s*=\s*(\*\(&([A-Za-z_]\w*) \+ \(\(\*\((u16|s16|u8|s8) \*\)\(\(u8 \*\)\((\w+)\) \+ (0x[0-9A-Fa-f]+|[0-9]+)\)\) \* (4)\)\))\s*;\s*', line)
            if shape:
                dest, expression, table, scalar, base, offset, stride = shape.groups()
                body = masked[match.end():end]
                parameters = match[2].split(',')
                local = re.findall(r'(?m)^[ \t]*void\s*\*\s*'+re.escape(dest)+r'\s*;', body)
                first = re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+re.escape(base)+r'\s*', parameters[0])
                table_decl = re.findall(r'(?m)^\s*extern\s+(?:s32|u32)\s+'+re.escape(table)+r'\s*;', masked[:match.start()])
                shadow = re.search(r'(?m)^[ \t]*\w+\s+\**\s*(?:'+re.escape(base)+'|'+re.escape(table)+r')\s*[;=]', body)
                from solver import indexed_address_repair
                peers = [w for w in indexed_address_repair.witnesses(target_assembly, parameter_indexes=True)
                         if w['kind']=='scalar-load' and w['table']==table and w['index_global']=='param0'
                         and w['index_offset']==int(offset,0) and w['index_width']==(2 if scalar.endswith('16') else 1)
                         and w['index_signed']==scalar.startswith('s') and w['stride']==4 and w['load_width']==4]
                a,b = offsets[number-1]+shape.start(2), offsets[number-1]+shape.end(2)
                if len(local)==1 and first and len(table_decl)==1 and not shadow and len(peers)==1 and a-2<=position<b:
                    replacement = f'*(void **)((u8 *)&{table} + ((*( {scalar} *)((u8 *)({base}) + {offset})) * {stride}))'
                    edits[a,b] = (replacement, 'binary-pointer-table-byte-stride')
                    report.setdefault('pointer_table_witnesses', []).extend(peers)
        if big_endian_o32 and ('arithmetic on a pointer to an incomplete type' in message or
                              'incompatible pointer types assigning' in message):
            assignment = re.fullmatch(r'\s*(\w+)\s*=\s*(\w+)\s*([+-])\s*(0x[0-9a-fA-F]+|[1-9][0-9]*)\s*;\s*',line)
            if assignment:
                dest,base,operator,literal=assignment.groups()
                body=masked[match.end():end]
                outputs=re.findall(r'(?m)^[ \t]*(?:struct\s+)?\w+\s*\*\s*'+re.escape(dest)+r'\s*;',body)
                parameters=[p for p in match[2].split(',') if re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+re.escape(base)+r'\s*',p)]
                if (len(outputs)==1 and len(parameters)==1 and int(literal,0)<=65536
                        and not re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body)):
                    if 'incompatible pointer types assigning' in message:
                        # Complete pointee types require a binary byte-offset
                        # witness; do not silently reinterpret ordinary C indexing.
                        first=re.fullmatch(r'\s*(?:struct\s+)?\w+\s*\*\s*'+re.escape(base)+r'\s*',match[2].split(',')[0])
                        value=int(literal,0)*(1 if operator=='+' else -1)
                        if not first or value not in first_parameter_offsets:continue
                    a=offsets[number-1]+assignment.start(2)
                    b=offsets[number-1]+assignment.end(4)
                    if offsets[number-1]+line.index('=')<=position<b:
                        edits[a,b]=(f'(void *)((unsigned char *){base} {operator} {literal})','incomplete-pointer-byte-offset')
        if big_endian_o32 and 'indirection requires pointer operand' in message:
            # m2c can retain an integer address expression but omit its pointer
            # cast. Only a closed parameter-address -> same-width output-pointer
            # assignment qualifies; arbitrary scalar dereferences do not.
            assignment = re.fullmatch(r'\s*\*(\w+)\s*=\s*\*\((\w+)\s*\|\s*(?:\(s32\)\s*)?(0x[0-9a-fA-F]+)\)\s*;\s*', line)
            if assignment:
                dest, base, literal = assignment.groups()
                parameters = match[2].split(',')
                output_types = [m[1] for p in parameters if (m := re.fullmatch(
                    r'\s*(u8|u16|u32|s8|s16|s32)\s*\*\s*'+re.escape(dest)+r'\s*', p))]
                addresses = [p for p in parameters if re.fullmatch(r'\s*u32\s+'+re.escape(base)+r'\s*', p)]
                shadowed = re.search(r'(?m)^[ \t]*\w+\s+\**\s*(?:'+re.escape(base)+'|'+re.escape(dest)+r')\s*[;=]', masked[match.end():end])
                if len(output_types)==1 and len(addresses)==1 and not shadowed and int(literal,16)<=0xffffffff:
                    at = offsets[number-1] + line.index('*(', line.index('=')) + 1
                    stop = offsets[number-1] + line.rfind(')') + 1
                    if at-1 <= position < stop:
                        edits[at, stop] = ('('+output_types[0]+' *)'+source[at:stop], 'integer-address-dereference')
        # Integer literal ROM/address arguments. No identifiers, expressions,
        # pointer dereferences, or inferred public ABI changes.
        if big_endian_o32 and "integer to pointer conversion passing 'int' to parameter of type 'void *'" in message:
            token = re.match(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)\b', masked[position:])
            if token and re.match(r'\s*[,)]', masked[position+token.end():]):
                literal = token[0]
                value = int(literal, 16 if literal.lower().startswith('0x') else 10)
                if 0 < value <= 0xffffffff:
                    edits[position, position+len(literal)] = ('(void *)'+literal, 'literal-address-argument')
        # An object address assigned to a byte cursor, preserving its address.
        # Reject other destination widths and complex RHS expressions.
        if "incompatible pointer types assigning to 'u8 *'" in message or "incompatible pointer types assigning to 'unsigned char *'" in message:
            statement = re.fullmatch(r'\s*\w+\s*=\s*(&?\w+(?:\s*[+-]\s*\([\w\s<>+\-]+\))?)\s*;\s*', line)
            if statement and ' from ' in message and '*' in message.split(' from ', 1)[1]:
                a = offsets[number-1] + statement.start(1)
                b = offsets[number-1] + statement.end(1)
                assignment = offsets[number-1] + line.index('=')
                if assignment <= position < b:
                    edits[a, b] = ('(unsigned char *)('+source[a:b]+')', 'object-address-byte-view')
        if big_endian_o32 and 'too many arguments to function call, expected single argument' in message:
            if declarations is None:
                declarations = project_headers._included_declarations(repo, source)
            # Only a closed, side-effect-free two-word call to a header-declared
            # single 64-bit argument. No guessed names, deleted words or prototypes.
            for call in re.finditer(r'\b(\w+)\s*\(', masked[offsets[number-1]:offsets[number]]):
                name = call[1]
                protos = declarations.get(name, [])
                shapes = [type_transaction.signature(p, name) for p in protos]
                if (not shapes or any(s is None or s[1] not in ((('u64',),), (('s64',),)) for s in shapes)
                        or len(set(shapes)) != 1):
                    continue
                opening = offsets[number-1]+call.end()
                depth, split, cursor = 1, [], opening
                while cursor < offsets[number] and depth:
                    char = masked[cursor]
                    if char == ',' and depth == 1:
                        split.append(cursor)
                    depth += (char == '(') - (char == ')')
                    cursor += 1
                if depth or len(split) != 1 or not opening <= position < cursor:
                    continue
                a, b = masked[opening:split[0]].strip(), masked[split[0]+1:cursor-1].strip()
                parts = (a, b)
                if any(not re.fullmatch(r'[\w\s()+\-<>&|^~]+', p) or
                       re.search(r'\b\w+\s*\(|\+\+|--', p) for p in parts):
                    continue
                replacement = '(((u64)(u32)('+a+') << 32) | (u32)('+b+'))'
                if shapes[0][1] == (('s64',),):
                    replacement = '(s64)'+replacement
                report['header_prototypes'][name] = protos
                edits[opening, cursor-1] = (replacement, 'o32-u64-argument-pack')
    ordered = sorted(edits.items())
    if len(ordered) > 128 or any(a[0][1] > b[0][0] for a, b in zip(ordered, ordered[1:])):
        report['declines'].append('edit bound or overlapping diagnostics')
        return report
    for (a, b), (replacement, kind) in reversed(ordered):
        report['source'] = report['source'][:a] + replacement + report['source'][b:]
    report['changes'] = [{'start': a, 'end': b, 'before': source[a:b], 'after': replacement, 'kind': kind}
                         for (a, b), (replacement, kind) in ordered]
    return report
