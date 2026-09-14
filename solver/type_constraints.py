"""Header-assisted type-plan candidates from checked layouts and pointer flow.

Clang parses declarations; the configured target compiler measures offsets and
sizes. Pointer assignments and unkNN labels in the draft are hypotheses, not
binary evidence. Ambiguous union arms remain separate bounded candidates.
"""
import hashlib
import itertools
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from solver import compile_obligations, compiler_recipe, frontend_check, project_headers, repair_context, type_plan


def _walk(node):
    yield node
    for child in node.get('inner',[]):
        yield from _walk(child)


def declarations(ast, wanted):
    """Flatten named member paths without calculating their C layout ourselves."""
    nodes=list(_walk(ast))
    records={n['id']:n for n in nodes if n.get('kind')=='RecordDecl' and n.get('completeDefinition')}
    aliases={}
    for n in nodes:
        if n.get('kind')=='TypedefDecl':
            refs=[r.get('decl',{}).get('id') for r in _walk(n) if r.get('kind')=='RecordType']
            if refs and refs[0] in records: aliases[n['name']]=refs[0]
    record_names={rid:next((a for a,r in aliases.items() if r==rid and a in wanted),
                          next((a for a,r in aliases.items() if r==rid),'')) for rid in records}
    tags={n['tagUsed']+' '+n['name']:rid for rid,n in records.items() if n.get('name')}
    def record_id(spelling):
        spelling=re.sub(r'\b(?:const|volatile)\s+','',spelling).strip()
        return aliases.get(spelling) or tags.get(spelling)
    def fields(rid,prefix='',visited=()):
        if rid in visited: return []
        out=[]; anonymous=None
        for f in records[rid].get('inner',[]):
            if f.get('kind')=='RecordDecl' and not f.get('name'):
                anonymous=f.get('id')
            if f.get('kind')!='FieldDecl' or not f.get('name') or f.get('isBitfield'): continue
            typ=f['type']; spelling=typ['qualType']; canonical=typ.get('desugaredQualType',spelling)
            path=prefix+f['name']
            nested=record_id(canonical) or record_id(spelling)
            if not nested and ('unnamed' in canonical or 'anonymous' in canonical): nested=anonymous
            if nested in records and '*' not in canonical and '[' not in canonical:
                out+=fields(nested,path+'.',(*visited,rid))
                continue
            # Flexible arrays have no sizeof; pointer-to-array and fixed arrays
            # are represented but are not guessed to be pointers to records.
            if re.search(r'\[\s*\]',canonical): continue
            pointee=None
            if canonical.endswith('*'):
                target=record_id(canonical[:-1].strip()) or record_id(spelling[:-1].strip())
                pointee=record_names.get(target) if target else None
            out.append({'member':path,'spelling':spelling,'canonical':canonical,
                        'pointee':pointee,'pointer':'*' in canonical,
                        'array':'[' in canonical and '*' not in canonical})
        return out
    # Typedef names, or a tag spelling such as `struct Actor` for records a
    # draft declares without a typedef.
    return {name:fields(aliases.get(name) or tags[name]) for name in wanted if name in aliases or name in tags}


def probe_source(includes, layouts, global_symbols=()):
    if (len(global_symbols)>64 or len(set(global_symbols))!=len(global_symbols)
            or any(not re.fullmatch(r'[A-Za-z_]\w*',name) for name in global_symbols)):
        raise ValueError('global extent probe requires up to64 unique identifiers')
    rows=[(name,f) for name,fields in layouts.items() for f in fields]
    values=['0x71A9B30D']
    for name,f in rows:
        expression=f'(({name} *)0)->{f["member"]}'
        values += [f'(unsigned int)&({expression})',f'sizeof({expression})',f'sizeof({name})']
    values += [f'sizeof({name})' for name in global_symbols]
    return includes+'\nunsigned int decomp_layout_values[] = {\n'+',\n'.join(values)+'\n};\n',rows


