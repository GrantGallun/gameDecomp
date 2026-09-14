"""Bounded executable leaf callees and explicitly assumed output environments.

No reference C or inferred summaries are executed. Leaf admission deliberately
excludes relocations, calls, floating point and computed jumps. Output models
are opt-in test assumptions, never auto-loaded binary facts.
"""
from dataclasses import asdict, dataclass, field, replace
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from solver import cfg, mips_differential as d


LEAF_OPS = frozenset({
    'nop', 'move', 'li', 'lui', 'addiu', 'addu', 'subu', 'neg', 'negu', 'not',
    'and', 'andi', 'or', 'ori', 'xor', 'xori', 'nor', 'sll', 'srl', 'sra',
    'sllv', 'srlv', 'srav', 'slt', 'sltu', 'slti', 'sltiu', 'mult', 'multu',
    'div', 'divu', 'mflo', 'mfhi', 'mthi', 'mtlo',
    'lb', 'lbu', 'lh', 'lhu', 'lw', 'sb', 'sh', 'sw', 'b', 'j', 'jr',
    'beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'blez', 'bltz',
    'beql', 'bnel', 'beqzl', 'bnezl', 'bgezl', 'bgtzl', 'blezl', 'bltzl',
})

# A deliberately closed instruction dialect: o32 word-pair multiplication.
# Recognition is by the entire instruction stream, never a function name.
WORD_PAIR_MULTIPLY = '''sw a0,0(sp)
sw a1,4(sp)
sw a2,8(sp)
sw a3,12(sp)
ld t7,8(sp)
ld t6,0(sp)
dmultu t6,t7
mflo v0
dsll32 v1,v0,0
dsra32 v1,v1,0
jr ra
dsra32 v0,v0,0'''


def _instruction_key(program):
    def normalized(text):
        return re.sub(r'0x[0-9a-fA-F]+', lambda m:str(int(m[0],16)), text.replace('$','').replace(' ',''))
    return [(i.opcode, tuple(normalized(o) for o in i.operands)) for i in program.instructions]


class WordPairRunner(d.Runner):
    """64-bit intermediates for the closed ROM-bound word-pair dialect only."""
    def __init__(self, program, *args, **kwargs):
        if _instruction_key(program) != _instruction_key(d.Program.parse('word-pair', WORD_PAIR_MULTIPLY)):
            raise ValueError('unsupported word-pair instruction stream')
        super().__init__(program, *args, **kwargs)

    def set(self, name, value, origin=''):
        name = name.strip().lstrip('$')
        if name != 'zero':
            self.reg[name] = value & ((1 << 64)-1)
            self.origins[name] = self._compact_origin(origin or 'word-pair result')

    def _execute_plain(self, instruction):
        op, args = instruction.opcode, instruction.operands
        origin = f'i{instruction.index} {instruction.text}'
        if op == 'ld':
            address = self.address(args[1])
            if address % 8:
                raise d.MemoryFault('unaligned word-pair doubleword load')
            self._check_callee_stack(address, 8, reading=True)
            self.set(args[0], self.memory.read(address, 8), origin)
        elif op == 'dmultu':
            product = self.get(args[0]) * self.get(args[1])
            self.set('lo', product, origin)
            self.set('hi', product >> 64, origin)
        elif op in ('dsll32','dsra32'):
            value = self.get(args[1])
            shift = 32 + self.immediate(args[2])
            self.set(args[0], value << shift if op == 'dsll32' else d.sign_extend(value,64) >> shift, origin)
        else:
            super()._execute_plain(instruction)

    def execute(self):
        result = super().execute()
        # The caller interpreter is o32; retain full intermediate values in the
        # nested trace, but expose its low-word architectural interface.
        self.reg = {name:d.u32(value) for name,value in self.reg.items()}
        return replace(result, return_values={name:d.u32(value) for name,value in result.return_values.items()})


