"""Intended machinery coverage, without turning hypotheses into guarantees."""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from eval.repair_graph import _bindings
from eval.search_replay import digest
from solver import (cfg, capability_contracts, frontend_repair, header_signature_view,
                    global_scalar_view, project_headers, repair_context, scalar_member_index, type_transaction)
from solver.repair_theory import observation


# A conservative inventory, not an ISA semantics implementation or support claim
# for the executor. Unclassified operations always leave an explicit obligation.
INTEGER = set('nop move li lui la addu addiu subu add addi sub and andi or ori xor xori nor not neg negu slt sltu slti sltiu sll srl sra sllv srlv srav mult multu div divu mfhi mflo mthi mtlo'.split())
MEMORY = set('lb lbu lh lhu lw lwu sb sh sw lwl lwr swl swr ll sc ld sd ldc1 sdc1 lwc1 swc1'.split())
CONTROL = set('jr j b beq bne beqz bnez bgez bgtz blez bltz beql bnel beqzl bnezl bgezl bgtzl blezl bltzl bc1t bc1f bc1tl bc1fl'.split())
FLOAT = set('mtc1 mfc1 ctc1 cfc1'.split()) | {a+'.'+b for a in ('add','sub','mul','div','mov','abs','neg','sqrt','c.eq','c.lt','c.le') for b in ('s','d')}
SYSTEM = set('mfc0 mtc0 cache sync syscall break eret tlbp tlbr tlbwi tlbwr'.split())


def requirements(assembly, source, verdict):
    instructions,_ = cfg.parse_assembly(assembly)
    classes = Counter()
    unknown=[]; indirect=[]
    metadata={'.text','.align','.balign','.set','.ent','.end','.size','.type','.globl','.global','.section','.file','.loc'}
    for line_number,raw in enumerate(assembly.splitlines(),1):
        clean=cfg.clean_line(raw)
        label=cfg.LABEL.match(clean)
        if label:clean=(label.group(2) or '').strip()
        if clean.startswith('.') and clean.split()[0] not in metadata:
            unknown.append({'line':line_number,'text':clean,'reason':'opaque assembly directive or emitted bytes'})
    for instruction in instructions:
        op=instruction.opcode
        group = ('integer' if op in INTEGER else 'memory' if op in MEMORY else 'control' if op in CONTROL
                 else 'calls' if op in cfg.CALL_OPS else 'float' if op in FLOAT else 'system' if op in SYSTEM else 'unknown')
        classes[group]+=1
        if group=='unknown': unknown.append({'instruction':instruction.index,'text':instruction.text})
        if op=='jr' and instruction.operands not in {('ra',),('$ra',)} or op=='jalr':
            indirect.append(instruction.index)
    o=observation(verdict)
    needs=set(classes)-{'unknown'} | set(o['blockers']) | {'semantic_equivalence','exact_object'}
    if verdict['compiled'] and not verdict['exact']: needs.add('object_residual')
    if re.search(r'/\*\s*u64\+0x0\s*\*/',source): needs.add('wide_calls')
    if re.search(r'\(u64\)',source) and 's32' in source: needs.add('wide_returns')
    return {'instruction_count':len(instructions), 'instruction_classes':dict(sorted(classes.items())),
            'unknown_instructions':unknown, 'indirect_control':indirect, 'needs':sorted(needs),
            'coverage_complete':bool(instructions) and not unknown,
            'blockers':o['blockers'],'diagnostics_complete':o['diagnostics_complete'],
            'reference_bodies_used':False,
            'scope':'observed binary operation categories and draft obligations; not recovered intent'}