def measure(repo,ws,source,function,target, *, global_symbols=(), layout_globals=(), records=()):
    """Header-only probe; never include or read a reference C implementation.

    `records` instead measures named records as the candidate itself declares
    them: the probe compiles the candidate's text BEFORE its function definition
    (includes plus its own record declarations), never the function body.
    """
    if records:
        if len(records)>8 or any(not re.fullmatch(r'(?:(?:struct|union)\s+)?[A-Za-z_]\w*', r) for r in records):
            raise ValueError('record layouts require up to eight record spellings')
        provided,wanted=[],list(records)
        includes=source[:repair_context.definition(source,function)[0].start()]
    else:
        provided=compile_obligations.header_types(repo,source,function)
        wanted=[r['type'] for r in provided]
        includes='\n'.join(re.findall(r'(?m)^\s*#\s*include[^\n]+',source))
    if len(layout_globals)>8 or any(not re.fullmatch(r'[A-Za-z_]\w*', n) for n in layout_globals):
        raise ValueError('layout roots require up to eight global identifiers')
    headers=frontend_check.recipe(str(repo),(repo/'Makefile').read_text(),target)
    selected=compiler_recipe.resolve(repo,target)
    cross=next((p for p in ('mips-linux-gnu-','mips64-linux-gnu-','mips64-elf-') if shutil.which(p+'objcopy')),None)
    if not cross: raise ValueError('no MIPS object tools for layout probe')
    with tempfile.TemporaryDirectory(prefix='type-constraints-',dir=ws) as directory:
        folder=Path(directory); header=folder/'headers.c'; header.write_text(includes+'\n')
        command=[*headers['command'],'-Xclang','-ast-dump=json',str(header)]
        process=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=60)
        if process.returncode: raise ValueError('header AST probe rejected: '+process.stderr[-2000:])
        ast=json.loads(process.stdout)
        globals_=[{'name':node['name'],'spelling':node['type']['qualType'],
                   'canonical':node['type'].get('desugaredQualType',node['type']['qualType'])}
                  for node in ast.get('inner',[]) if node.get('kind')=='VarDecl' and node.get('name')]
        if layout_globals:
            wanted=[]
            for name in layout_globals:
                matches={g['canonical'] for g in globals_ if g['name']==name}
                if len(matches)!=1:raise ValueError('layout root requires unique global type: '+name)
                typ=re.sub(r'\[[0-9]+\]', '', next(iter(matches))).strip()
                typ=re.sub(r'^(?:struct|union)\s+', '',typ)
                if not re.fullmatch(r'\w+',typ):raise ValueError('layout root is not a record object/array: '+name)
                wanted.append(typ)
        layouts=declarations(ast,wanted)
        if layout_globals and set(wanted)-set(layouts):raise ValueError('layout root record unavailable')
        if layout_globals:
            elements={re.sub(r'^(?:struct|union)\s+', '',re.sub(r'\[[0-9]+\]', '',f['canonical']).strip())
                      for fields in layouts.values() for f in fields if f.get('array')}
            layouts.update(declarations(ast,sorted(elements)))
        if (not layouts and not global_symbols) or sum(map(len,layouts.values()))>1000: raise ValueError('empty or oversized header layout packet')
        for name in global_symbols:
            declarations_=[g for g in globals_ if g['name']==name]
            if len(declarations_)!=1:
                raise ValueError('global extent requires unique header declaration: '+name)
        code,rows=probe_source(includes,layouts,global_symbols)
        probe=folder/'layouts.c'; obj=folder/'layouts.o'; data=folder/'data.bin'; probe.write_text(code)
        command=[*selected['command'],'-o',str(obj),str(probe)]
        process=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=90)
        if process.returncode: raise ValueError('target layout probe rejected: '+process.stderr[-3000:])
        symbols=subprocess.run([cross+'objdump','-t',str(obj)],capture_output=True,text=True,check=True,timeout=20).stdout
        symbol=re.search(r'(?m)^([0-9a-fA-F]+)\s+[^\n]*\s(\.data)\s+[0-9a-fA-F]+\s+decomp_layout_values\s*$',symbols)
        if not symbol: raise ValueError('layout constant array missing from target object')
        subprocess.run([cross+'objcopy','--dump-section','.data='+str(data),str(obj)],check=True,capture_output=True,timeout=20)
        payload=data.read_bytes(); offset=int(symbol[1],16)
        count=1+3*len(rows)+len(global_symbols)
        if offset+count*4>len(payload): raise ValueError('truncated target layout constant array')
        values=struct.unpack_from('>'+str(count)+'I',payload,offset)
        if values[0]!=0x71A9B30D: raise ValueError('layout probe byte order/symbol mismatch')
        for index,(name,f) in enumerate(rows):
            f.update(offset=values[index*3+1],width=values[index*3+2],owner_size=values[index*3+3])
        extents=[{'name':name,'size':values[1+3*len(rows)+index],
                  'declaration':next(g for g in globals_ if g['name']==name),
                  'scope':'declared object only; not pointee extent or initialized contents'}
                 for index,name in enumerate(global_symbols)]
        report={'kind':'target-compiler-header-layouts','source_sha256':compiler_recipe.sha(source.encode()),
                'layout_globals':list(layout_globals),
                'header_definitions':provided,'layouts':layouts,'global_declarations':globals_,
                'global_extents':extents,'compiler_recipe':selected,
                'frontend_recipe':headers,'probe_source':code,'probe_object_sha256':compiler_recipe.sha(obj.read_bytes()),
                'authority':'project headers measured by target compiler, not binary-derived C types'}
    path=ws/('type-constraints-layout-'+compiler_recipe.sha(json.dumps(report,sort_keys=True).encode())+'.json')
    path.write_text(json.dumps(report,indent=2)+'\n')
    report['receipt_path']=str(path)
    return report


