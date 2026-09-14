"""Header-measured callback-slot bindings from binary pointer provenance.

Contracts describe argument words only: no callee effects or KB facts.
Ambiguous globals/union fields, lost roots and non-word ABIs fail closed.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re

from solver import dataflow


def program_identity(program):
    """Bind instruction indices to parsed code/data, not a mutable function name."""
    payload={'instructions':[(i.opcode,i.operands) for i in program.instructions],
        'labels':sorted(program.labels.items()),'text_base':program.text_base,
        'symbol_addresses':sorted(program.symbol_addresses.items()),
        'data_words':sorted(program.data_words.items()),'data_bytes':sorted(program.data_bytes.items())}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class CallbackProgram:
    identity: str
    report: dict

    @property
    def contracts(self):
        return {row['instruction']:row['contract'] for row in self.report['calls'] if row['status']=='bound'}


def admit(assembly,measurement):
    """Explicit opt-in; fresh binding, exact parsed program, header-assisted ABI.

    The supplied measurement must come from the caller's audited target-header
    probe. This does not certify its provenance or authorize callback effects.
    """
    from solver import mips_differential as d
    report=bind(assembly,measurement)
    identity=program_identity(d.Program.parse('callback-binding',assembly))
    report={**report,'program_identity':identity,'argument_comparison_admitted':True,
            'execution_admitted':False,'effects_known':False,
            'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return CallbackProgram(identity,report)


def word_abi(canonical):
    match=re.fullmatch(r'\s*(.+?)\s*\(\s*\*\s*\)\s*\(([^()]*)\)\s*',canonical)
    if not match:
        return None
    scalar={'s8','u8','s16','u16','s32','u32','int','unsigned int','signed int',
            'short','unsigned short','signed short','char','signed char','unsigned char',
            'long','signed long','unsigned long'}
    def word(typ):
        typ=re.sub(r'\b(?:const|volatile)\s*','',typ).strip()
        return typ in scalar or bool(re.fullmatch(r'(?:struct\s+)?\w+\s*\*',typ))
    result=match[1].strip()
    parameters=[p.strip() for p in match[2].split(',')]
    if parameters==['void']:
        parameters=[]
    # Empty parentheses are unprototyped in the project's C89 dialect.
    if (not parameters and match[2].strip()!='void') or len(parameters)>8:
        return None
    if not all(word(p) for p in parameters) or (result!='void' and not word(result)):
        return None
    return {'argument_words':len(parameters),'parameters':parameters,
            'return_registers':[] if result=='void' else ['v0']}


def bind(assembly, measurement):
    layouts=measurement['layouts']
    roots={}
    for decl in measurement.get('global_declarations',[]):
        roots.setdefault(decl['name'],set()).add(decl['spelling'])
    declines=[]

    def decline(reason):
        declines.append(reason)
        return None

    def resolve(value,depth=0):
        if value is None or depth>8 or value.kind!='load' or value.offset or value.width!=4:
            return decline('missing/unmodeled 32-bit pointer load provenance or path depth exceeded')
        address=value.inner
        if address is None:
            return decline('pointer load address is unresolved')
        if address.kind=='address' and address.offset==0:
            declarations=roots.get(address.name,set())
            if len(declarations)!=1:
                return decline('missing or conflicting header global declaration: '+address.name)
            match=re.fullmatch(r'\s*(\w+)\s*\*\s*',next(iter(declarations)))
            if not match or match[1] not in layouts:
                return decline('root is not a pointer to a measured header record: '+address.name)
            return {'object_type':match[1],'path':[{'global':address.name,'type':next(iter(declarations))}]}
        if address.kind!='load':
            return decline('path root is not an independently declared global pointer')
        owner=resolve(replace(address,offset=0),depth+1)
        if not owner or 'object_type' not in owner:
            return decline('cannot establish owner record for callback-slot path')
        fields=[field for field in layouts[owner['object_type']]
                if field['offset']==address.offset and field['width']==4 and not field.get('array')]
        if len(fields)!=1:
            return decline('missing or ambiguous measured 4-byte field at '+owner['object_type']+'+'+hex(address.offset))
        field=fields[0]
        path=owner['path']+[{'owner':owner['object_type'],**field}]
        if field.get('pointee') in layouts:
            return {'object_type':field['pointee'],'path':path}
        abi=word_abi(field.get('canonical',''))
        return {'abi':abi,'path':path} if abi else decline('field ABI outside supported fixed word-only dialect: '+field.get('canonical',''))

    analysis=dataflow.analyse(assembly)
    rows=[]
    for index,call in sorted(analysis.callsites.items()):
        if call.opcode!='jalr':
            continue
        declines.clear()
        bound=resolve(call.target_value)
        rows.append({'instruction':index,'status':'bound' if bound and 'abi' in bound else 'unresolved',
            'binary_value':asdict(call.target_value) if call.target_value else None,
            'contract':bound if bound and 'abi' in bound else None,
            'decline_reasons':list(dict.fromkeys(declines))})
    return {'kind':'header-assisted-binary-callback-bindings','calls':rows,
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'measurement_receipt':measurement.get('receipt_path'),
        'measurement_object_sha256':measurement.get('probe_object_sha256'),
        'authority':'header root declarations and compiler-measured fields matched to binary load paths',
        'execution_admitted':False,'effects_known':False}