def assess(source,function,verdict,*,assembly,context,contracts,facts,connected):
    capability_contracts.validate_catalog(contracts)
    if connected not in {'theory','compile-recovery','model-repair'}:
        raise ValueError('unknown capability caller')
    if any(type(v) not in (bool,type(None)) for v in facts.values()):
        raise ValueError('capability prerequisites must be true, false or unknown')
    _bindings({'source':source,'source_sha256':digest(source),'verdict':verdict},context)
    certificate=verdict.get('verification') or {}
    if verdict['exact'] and (not verdict['compiled'] or certificate.get('exact') is not True
            or certificate.get('status')!='object_sections_exact'
            or certificate.get('kind')!='mips_object_section_certificate' or certificate.get('schema_version')!=1
            or (verdict.get('frontend') or {}).get('passed') is not True or verdict.get('error')):
        raise ValueError('exact capability witness requires compiler certificate and frontend pass')
    req=requirements(assembly,source,verdict)
    diagnostic=(verdict.get('frontend') or {}).get('diagnostics') or ''
    # Derived predicates take precedence over caller hints.
    predicates={**facts, 'assembly_available':bool(req['instruction_count']),
        'draftable_subset':True if req['coverage_complete'] and not req['indirect_control'] and not
            (set(req['instruction_classes'])&{'system','float'}) else None,
        'compiled_candidate':verdict['compiled'] and not bool(verdict.get('error')),
        'source_bound_feedback':bool((verdict.get('frontend') or {}).get('source_sha256'))
            and type((verdict.get('frontend') or {}).get('passed')) is bool
            and (verdict.get('frontend') or {}).get('status')!='unavailable' and not bool(verdict.get('error')),
        'void_member_diagnostic':bool(re.search(r"member reference base type 'void'",diagnostic)),
        'named_wide_fields':bool(re.search(r'\b\w+->unk_?(?:[0-9a-fA-F]+)\b',source)) if 'wide_calls' in req['needs'] else None}
    capabilities=[]
    for contract in contracts['contracts']:
        failed_domain=[p for p in contract['domain'] if predicates.get(p) is False]
        missing=[p for p in contract['requires'] if predicates.get(p) is False]
        unknown=[p for p in contract['domain']+contract['requires'] if predicates.get(p) is None]
        applicability=('outside-declared-domain' if failed_domain else 'missing-prerequisite' if missing
                       else 'undetermined' if unknown else 'expected-within-contract')
        needed=bool(set(contract['covers'])&set(req['needs']))
        wired=connected in contract['callers']
        capabilities.append({**deepcopy(contract),'needed':needed,'connected':wired,
            'applicability':applicability,'status':'available-not-connected' if needed and not wired and not failed_domain else applicability,
            'failed_domain':failed_domain,'missing_prerequisites':missing,'unknown_prerequisites':unknown})
    exact=verdict['exact']
    unresolved=[] if exact else ['complete-candidate-construction','repair-composition','search-completeness','all-input-equivalence']
    if req['indirect_control'] and not exact: unresolved.append('indirect-control-targets')
    if not req['coverage_complete'] and not exact: unresolved.append('instruction-requirement-coverage')
    investigations=[]
    if not exact:
        for row in capabilities:
            if not row['needed']: continue
            if row['failed_domain'] or row['missing_prerequisites'] or row['unknown_prerequisites'] or not row['connected']:
                investigations.append({'contract':row['id'],'status':row['status'],
                    'action':'inspect-domain-or-representation' if row['failed_domain'] else
                             'inspect-wiring-and-prerequisites' if not row['connected'] else 'establish-prerequisites',
                    'requirements':row['failed_domain']+row['missing_prerequisites']+row['unknown_prerequisites']})
    result={'schema_version':1,'kind':'machinery-capability-envelope',
        'inputs':deepcopy(dict(source=source,function=function,verdict=verdict,assembly=assembly,context=context,
                               contracts=contracts,facts=facts,connected=connected)),
        'source_sha256':digest(source),'assembly_sha256':digest(assembly),'target_sha256':context['target_sha256'],
        'compiler_sha256':context.get('compiler_sha256'),'receipt_id':verdict.get('receipt_id'),
        'requirements':req,'capabilities':capabilities,'predicates':predicates,
        'goals':{'exact_c':{'status':'demonstrably-reachable' if exact else 'undetermined',
                            'receipt_id':verdict.get('receipt_id') if exact else None},
                 'semantic_c':{'status':'witnessed-by-exact-object' if exact else 'undetermined',
                               'scope':'same linked context and machine environment; no independent all-input proof'},
                 'original_source':{'status':'not-identifiable-from-this-evidence'},
                 'binary_preservation':{'status':'original-target-available' if predicates.get('target_available') else 'unassessed',
                                        'scope':'retaining original code; not a C reconstruction'}},
        'composition':{'complete_constructor_available':False,'unresolved':unresolved,
                       'rule':'local candidate construction does not imply compositional semantic or byte equivalence'},
        'investigations':investigations,'global_impossibility_established':False,'training_eligible':False,
        'scope':'intended contracts of inventoried machinery; conditions are explicit, not success probabilities'}
    result['sha256']=digest(result)
    return result