def solve(source,function,assembly,layouts,max_plans=8):
    """Arc-consistent pointer/member hypotheses; return bounded full type plans."""
    if max_plans<1: raise ValueError('positive type-plan budget required')
    required=type_plan.inventory(source)
    if not required: return {'status':'not_applicable','plans':[]}
    mask=project_headers._mask_noncode(source)
    match,end=repair_context.definition(source,function)
    params=[re.search(r'([A-Za-z_]\w*)\s*$',p.strip()).group(1) for p in match[2].split(',') if re.search(r'([A-Za-z_]\w*)\s*$',p.strip())]
    roots={r['base'].split('->')[0] for r in required}
    voids=set(re.findall(r'\bvoid\s*\*\s*([A-Za-z_]\w*)\b',mask))
    if not roots<=voids:
        return {'status':'declined','reason':'requires original void-pointer member roots','plans':[]}
    name=type_plan.NAME; expression=name+r'(?:\s*->\s*'+name+r')*'
    equalities=[]; declined_assignments=[]
    body=mask[match.end():end]
    for assignment in re.finditer(r'\b('+expression+r')\s*=\s*('+expression+r')\s*;',body):
        # A regex match may be only the suffix of an lvalue: *p = q,
        # obj.p = q, or array[i] = q must not become p = q / i = q.
        # Admit standalone statements only; control-prefixed or otherwise
        # unsupported forms remain explicit debt, not invented equalities.
        preceding=body[:assignment.start()].rstrip()
        if preceding and preceding[-1] not in ';{}:':
            declined_assignments.append({'assignment':assignment.group(0),
                'preceding_token':preceding[-1],
                'reason':'not a standalone direct-pointer assignment'})
            continue
        left,right=(re.sub(r'\s+','',v) for v in assignment.groups())
        if ((left in voids or right in voids)
                and left.split('->')[0] in voids and right.split('->')[0] in voids):
            equalities.append((left,right))
    parent={}
    def find(n):
        parent.setdefault(n,n)
        if parent[n]!=n: parent[n]=find(parent[n])
        return parent[n]
    for r in required: find(r['base'])
    for a,b in equalities: parent[find(a)]=find(b)
    domains={find(n):set(layouts) for n in list(parent)}
    analysis,accesses=compile_obligations.analyse(assembly)
    widths={}
    for access in analysis.accesses.values():
        a=access.address
        if a and a.kind=='address' and re.fullmatch(r'param\d+',a.name):
            index=int(a.name[5:])
            if index<len(params): widths.setdefault((find(params[index]),a.offset),set()).add(access.width)
    obligations=[]
    for r in required:
        key=(r['base'],r['field']); child=r['base']+'->'+r['field']
        obligations.append((key,find(r['base']),find(child) if child in parent else None,r['offset_hypothesis']))
    def choices(group,typ,offset,child):
        return [f for f in layouts[typ] if f['offset']==offset and not f.get('array')
                and (not widths.get((group,offset)) or f['width'] in widths[group,offset])
                and (child is None or f.get('pointee') in domains[child])]
    eliminations=[]
    changed=True
    while changed:
        changed=False
        for key,group,child,offset in obligations:
            for typ in sorted(domains[group]):
                if not choices(group,typ,offset,child):
                    domains[group].remove(typ); changed=True
                    eliminations.append({'node':group,'type':typ,'access':key,'offset':offset,
                                         'reason':'no compatible direct header field/width/pointee'})
            if child is not None:
                allowed={f['pointee'] for typ in domains[group] for f in choices(group,typ,offset,child)}
                old=domains[child].copy(); domains[child]&=allowed
                changed |= old!=domains[child]
    report={'kind':'pointer-flow-type-constraints','source_sha256':compiler_recipe.sha(source.encode()),
            'assembly_sha256':compiler_recipe.sha(assembly.encode()),'equalities':equalities,
            'declined_assignment_forms':declined_assignments,
            'domains':{n:sorted(domains[find(n)]) for n in parent if find(n) in domains},
            'eliminations':eliminations,'binary_entry_accesses':accesses,
            'authority':'candidate direct-field/type-flow hypotheses, not proven C types',
            'plans':[]}
    if any(not d for d in domains.values()):
        return {**report,'status':'inconsistent','reason':'no solution in supplied header/direct-access dialect'}
    groups=sorted(domains)
    assignments=itertools.product(*(sorted(domains[g]) for g in groups))
    explored=0; truncated=False
    for values in assignments:
        explored+=1
        if explored>4096: truncated=True; break
        types=dict(zip(groups,values)); options=[]
        for key,group,child,offset in obligations:
            fields=[f for f in choices(group,types[group],offset,child)
                    if child is None or f.get('pointee')==types[child]]
            options.append([{'base':key[0],'field':key[1],'member':f['member']} for f in fields])
        if not all(options): continue
        for fields in itertools.product(*options):
            if len(report['plans'])>=max_plans: truncated=True; break
            report['plans'].append({'hypothesis':'compiler-measured direct fields plus draft pointer-flow constraints; ambiguous union choices enumerated',
                'types':[{'variable':r,'type':types[find(r)]} for r in sorted(roots)],'fields':list(fields)})
        if truncated: break
    return {**report,'status':'candidates' if report['plans'] else 'inconsistent',
            'assignments_considered':explored,'truncated':truncated}