@dataclass(frozen=True)
class Leaf:
    assembly: str
    authority: str
    return_registers: tuple[str, ...] = ('v0',)
    program: d.Program = field(init=False, repr=False)
    identity: str = field(init=False)
    word_pair_multiply: bool = field(init=False, default=False)

    def __post_init__(self):
        if not self.authority.strip():
            raise ValueError('executable callee requires explicit provenance')
        program = d.Program.parse('concrete-leaf', self.assembly)
        word_pair = _instruction_key(program) == _instruction_key(d.Program.parse('word-pair', WORD_PAIR_MULTIPLY))
        object.__setattr__(self, 'word_pair_multiply', word_pair)
        if word_pair:
            object.__setattr__(self, 'return_registers', ('v0','v1'))
        if program.symbols or program.data_words or len(program.instructions) > 512:
            raise ValueError('leaf requires relocation-free code of at most 512 instructions')
        for i in program.instructions:
            if i.opcode not in LEAF_OPS and not word_pair:
                raise ValueError(f'unsupported leaf opcode: {i.opcode}')
            if i.opcode == 'jr' and i.operands != ('ra',) and i.operands != ('$ra',):
                raise ValueError('computed leaf jump is not supported')
            if i.opcode in {'b', 'j'} or cfg.is_conditional_branch(i.opcode):
                program.branch_target(i.operands[-1])
        object.__setattr__(self, 'program', program)
        object.__setattr__(self, 'identity', hashlib.sha256(self.assembly.encode()).hexdigest())


@dataclass(frozen=True)
class OutputBuffer:
    """Non-escaping, address-insensitive, write-only output on one return code.

    This is an assumed environment contract, NOT hardware emulation. A single
    buffer keeps alias obligations narrow. Other stack-pointer arguments decline;
    non-stack addresses remain compared. Frame bounds are necessary checks, not
    recovered C object bounds. Payloads depend on seed, ordinal and other args.
    """
    argument: int
    size: int
    provenance: str
    success_return: int = 0

    def __post_init__(self):
        if not 0 <= self.argument < 4 or not 0 < self.size <= 4096 or not self.provenance.strip():
            raise ValueError('invalid explicit output-buffer contract')

    def labels(self, runner, raw, labels):
        if self.argument >= len(raw):
            raise d.UnsupportedInstruction('output buffer argument exceeds verified arity')
        address = raw[self.argument]
        region = runner.memory.region(address)
        if region.kind == 'stack':
            if not runner.get('sp') <= address < runner.initial_reg['sp']:
                raise d.UnsupportedInstruction('output buffer is not in the active local frame')
            for index, value in enumerate(raw):
                if index != self.argument and d.STACK_BASE <= value < d.STACK_BASE+d.STACK_SIZE:
                    raise d.UnsupportedInstruction('output model cannot discharge stack argument aliasing')
            labels = list(labels)
            labels[self.argument] = f'non-escaping-output[{self.size}]'
        return labels

    def apply(self, runner, raw, arguments, ordinal, returned, instruction):
        if returned != d.u32(self.success_return):
            return
        address = raw[self.argument]
        region = runner.memory.region(address, self.size)
        if region.kind == 'stack' and address+self.size > runner.initial_reg['sp']:
            raise d.MemoryFault(f'output buffer {address:#x}+{self.size} escapes active caller frame')
        for offset in range(self.size):
            byte = d._stable_word('explicit-output', runner.case_seed, ordinal, *arguments, offset) & 255
            runner.memory.write(address+offset, 1, byte)
            runner._record_write(instruction, address+offset, 1, byte,
                f'assumed output argument {self.argument}+{offset}',
                f'explicit output environment: {self.provenance}')


