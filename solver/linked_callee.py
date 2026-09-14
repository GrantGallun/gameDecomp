"""ROM binding and bounded integer admission for linked binary callees."""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace

from solver import cfg, mips_differential as d, sdk_intake


def from_extracted(repo, function, text, *, return_registers):
    """Prepare a linked leaf from annotated binary disassembly, without a workspace.

    Accept only one explicitly sized function and inline word jump tables. The
    existing binder independently reassembles the interpreted stream and verifies
    every initialized table byte against ROM. No C, guessed extent or callee
    summary is consulted. Admission is still a bounded interpreter contract.
    """
    name=re.escape(function)
    starts=list(re.finditer(r'(?m)^\s*glabel\s+(\w+)\s*$',text))
    ends=list(re.finditer(r'(?m)^\s*endlabel\s+(\w+)\s*$',text))
    sizes=re.findall(r'(?m)^\s*nonmatching\s+'+name+r'\s*,\s*(0x[\da-fA-F]+)\s*$',text)
    if len(starts)!=1 or len(ends)!=1 or starts[0][1]!=function or ends[0][1]!=function or len(sizes)!=1:
        raise ValueError('linked callee requires unique explicit extracted extent')
    if ends[0].start()<=starts[0].end():
        raise ValueError('unsupported extracted function tail')
    tail=[line.strip() for line in text[ends[0].end():].splitlines() if line.strip()]
    padding=[]
    for line in tail:
        match=re.fullmatch(r'/\*\s*([\da-fA-F]+)\s+([\da-fA-F]{8})\s+00000000\s*\*/\s*nop',line)
        if not match or len(padding)>=3:
            raise ValueError('unsupported extracted function tail')
        padding.append((int(match[1],16),int(match[2],16)))
    body=text[starts[0].end():ends[0].start()]
    words=re.findall(r'/\*\s*([\da-fA-F]+)\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})\s*\*/',body)
    size=int(sizes[0],16)
    program=d.Program.parse(function,body)
    if not words or size!=len(words)*4 or len(words)!=len(program.instructions) or len(words)>512:
        raise ValueError('extracted instruction annotations do not cover bounded extent')
    offset,address=int(words[0][0],16),int(words[0][1],16)
    for i,(position,pc,word) in enumerate(words):
        if (int(position,16),int(pc,16))!=(offset+4*i,address+4*i):
            raise ValueError('noncontiguous extracted instruction annotations')
    import yaml
    config=yaml.safe_load((repo/'snowboardkids.yaml').read_bytes())
    rom=(repo/config['options']['target_path']).read_bytes()
    if hashlib.sha1(rom).hexdigest()!=config['sha1'] or sdk_intake.rom_offset(config,address,size,len(rom))!=offset:
        raise ValueError('extracted extent ROM identity/mapping mismatch')
    if rom[offset:offset+size]!=b''.join(bytes.fromhex(word) for _,_,word in words):
        raise ValueError('extracted annotation bytes differ from ROM')
    for i,(position,pc) in enumerate(padding):
        if (position,pc)!=(offset+size+i*4,address+size+i*4) or sdk_intake.rom_offset(config,pc,4,len(rom))!=position:
            raise ValueError('extracted padding annotation/mapping mismatch')
        if rom[position:position+4]!=bytes(4):
            raise ValueError('extracted padding differs from ROM')
    symbols={}
    for symbol,value in re.findall(r'(?m)^\s*(\w+)\s*=\s*(0x[\da-fA-F]+)\s*;',
                                   (repo/'symbol_addrs.txt').read_text(encoding='utf-8')):
        if symbol in symbols and symbols[symbol]!=int(value,16):
            raise ValueError('conflicting linker symbol addresses')
        symbols[symbol]=int(value,16)
    annotations=[]
    prefix=text[:starts[0].start()]
    tables=list(re.finditer(r'(?ms)^\s*dlabel\s+(\w+)\s*\n(.*?)^\s*enddlabel\s+\1\s*$',prefix))
    for table in tables:
        symbol,contents=table[1],table[2]
        if symbol not in symbols:
            raise ValueError('unbound extracted table')
        lines=[line.strip() for line in contents.splitlines() if line.strip()]
        for i,line in enumerate(lines):
            match=re.fullmatch(r'/\*\s*([\da-fA-F]+)\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})\s*\*/\s*\.word\s+([\w.$]+)',line)
            if not match or match[4] not in program.labels:
                raise ValueError('only annotated local word jump tables supported')
            location=symbols[symbol]+i*4
            target=program.labels[match[4]]*4
            if int(match[2],16)!=location or int(match[3],16)!=address+target or int(match[1],16)!=sdk_intake.rom_offset(config,location,4,len(rom)):
                raise ValueError('extracted table annotation/label mismatch')
            annotations.append(f'# MIPS_DIFF_DATA {symbol} {i*4:#x} {target:#x}')
    # Reject unsupported inline data rather than silently leaving it unseeded.
    remainder=prefix
    for table in reversed(tables):
        remainder=remainder[:table.start()]+remainder[table.end():]
    remainder=re.sub(r'/\*.*?\*/','',remainder,flags=re.S)
    if any(line.strip() and not re.fullmatch(r'(?:\.section\s+\.\w+|nonmatching\s+\w+(?:\s*,\s*0x[\da-fA-F]+)?)',line.strip())
           for line in remainder.splitlines()):
        raise ValueError('unsupported extracted data/directive prefix')
    enriched=body+'\n'+'\n'.join(annotations)+'\n'
    for symbol in sorted(d.Program.parse(function,enriched).symbols):
        if symbol not in symbols:
            raise ValueError('unbound extracted callee symbol: '+symbol)
        enriched+=f'# MIPS_DIFF_SYMBOL {symbol} {symbols[symbol]:#x}\n'
    leaf=admit(repo,function,enriched,address,size,return_registers=return_registers)
    return replace(leaf,binding={**leaf.binding,
        'extent_authority':'unique explicit extracted function size; contiguous PC/ROM annotations; reassembled full-ROM comparison',
        'verified_excluded_padding_bytes':len(padding)*4,
        'extracted_sha256':hashlib.sha256(text.encode()).hexdigest()})


