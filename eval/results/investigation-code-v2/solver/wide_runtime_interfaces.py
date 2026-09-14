"""Closed target-instruction recognition of o32 wide runtime interfaces.

Names only select cheap probes; entire helper instruction streams certify the
interface shape. This does not admit runtime execution or replace helper calls.
"""
import hashlib
import re
from solver import cfg, m2c_byte_view, project_headers, repair_context, target_intake

PACK='''sw a0,0(sp)
sw a1,4(sp)
sw a2,8(sp)
sw a3,12(sp)
ld t7,8(sp)
ld t6,0(sp)
'''
RETURN='''mflo v0
dsll32 v1,v0,0
dsra32 v1,v1,0
jr ra
dsra32 v0,v0,0
'''
MULTIPLY=PACK+'dmultu t6,t7\n'+RETURN
DIVIDE=PACK+'''ddiv zero,t6,t7
nop
bnez t7,.nonzero
nop
break 7
.nonzero:
daddiu at,zero,-1
bne t7,at,.result
daddiu at,zero,1
dsll32 at,at,31
bne t6,at,.result
nop
break 6
.result:
'''+RETURN
UNSIGNED_DIVIDE = PACK + '''ddivu zero,t6,t7
bnez t7,.result
nop
break 7
.result:
''' + RETURN
UNSIGNED_REMAINDER = UNSIGNED_DIVIDE.replace('mflo v0', 'mfhi v0')
UNSIGNED_RSHIFT = PACK + 'dsrlv v0,t6,t7\n' + RETURN.split('\n', 1)[1]
LEFT_SHIFT = PACK + 'dsllv v0,t6,t7\n' + RETURN.split('\n', 1)[1]
SIGNED_RSHIFT = PACK + 'dsrav v0,t6,t7\n' + RETURN.split('\n', 1)[1]
HELPERS = (
    (MULTIPLY, 'u64', 'low-word-pair-product', '*'),
    (DIVIDE, 's64', 'signed-word-pair-division', '/'),
    (UNSIGNED_DIVIDE, 'u64', 'unsigned-word-pair-division', '/'),
    (UNSIGNED_REMAINDER, 'u64', 'unsigned-word-pair-remainder', '%'),
    (UNSIGNED_RSHIFT, 'u64', 'unsigned-word-pair-right-shift', '>>'),
    (LEFT_SHIFT, 'u64', 'word-pair-left-shift', '<<'),
    (SIGNED_RSHIFT, 's64', 'signed-word-pair-right-shift', '>>'),
)


def instruction_key(assembly):
    assembly=re.sub(r'(?m)^\s*(?:nonmatching\s+\w+(?:,\s*(?:0x[0-9a-fA-F]+|\d+))?|endlabel\s+\w+)\s*$', '',assembly)
    graph=cfg.build(assembly)
    labels={label:i.index for i in graph.instructions for label in i.labels}
    def operand(text):
        if text in labels:return '@'+str(labels[text])
        return re.sub(r'0x[0-9a-fA-F]+',lambda m:str(int(m[0],16)),text.replace('$','').replace(' ',''))
    return [(i.opcode,tuple(operand(o) for o in i.operands)) for i in graph.instructions]


def recognize(assembly):
    key=instruction_key(assembly)
    for template,ctype,operation,_ in HELPERS:
        if key==instruction_key(template):
            return {'return_type':ctype,'parameters':[ctype,ctype],
                    'argument_words':4,'return_words':2,'operation':operation}
    return None


