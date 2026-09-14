"""Bounded assembly-derived interface hypotheses fed back into m2c.

These are candidate prototypes, NOT admitted ABI/effect contracts or KB facts.
Only missing fixed-word declarations from small uniquely resolved disassemblies
are considered. No reference C bodies, recursive source search or header edits.
"""
import hashlib
import re
import subprocess
from solver import cfg, m2c_input, project_headers, repair_context, target_intake


def hypotheses(repo, source, function, limit=2):
    mask=project_headers._mask_noncode(source)
    definition,_=repair_context.definition(source,function)
    names=[]
    for declaration in re.finditer(r'(?m)^([^;{}\n]+?)\b(\w+)\s*\(([^;{}\n]*)\)\s*;',mask[:definition.start()]):
        if 'M2C_UNK' in declaration[0] and declaration[2] != function:
            names.append(declaration[2])
    report={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'prototypes':[], 'declines':[],
        'authority':'callee m2c interface hypotheses; not header/ABI/effect proof', 'reference_bodies_used':False}
    words={'s8','u8','s16','u16','s32','u32'}
    for name in sorted(set(names))[:limit]:
        try:
            if project_headers.declarations(repo,name):
                raise ValueError('existing header declaration must take precedence')
            resolution=target_intake.resolve(repo,name)
            if resolution.kind!='disassembly' or resolution.symbol!=name:
                raise ValueError('requires uniquely named binary disassembly')
            assembly=resolution.path.read_text()
            graph=cfg.build(assembly)
            if not graph.instructions or len(graph.instructions)>256 or len(assembly)>32768:
                raise ValueError('callee exceeds bounded interface probe')
            draft,meta=m2c_input.draft(repo,resolution.path,context_headers=('common.h',),valid_syntax=True)
            if draft.returncode:
                raise ValueError('callee m2c failed: '+draft.stderr[-500:])
            signature,_=repair_context.definition(draft.stdout,name)
            ret=signature[1].strip()
            raw=signature[2].strip()
            params=[]
            if raw!='void':
                for part in raw.split(','):
                    parameter=re.fullmatch(r'\s*(s8|u8|s16|u16|s32|u32)\s+\w+\s*',part)
                    if not parameter:
                        raise ValueError('unresolved/non-word callee parameter')
                    params.append(parameter[1])
            if ret not in words|{'void'} or len(params)>4:
                raise ValueError('unresolved/non-word callee interface')
            report['prototypes'].append({'name':name,'return_type':ret,'parameters':params,
                'assembly_path':str(resolution.path),'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
                'generated_source_sha256':hashlib.sha256(draft.stdout.encode()).hexdigest(),'draft':meta})
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            report['declines'].append({'name':name,'reason':str(exc)})
    report['omitted_names']=sorted(set(names))[limit:]
    return report