@dataclass(frozen=True)
class LinkedCallee:
    program: d.Program
    authority: str
    identity: str
    binding: dict
    return_registers: tuple[str, ...]
    word_pair_multiply: bool = False


def admit(repo, function, assembly, address, size, *, return_registers=()):
    """ROM-bound integer callee; no nested calls or arbitrary jumps."""
    from solver.callee_execution import LEAF_OPS
    if any(register not in ('v0','v1') for register in return_registers):
        raise ValueError('unsupported linked callee result registers')
    # Reject unsupported execution before linking: otherwise a nested call can
    # masquerade as a missing linker symbol, obscuring the actual policy boundary.
    preliminary=d.Program.parse(function,assembly)
    if len(preliminary.instructions)>512:
        raise ValueError('linked callee instruction budget exceeded')
    for instruction in preliminary.instructions:
        if instruction.opcode not in LEAF_OPS | {'break'}:
            raise ValueError('unsupported linked callee opcode: '+instruction.opcode)
    binding=bind(repo,function,assembly,address,size)
    completed=assembly.rstrip()+'\n'+'nop\n'*binding['verified_trailing_nops']
    program=replace(d.Program.parse(function,completed),text_base=address)
    if len(program.instructions)>512:
        raise ValueError('linked callee instruction budget exceeded')
    for instruction in program.instructions:
        if instruction.opcode not in LEAF_OPS | {'break'}:
            raise ValueError('unsupported linked callee opcode: '+instruction.opcode)
        if instruction.opcode=='jr' and instruction.operands not in (('ra',),('$ra',)):
            if d._indirect_jump_targets(program,instruction.index) is None:
                raise ValueError('unresolved linked callee indirect jump')
        if instruction.opcode in {'b','j'} or cfg.is_conditional_branch(instruction.opcode):
            program.branch_target(instruction.operands[-1])
    binding={**binding,'execution_admitted':True,'admission_policy':'bounded integer, no nested calls, resolved local table jumps',
             'return_registers':list(return_registers),
             'deferred_traps':[i.index for i in program.instructions if i.opcode=='break'],
             'trap_policy':'reached break instructions remain unsupported; no exception emulation'}
    return LinkedCallee(program,'ROM-bound linked integer callee; finite synthetic caller environment',
        hashlib.sha256(completed.encode()).hexdigest(),binding,tuple(return_registers))