def reconstruct_helper(source, function, assembly, *, big_endian_o32=False, compiler_mips=''):
    """Generate a C candidate for a completely recognized arithmetic helper.

    This repairs the helper itself, not callers or interpreter admission. The
    existing compiler/frontend and independent byte certificate decide success.
    Trap/shift behavior is not asserted for undefined C inputs.
    """
    report = {'kind': 'closed-wide-runtime-candidate', 'changes': [],
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
              'authority': 'whole instruction-pattern candidate; compiler/object verification required'}
    if not big_endian_o32 or compiler_mips != '-mips3 -32':
        return source, report
    interface = recognize(assembly)
    if interface is None:
        return source, report
    operators = {operation: operator for _, _, operation, operator in HELPERS}
    definition, end = repair_context.definition(source, function)
    typ = interface['return_type']
    candidate = (f'{typ} {function}({typ} wide_lhs, {typ} wide_rhs) {{\n'
                 f'    return wide_lhs {operators[interface["operation"]]} wide_rhs;\n}}')
    result = source[:definition.start()] + candidate + source[end:]
    if result != source:
        report.update(interface=interface, candidate_sha256=hashlib.sha256(result.encode()).hexdigest())
        report['changes'] = [{'function': function, 'operation': interface['operation'],
                              'replacement': candidate}]
    return result, report


def infer(repo,assembly):
    names=sorted(set(re.findall(r'\bjal\s+(__u?ll_\w+)\b',assembly)))
    rows=[];declines=[]
    for name in names[:4]:
        try:
            resolution=target_intake.resolve(repo,name)
            if resolution.kind!='disassembly' or resolution.symbol!=name:
                raise ValueError('requires uniquely named binary disassembly')
            text=resolution.path.read_text()
            interface=recognize(text)
            if interface is None:raise ValueError('unrecognized complete runtime instruction stream')
            rows.append({'name':name,**interface,'assembly_path':str(resolution.path),
                         'assembly_sha256':hashlib.sha256(text.encode()).hexdigest()})
        except (OSError,ValueError,RuntimeError) as exc:
            declines.append({'name':name,'reason':str(exc)})
    return {'interfaces':rows,'declines':declines,'omitted':names[4:],
            'authority':'closed binary o32 interface; no runtime/callee-effect admission'}


def pair_arguments(source, interfaces):
    """Render m2c's explicit high/low annotations as two wide C arguments.

    Only verified helper interfaces and side-effect-free chunks participate.
    Full wide helper-result locals are projected to their high half when used
    in an annotated high-word expression; other expressions remain word casts.
    """
    mask=project_headers._mask_noncode(source)
    names={r['name']:r for r in interfaces}
    wide_locals=set(re.findall(r'(?m)^\s*(?:s64|u64)\s+(\w+)\s*;',mask))
    returned={m[1] for m in re.finditer(r'(?m)^\s*(\w+)\s*=\s*(\w+)\s*\(',mask)
              if m[2] in names} & wide_locals
    edits=[];changes=[]
    for name,interface in names.items():
        ctype=interface['parameters'][0]
        for call in re.finditer(r'\b'+re.escape(name)+r'\s*\(',mask):
            stop=m2c_byte_view.closing(mask,call.end()-1)
            args=m2c_byte_view.arguments(source[call.end():stop])
            if len(args)!=4:continue
            chunks=[]
            for i,arg in enumerate(args):
                m=re.fullmatch(r'\s*/\*\s*'+ctype+r'\+0x'+('0' if i%2==0 else '4')+r'\s*\*/\s*(.+)',arg,re.S)
                if not m:break
                expr=m[1]
                if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b\w+\s*\(',expr):break
                if i%2==0:
                    for local in sorted(returned):
                        expr=re.sub(r'\b'+re.escape(local)+r'\b','((u32)('+local+' >> 32))',expr)
                chunks.append(expr)
            if len(chunks)!=4:continue
            packed=[f'({ctype})(((u64)(u32)({chunks[i]}) << 32) | (u32)({chunks[i+1]}))' for i in (0,2)]
            edits.append((call.end(),stop,', '.join(packed)))
            changes.append({'helper':name,'before_arguments':args,'after_arguments':packed,
                'authority':'explicit m2c word annotations + verified helper ABI; source reconstruction candidate'})
    for a,b,text in sorted(edits,reverse=True):source=source[:a]+text+source[b:]
    return source,changes