def validate_assessment(report):
    try:
        rebuilt=assess(**report['inputs'])
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('invalid capability assessment structure') from exc
    if report!=rebuilt:
        raise ValueError('capability assessment differs from retained inputs')
    return report


def compare(before,after,*,contract_id):
    validate_assessment(before);validate_assessment(after)
    for key in ('target_sha256','compiler_sha256'):
        if before[key]!=after[key]: raise ValueError('capability comparison context mismatch')
    if before['inputs']['context'].get('assistance')!=after['inputs']['context'].get('assistance'):
        raise ValueError('capability comparison assistance mismatch')
    contract=next(r for r in before['capabilities'] if r['id']==contract_id)
    a=observation(before['inputs']['verdict']);b=observation(after['inputs']['verdict'])
    changed=before['source_sha256']!=after['source_sha256']
    status=('goal-witnessed' if b['exact'] else 'inconclusive' if a['error'] or b['error'] else
            'outside-contract-construction' if changed and contract['applicability']=='outside-declared-domain' else
            'construction-observed-validation-open' if changed else 'expected-construction-not-observed'
            if contract['applicability']=='expected-within-contract' else 'prerequisites-unresolved')
    return {'contract':contract_id,'expectation':contract['applicability'],'status':status,
        'parent_receipt_id':before['receipt_id'],'receipt_id':after['receipt_id'],
        'action':'retain-witness' if b['exact'] else 'investigate-implementation-or-contract' if status=='expected-construction-not-observed'
                 else 'investigate-generator-domain-guard' if status=='outside-contract-construction'
                 else 'investigate-composition-or-representation',
        'net_error_reduction':sum(a['blockers'].values())-sum(b['blockers'].values())
            if a['diagnostics_complete'] and b['diagnostics_complete'] else None,
        'new_blockers':sorted(set(b['blockers'])-set(a['blockers'])) if a['diagnostics_complete'] else [],
        'proven_implementation_bug':False,'global_impossibility_established':False}


def inspect_expectations(assessment,routes):
    """Compare recorded proposal inspection with intended construction contracts."""
    validate_assessment(assessment)
    contracts={c['id']:c for c in assessment['capabilities']}
    out=[]
    for route in routes:
        contract=contracts.get(route['id'])
        if (contract and contract['needed'] and contract['connected'] and contract['output']=='c-candidate'
                and contract['applicability']=='expected-within-contract' and not route['candidates']):
            out.append({**compare(assessment,assessment,contract_id=route['id']),
                        'owner_report':deepcopy(route),'action':'investigate-implementation-or-contract'})
    return out