def bind(repo, function, assembly, address, size):
    """Caller supplies an independently audited function extent, recorded below.

    Reassembles the actual interpreter instructions, links external addresses,
    and compares the full supplied range, including zero trailing delay/padding
    words. Only initialized bytes referenced by this program are certified.
    This does not authorize executing unsupported instructions or nested calls.
    """
    import yaml
    config_path = repo/'snowboardkids.yaml'
    config_bytes = config_path.read_bytes()
    config = yaml.safe_load(config_bytes)
    rom = (repo/config['options']['target_path']).read_bytes()
    if hashlib.sha1(rom).hexdigest() != config['sha1']:
        raise ValueError('ROM identity differs from extraction configuration')
    program = d.Program.parse(function, assembly)
    if size <= 0 or size % 4 or address % 4 or len(program.instructions)*4 > size:
        raise ValueError('invalid linked callee function extent')
    symbols = dict(re.findall(r'(?m)^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+)\s*;',
                             (repo/'symbol_addrs.txt').read_text(encoding='utf-8')))
    if int(symbols.get(function,'-1'),0) != address:
        raise ValueError('callee function address conflicts with symbol metadata')
    for name in program.symbols:
        if name not in program.symbol_addresses or name not in symbols:
            raise ValueError('unbound callee symbol: '+name)
        if program.symbol_addresses[name] != int(symbols[name],0):
            raise ValueError('callee symbol address conflict: '+name)
    names = ('mips-linux-gnu-as','mips-linux-gnu-ld','mips-linux-gnu-objcopy')
    executables = [shutil.which(name) for name in names]
    if not all(executables):
        raise ValueError('MIPS assembler/linker/objcopy unavailable')
    lines = ['.set noreorder','.set noat','.text']
    registers = r'(?<![\w$])('+'|'.join(sorted(d.REGISTER_NAMES))+r')(?!\w)'
    for instruction in program.instructions:
        operands = list(instruction.operands)
        if instruction.opcode in {'b','j'} or cfg.is_conditional_branch(instruction.opcode):
            operands[-1] = f'.Lbound_{program.branch_target(operands[-1])}'
        operands = re.sub(registers,lambda m:'$'+m[1],','.join(operands))
        lines.extend((f'.Lbound_{instruction.index}:',instruction.opcode+' '+operands))
    # Missing normalized words may only be zero NOPs, verified against ROM.
    lines.extend(['nop']*((size//4)-len(program.instructions)))
    with tempfile.TemporaryDirectory(prefix='linked-callee-binding-') as temp:
        obj, linked, binary = [Path(temp)/name for name in ('parsed.o','linked.elf','text.bin')]
        def run(command, **kwargs):
            result=subprocess.run(command,capture_output=True,text=True,timeout=20,**kwargs)
            if result.returncode:
                raise ValueError('linked callee tool failed: '+result.stderr[-1200:])
        run([executables[0],'-EB','-mabi=32','-march=vr4300','-o',str(obj),'-'],input='\n'.join(lines)+'\n')
        # GAS defaults .text to 16-byte alignment, but extracted function starts
        # need only be instruction-aligned. Otherwise ld inserts a leading gap
        # inside the requested section address and shifts the verified stream.
        run([executables[2],'--set-section-alignment','.text=4',str(obj)])
        definitions=[f'--defsym={name}={value:#x}' for name,value in sorted(program.symbol_addresses.items())]
        run([executables[1],'-EB','-Ttext',hex(address),'-e',hex(address),*definitions,'-o',str(linked),str(obj)])
        run([executables[2],'-O','binary','--only-section=.text',str(linked),str(binary)])
        actual=binary.read_bytes()
    offset=sdk_intake.rom_offset(config,address,size,len(rom))
    expected=rom[offset:offset+size]
    if actual[:size] != expected or any(actual[size:]):
        raise ValueError('linked parsed callee text differs from full ROM extent')
    data=dict(program.data_bytes)
    for (name, off), target in program.data_words.items():
        if target % 4 or not 0 <= target < size:
            raise ValueError('callee table points outside function')
        for i,value in enumerate((address+target).to_bytes(4,'big')):
            data[(name,off+i)]=value
    for (name, off), value in data.items():
        location=program.symbol_addresses[name]+off
        position=sdk_intake.rom_offset(config,location & ~3,4,len(rom))+(location & 3)
        if rom[position] != value:
            raise ValueError('callee initialized data differs from ROM')
    return {'kind':'linked-callee-ROM-binding','function':function,'address':address,'size':size,
        'extent_authority':'caller-supplied audited range; not inferred here',
        'rom_sha1':config['sha1'],'rom_offset':offset,'text_sha256':hashlib.sha256(expected).hexdigest(),
        'parsed_instructions':len(program.instructions),'verified_trailing_nops':size//4-len(program.instructions),
        'initialized_bytes_verified':len(data),'symbol_addresses':program.symbol_addresses,
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'config_sha256':hashlib.sha256(config_bytes).hexdigest(),
        'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'tool_sha256':{name:hashlib.sha256(Path(path).read_bytes()).hexdigest() for name,path in zip(names,executables)},
        'reference_bodies_used':False,'execution_admitted':False}