@dataclass
class Environment:
    leaves: dict[str, Leaf] = field(default_factory=dict)
    outputs: dict[str, OutputBuffer] = field(default_factory=dict)
    callbacks: tuple = ()

    def __post_init__(self):
        if self.leaves.keys() & self.outputs.keys():
            raise ValueError('a callee cannot be both executable and modeled')
        if len({item.identity for item in self.callbacks}) != len(self.callbacks):
            raise ValueError('duplicate callback program contracts')

    def manifest(self):
        return {'leaves': {name: {'assembly_sha256': leaf.identity,
                    'text_base': leaf.program.text_base,
                    'linked_rom_binding': getattr(leaf, 'binding', None),
                    'authority': leaf.authority, 'return_registers': leaf.return_registers,
                    'backend': 'closed-word-pair-64' if leaf.word_pair_multiply else
                               'linked-integer32' if getattr(leaf,'binding',None) else 'integer32',
                    'argument_words': 4 if leaf.word_pair_multiply else None}
                    for name, leaf in sorted(self.leaves.items())},
                'assumed_outputs': {name: asdict(value) for name, value in sorted(self.outputs.items())},
                'callback_programs': {item.identity:item.report for item in self.callbacks},
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def source_contracts(source, environment):
    """Bind a closed binary ABI to explicit candidate declarations, not guesses."""
    from solver import project_headers
    masked = project_headers._mask_noncode(source)
    rows = []
    for name, leaf in sorted(environment.leaves.items()):
        if not leaf.word_pair_multiply:
            continue
        declarations = list(re.finditer(r'(?m)^[ \t]*(?:extern\s+)?'
            r'((?:unsigned\s+|signed\s+)?(?:long\s+long|long|int|s32|u32|s64|u64))\s+'
            +re.escape(name)+r'\s*\([^;{}]*\)\s*;', masked))
        for declaration in declarations:
            spelling = ' '.join(declaration[1].split())
            words = 2 if spelling in ('s64','u64','long long','unsigned long long','signed long long') else 1
            rows.append({'callee':name,'status':'result-width-conflict' if words != 2 else 'result-width-compatible',
                'source_declaration':source[declaration.start():declaration.end()].strip(),
                'source_line':source[:declaration.start()].count('\n')+1,
                'declared_result_words':words,'binary_result_words':2,
                'binary_result_mapping':{'v0':'product bits 63..32','v1':'product bits 31..0'},
                'binary_argument_mapping':{'a0:a1':'first unsigned 64-bit word pair','a2:a3':'second unsigned 64-bit word pair'},
                'assembly_sha256':leaf.identity,
                'constraint':'A 32-bit call expression receives only v0. A later u64 cast cannot recover discarded v1. '
                             'Check the declaration, result locals, high/low extraction, and downstream 64-bit argument together. '
                             'This checks result width, not full prototype or C semantic equivalence.'})
    return rows


def load_binary_leaves(repo, names, contracts=None):
    """Read uniquely labeled extracted assembly, never the finished C body.

    Admission binds annotated instruction words to their ROM offsets. This is
    not a compiler/source exact-callee promotion and does not load macro source.
    Missing ROM/annotations, duplicated symbols, or unsupported code decline.
    """
    wanted = set(names)
    found = {name: [] for name in wanted}
    for path in sorted((repo/'asm').rglob('*.s')):
        if path.stem not in wanted:
            continue
        text = path.read_text(errors='replace')
        labels = re.findall(r'(?m)^\s*glabel\s+(\w+)\s*$', text)
        if len(labels) == 1 and labels[0] in wanted:
            found[labels[0]].append((path, text))
    # Prefer the same explicit reference ROM used by project tooling.
    import yaml
    from solver import byte_certificate, sdk_intake
    environment, report = Environment(), []
    rom, config = None, None
    symbols = dict(re.findall(r'(?m)^\s*([A-Za-z_]\w*)\s*=\s*(0x[0-9a-fA-F]+)\s*;',
                              (repo/'symbol_addrs.txt').read_text() if (repo/'symbol_addrs.txt').exists() else ''))
    for name in sorted(wanted):
        try:
            if len(found[name]) != 1:
                raise ValueError('missing/ambiguous single-symbol extracted assembly')
            path, text = found[name][0]
            start = text.index('glabel '+name)
            stop = text.find('endlabel '+name, start)
            body = text[start:stop if stop >= 0 else None]
            leaf = Leaf(body, 'ROM-checked extracted assembly: '+str(path.relative_to(repo)))
            words = re.findall(r'/\*\s*([0-9A-Fa-f]+)\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s*\*/', body)
            if len(words) != len(leaf.program.instructions):
                raise ValueError('missing instruction-byte annotations')
            if rom is None:
                config = yaml.safe_load((repo/'snowboardkids.yaml').read_text())
                candidate_rom = (repo/config['options']['target_path']).read_bytes()
                if hashlib.sha1(candidate_rom).hexdigest() != config['sha1']:
                    raise ValueError('ROM identity differs from extraction configuration')
                rom = candidate_rom
            address = int(symbols[name], 0)
            start_pc = re.search(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+[0-9A-Fa-f]{8}\s*\*/',body)
            if not start_pc or int(start_pc[1],16) != address:
                raise ValueError('callee symbol address disagrees with extracted instructions')
            if sdk_intake.rom_offset(config,address,len(words)*4,len(rom)) != int(words[0][0],16):
                raise ValueError('callee ROM range disagrees with extraction mapping')
            previous = None
            for offset, word in words:
                position = int(offset,16)
                if previous is not None and position != previous+4:
                    raise ValueError('non-contiguous instruction annotations')
                if rom[position:position+4] != bytes.fromhex(word):
                    raise ValueError('callee instruction bytes disagree with ROM')
                previous = position
            # Annotation bytes alone do not authenticate the mnemonic text.
            # Reassemble precisely the parsed instructions the interpreter uses.
            assembler = shutil.which('mips-linux-gnu-as')
            objcopy = shutil.which('mips-linux-gnu-objcopy')
            if not assembler or not objcopy:
                raise ValueError('MIPS assembler/objcopy unavailable for callee binding')
            lines = ['.set noreorder', '.set noat', '.text']
            if leaf.word_pair_multiply:
                lines.append('.set gp=64')
            registers = r'(?<![\w$])('+'|'.join(sorted(d.REGISTER_NAMES))+r')(?!\w)'
            for i in leaf.program.instructions:
                operands = list(i.operands)
                if i.opcode in {'b','j'} or cfg.is_conditional_branch(i.opcode):
                    operands[-1] = f'.Lcallee_{leaf.program.branch_target(operands[-1])}'
                body = re.sub(registers, lambda m:'$'+m[1], ','.join(operands))
                lines += [f'.Lcallee_{i.index}:', i.opcode+' '+body]
            with tempfile.TemporaryDirectory(prefix='callee-binding-') as temporary:
                obj = Path(temporary)/'callee.o'
                binary = Path(temporary)/'text.bin'
                result = subprocess.run([assembler,'-EB','-mabi=32','-march=vr4300','-o',str(obj),'-'],
                    input='\n'.join(lines)+'\n', text=True, capture_output=True, timeout=20)
                if result.returncode:
                    raise ValueError('callee reassembly failed: '+result.stderr[-400:])
                if byte_certificate.object_image(obj.read_bytes())['sections']['.text']['relocations']:
                    raise ValueError('callee reassembly requires relocations')
                subprocess.run([objcopy,'-O','binary','--only-section=.text',str(obj),str(binary)],
                    check=True, capture_output=True, timeout=20)
                expected = b''.join(bytes.fromhex(word) for _,word in words)
                if binary.read_bytes()[:len(expected)] != expected:
                    raise ValueError('parsed callee instructions do not reassemble to ROM words')
            environment.leaves[name] = leaf
            report.append({'callee':name, 'status':'executable', 'sha256':leaf.identity})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, d.UnsupportedInstruction) as exc:
            declined={'callee':name, 'status':'opaque', 'reason':str(exc)}
            contract=(contracts or {}).get(name,{})
            if len(found[name])==1 and contract:
                try:
                    if contract.get('source')!='project-header' or not contract.get('known') or not contract.get('arity_known') or contract.get('issues'):
                        raise ValueError('linked execution requires an unambiguous supported header ABI')
                    returns=contract.get('return_registers')
                    if not isinstance(returns,list) or any(r not in ('v0','v1') for r in returns):
                        raise ValueError('linked execution requires explicit integer/void return ABI')
                    from solver import linked_callee
                    path,text=found[name][0]
                    leaf=linked_callee.from_extracted(repo,name,text,return_registers=tuple(returns))
                    environment.leaves[name]=leaf
                    report.append({'callee':name,'status':'executable','sha256':leaf.identity,
                        'backend':'linked-integer32','binding':leaf.binding,'header_contract':contract,
                        'prior_leaf_decline':str(exc)})
                    continue
                except (OSError,ValueError,KeyError,subprocess.SubprocessError,d.UnsupportedInstruction) as linked_error:
                    declined['linked_decline']=str(linked_error)
            report.append(declined)
    return environment, report