def workspace_assessment(source,function,verdict,*,context,repo,workspace,contracts=None,connected='theory'):
    """Read assembly, target identity and included header declarations only.

    No source generation, layout compiler probes, oracle calls or reference body
    reads. Unmeasured predicates remain unknown rather than inferred from names.
    """
    repo,ws=Path(repo),Path(workspace)
    target=ws/'target.o'
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()!=context['target_sha256']:
        raise ValueError('workspace target binding mismatch')
    assembly=(ws/'target.s').read_text() if (ws/'target.s').is_file() else ''
    abi=type_transaction.contract(repo,source,function)
    shape=type_transaction.signature(source,function)
    supported=None
    if abi['status']=='locked' and shape:
        old_ret,old_params=shape;new_ret,new_params=abi['shape']
        width=header_signature_view.width
        supported=(len(old_params)==len(new_params) and len(old_params)<=8
            and width(old_ret) is not None and width(old_ret)==width(new_ret)
            and all(width(a) is not None and width(a)!=0 and width(a)==width(b) for a,b in zip(old_params,new_params)))
    diagnostic=(verdict.get('frontend') or {}).get('diagnostics') or ''
    conflict_site=False;ordinary_sites=None
    try:
        definition,end=repair_context.definition(source,function)
        number=source.count('\n',0,definition.start())+1
        line=source.splitlines()[number-1]
        diag=re.search(r'^candidate\.c:'+str(number)+r":\d+: error: conflicting types for '"+
                       re.escape(function)+r"'\n\s*"+str(number)+r' \| (.*)',diagnostic,re.M)
        conflict_site=bool(diag and diag[1]==line and '\t' not in line)
        body=project_headers._mask_noncode(source)[definition.end():end-1]
        raw=[] if shape and not shape[1] else definition[2].split(',')
        names=[re.fullmatch(r'\s*[\w\s*]+?\b([A-Za-z_]\w*)\s*',p) for p in raw]
        ordinary_sites=bool(shape and len(raw)==len(shape[1]) and all(names)
            and len({n[1] for n in names if n})==len(names)
            and not re.search(r'(?m)^\s*#',body)
            and not re.search(r'(?m)^\s*#\s*define\s+'+re.escape(function)+r'\b',
                              source+'\n'+global_scalar_view.header_text(repo,source)))
        if shape and abi['status']=='locked' and shape[0]!=abi['shape'][0]:
            returns=re.findall(r'\breturn\b([^;{}]*);',body)
            if len(returns)!=len(re.findall(r'\breturn\b',body)) or any(not r.strip() for r in returns):ordinary_sites=False
    except ValueError:
        ordinary_sites=False
    indexable,scalar_site=_scalar_domain(source,diagnostic)
    facts={'target_available':target.is_file(),'big_endian_o32':frontend_repair.big_endian_o32(target) if target.is_file() else None,
        'm2c_available':(repo/'.venv/bin/m2c').is_file(),'header_abi_locked':abi['status']=='locked',
        'header_declarations':bool(project_headers._included_declarations(repo,source)),
        'signature_shape_supported':supported,
        'signature_change_needed':shape!=abi['shape'] if shape and abi['status']=='locked' else None,
        'signature_conflict_site':conflict_site,'signature_edit_sites_supported':ordinary_sites,
        'compiler_recipe':bool(verdict.get('compiler_recipe')),
        'indexable_member_base':indexable,'scalar_member_site_supported':scalar_site,
        'single_named_wide_parameter':bool(re.search(r'\(\s*(?:s32|u32)\s+(\w+)_unk0\s*,\s*(?:s32|u32)\s+\1_unk4\s*\)',source))}
    return assess(source,function,verdict,assembly=assembly,context=context,contracts=contracts or capability_contracts.catalog(),
                  facts=facts,connected=connected)


def _scalar_domain(source,diagnostics):
    """Check the declared site contract, without calling the rewrite generator."""
    lines=source.splitlines();indexable=[];sites=[]
    for diag in scalar_member_index._DIAGNOSTIC.finditer(diagnostics):
        line,column=int(diag['line']),int(diag['col'])
        if not 1<=line<=len(lines) or not 1<=column<=len(lines[line-1]):continue
        member=scalar_member_index._MEMBER.match(lines[line-1][column-1:])
        if not member:continue
        width=scalar_member_index.width_of(diag['type']) or scalar_member_index.width_of(diag['aka'] or '')
        array=bool(scalar_member_index._ARRAY.fullmatch(diag['type']) or scalar_member_index._ARRAY.fullmatch(diag['aka'] or ''))
        valid=bool(width and (member['op']=='->' or array))
        indexable.append(valid)
        sites.append(valid and member['sign']!='-' and int(member['offset'],16)%width==0)
    return (any(indexable),any(sites)) if indexable else (None,None)
