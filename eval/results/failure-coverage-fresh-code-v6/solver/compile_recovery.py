"""Bounded compile-context recovery, reusing existing header/global adapters.

All declarations are candidate hypotheses. No reference C bodies or KB writes.
The caller compiles/logs every emitted variant and retains its actual parent.
"""
from pathlib import Path
import re
import subprocess

from solver import buildtypes, compile_obligations, globaldecl, m2c_adapter, m2c_input, project_headers, unknowns


INCLUDES = re.compile(r'(?m)^[ \t]*#\s*include\s*[<"]([^>"\n]+)[>"][^\n]*(?:\n|$)')


def scalar_header_prototypes(repo, source, diagnostics):
    """Import unambiguous primitive prototypes without header macro side effects.

    Candidate-only projection, not an active-header ABI/effect admission. Unknown
    types, conditional signature alternatives and complex interfaces decline.
    """
    import hashlib
    from solver import type_transaction
    names = sorted(set(re.findall(r"implicit declaration of function ['\"](\w+)['\"]", diagnostics)))
    report = {'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'prototypes':[], 'declines':[], 'omitted':names[4:],
        'authority':'header declaration projection; compiler adjudicates, not runtime ABI/effect proof'}
    additions = []
    primitive = {'void','char','short','int','long','signed','unsigned'}
    for name in names[:4]:
        declarations = project_headers.declarations(repo,name,all_variants=True,max_results=13)
        shapes = [type_transaction.signature(d.prototype,name) for d in declarations]
        if (not shapes or len(declarations)>=13 or any(s is None for s in shapes) or len(set(shapes)) != 1
                or any(t not in primitive for part in (shapes[0][0], *shapes[0][1]) for t in part)):
            report['declines'].append({'name':name,'reason':'requires one primitive scalar header signature'})
            continue
        prototype = declarations[0].prototype.rstrip(';')+';'
        additions.append(prototype)
        report['prototypes'].append({'name':name,'prototype':prototype,
            'headers':[{'include':d.include,'sha256':hashlib.sha256(
                (repo/'include'/d.include).read_bytes()).hexdigest()} for d in declarations]})
    return ('\n'.join(additions)+'\n'+source if additions else source), report


def header_variant(repo: Path, function: str, asm: str, source: str, target: str):
    sdk = target.startswith('build/src/ultra/')
    original = INCLUDES.findall(source)
    retained = [inc for inc in original if not (sdk and inc.startswith('game/'))]
    clean = INCLUDES.sub('', source)
    seed = ''.join(f'#include "{inc}"\n' for inc in retained) + clean
    provided = set(project_headers._included_declarations(repo, seed))
    known = set()
    for inc in retained:
        known.update(buildtypes.type_names(repo, 'include/' + inc))
    used = set(re.findall(r'\b([A-Za-z_]\w*)\s*\*', clean))
    used |= set(re.findall(r'\b(?:g[A-Z]\w*|__\w+)\b', clean))
    functions = [function, *project_headers.called_functions(asm)]
    used.update(functions)
    headers = sorted((repo / 'include').rglob('*.h'))
    if sdk:
        headers = [h for h in headers if not h.relative_to(repo/'include').as_posix().startswith('game/')]
    additions = []
    for name in sorted(used - provided - known):
        if name in functions:
            choices = [d.include for d in project_headers.declarations(repo, name)
                       if not sdk or not d.include.startswith('game/')]
        else:
            choices = []
            for h in headers:
                text = h.read_text(errors='replace')
                if name not in text:
                    continue
                declared = any(project_headers._declared_name(d) == name
                               for _, _, d in project_headers._top_level_declarations(text))
                alias = re.search(r'(?m)^\s*typedef\s+(?:struct\s+|union\s+)?[A-Za-z_]\w*\s+'+re.escape(name)+r'\s*;',project_headers._mask_noncode(text))
                if declared or alias or project_headers._typedef_definition(text, name):
                    choices.append(h.relative_to(repo/'include').as_posix())
        if choices:
            choice = min(choices, key=lambda h: (h not in retained,
                not (name.startswith('__') and h.startswith('PRinternal/')), h))
            if choice not in retained:
                retained.append(choice)
                additions.append({'identifier': name, 'header': choice})
    changed = ''.join(f'#include "{inc}"\n' for inc in retained) + clean
    changed, removed = project_headers.reconcile_declarations(repo, changed)
    return changed, {'stage': 'header-context-recovery', 'sdk_namespace': sdk,
        'removed_headers': [i for i in original if i not in retained], 'added': additions,
        'reconciled': removed, 'authority': 'header-assisted candidate, compiler adjudicates'}


def globals_variant(conn, repo, function, source, diagnostics):
    wanted = set(re.findall(r"undeclared identifier '([A-Za-z_]\w*)'", diagnostics))
    unknown_extern = re.compile(r'(?m)^[ \t]*extern\s+(?:\?|M2C_UNK)\s+([A-Za-z_]\w*)\s*;[^\n]*(?:\n|$)')
    wanted.update(unknown_extern.findall(source))
    provided = set(project_headers._included_declarations(repo, source))
    stripped = unknown_extern.sub('', source)
    plans = globaldecl.plan(conn, stripped, function, buildtypes.type_names(repo) | provided,
                            unknowns.symbol_table(repo))
    plans = [p for p in plans if p['name'] in wanted and p['name'] not in provided]
    accepted = {p['name'] for p in plans}
    # Unknown declarations with no evidence must remain visibly unresolved.
    stripped = unknown_extern.sub(lambda m: '' if m.group(1) in accepted else m.group(), source)
    result = globaldecl.apply(stripped, plans)
    # Width mining can lose an indexed global's identity. A closed byte-address
    # view needs only symbol identity, not an invented scalar width or bound.
    from solver import address_units, workspace
    ws = repo/'nonmatchings'/function
    address_report = {'changes': []}
    if (ws/'target.s').is_file():
        address_symbols, identity_report = unknowns.address_symbol_evidence(repo, wanted)
        address_report = address_units.address_only_globals(result,function,
            workspace.target_asm(ws,function),address_symbols,provided)
        address_report['identity_evidence'] = identity_report
        result = address_report['source']
        from solver import call_address_globals
        call_addresses=call_address_globals.propose(result,function,workspace.target_asm(ws,function),address_symbols)
        call_addresses['identity_evidence']=identity_report
        address_report['call_addresses']=call_addresses
        result=call_addresses['source']
    address_names = {p['global'] for p in address_report['changes']}
    address_names.update(p['global'] for p in address_report.get('call_addresses',{}).get('changes',[]))
    return result, {'stage': 'binary-global-declarations',
        'plans': plans, 'address_only_views': address_report,
        'unresolved': sorted(wanted - accepted - provided - address_names),
        'authority': 'candidate types from observed accesses; not proven field types or extents'}


def project_signed_word_parameters(source, function, expected, *, o32=False):
    """Keep exact public int spelling and generated s32 working locals."""
    from solver import repair_context, type_transaction
    if not o32:return source
    match,end=repair_context.definition(source,function)
    actual=type_transaction.signature(match[0],function)
    if not actual or actual[0]!=expected[0] or len(actual[1])!=len(expected[1]):return source
    parts=match[2].split(',');locals_=[]
    for i,(got,want) in enumerate(zip(actual[1],expected[1])):
        if got==want:continue
        if got!=('s32',) or want!=('int',):return source
        parameter=re.fullmatch(r'\s*s32\s+(\w+)\s*',parts[i])
        if not parameter:return source
        name=parameter[1];fresh=name+'_public_word'
        if re.search(r'\b'+fresh+r'\b',source):return source
        parts[i]='int '+fresh
        locals_.append('    s32 '+name+' = '+fresh+';')
    if not locals_:return source
    source=source[:match.end()]+'\n'+'\n'.join(locals_)+source[match.end():]
    return source[:match.start(2)]+', '.join(parts)+source[match.end(2):]


def project_header_tag_parameters(source,function,expected,header_types):
    """Restore exact public tag spelling using an included typedef definition."""
    from solver import repair_context,type_transaction,m2c_byte_view
    definition,_=repair_context.definition(source,function)
    actual=type_transaction.signature(definition[0],function)
    if not actual or actual[0]!=expected[0] or len(actual[1])!=len(expected[1]):return source
    parts=m2c_byte_view.arguments(definition[2]);changed=False
    for i,(old,new) in enumerate(zip(actual[1],expected[1])):
        if old==new:continue
        if len(old)!=2 or old[1]!='*' or len(new)!=3 or new[0] not in {'struct','union'} or new[2]!='*':return source
        bodies={row['definition'] for row in header_types if row['type']==old[0]}
        if len(bodies)!=1:return source
        text=project_headers._mask_noncode(next(iter(bodies))).strip()
        if not re.match(r'typedef\s+'+new[0]+r'\s+'+new[1]+r'\s*\{',text):return source
        if not re.search(r'\}\s*'+old[0]+r'\s*;\s*$',text):return source
        if not re.fullmatch(r'\s*'+old[0]+r'\s*\*\s*\w+\s*',parts[i]):return source
        parts[i]=re.sub(r'\b'+old[0]+r'\b',new[0]+' '+new[1],parts[i],count=1);changed=True
    if not changed:return source
    return source[:definition.start(2)]+', '.join(parts)+source[definition.end(2):]


def header_tag_definitions(repo,source,names):
    """Exact alias lookup, independent of the bounded model-facing type packet."""
    paths=set()
    for inc in INCLUDES.findall(source):paths.update(buildtypes.closure(repo,'include/'+inc))
    rows=[]
    for path in sorted(paths):
        text=path.read_text(errors='replace')
        for name in names:
            definition=project_headers._typedef_definition(text,name)
            if definition:rows.append({'type':name,'definition':definition})
    return rows


def byteview_redraft(repo, ws, function, source, *, header_first=False):
    """Fresh assembly-only reconstruction gated by the included public ABI."""
    from solver import m2c_byte_view, repair_context, type_transaction
    abi=type_transaction.contract(repo,source,function)
    if abi['status']!='locked':
        raise ValueError('byte-view redraft requires an unambiguous header ABI')
    from solver import frontend_repair
    projections=[]
    def project(result):
        import hashlib
        original=before=result.stdout
        current,_=repair_context.definition(before,function)
        if type_transaction.signature(current[0],function)!=abi['shape']:
            shape=type_transaction.signature(current[0],function)
            names={p[0] for p in shape[1] if len(p)==2 and p[1]=='*'} if shape else set()
            before=project_header_tag_parameters(before,function,abi['shape'],header_tag_definitions(repo,source,names))
        result.stdout=project_signed_word_parameters(before,function,abi['shape'],o32=frontend_repair.big_endian_o32(ws/'target.o'))
        if original!=result.stdout:
            projections.append({'before_sha256':hashlib.sha256(original.encode()).hexdigest(),
                'after_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
                'kind':'public-header-tag-spelling' if original!=before else 'public-int-with-s32-working-local'})
    initial_context = {'context_headers': tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))} if header_first else {}
    result,meta=m2c_input.draft(repo,ws/'target.s',valid_syntax=True,**initial_context)
    if result.returncode:
        raise ValueError('byte-view m2c failed: '+result.stderr[-1000:])
    project(result)
    match,_=repair_context.definition(result.stdout,function)
    prior=None
    lowered=None
    shape=type_transaction.signature(match[0],function)
    reason=None
    if shape!=abi['shape']:
        reason='assembly-only draft differs from header ABI'
    else:
        try:
            lowered=m2c_byte_view.lower(result.stdout,function,known_types=abi.get('known_header_types',()),target_assembly=(ws/'target.s').read_text() if 'M2C_UNALIGNED32' in result.stdout or re.search(r'\b(?!M2C_UNK\b)\w+\s*\**\s*\(\*\*\)',result.stdout) else '')
        except ValueError as exc:
            reason='assembly-only byte lowering unavailable: '+str(exc)
    if reason is not None:
        lowered=None
        prior={'draft':meta,'shape':type_transaction.signature(match[0],function),
            'reason':reason}
        headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
        result,meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers,valid_syntax=True)
        if result.returncode:
            raise ValueError('header-aware byte-view m2c failed: '+result.stderr[-1000:])
        project(result)
        match,_=repair_context.definition(result.stdout,function)
    if type_transaction.signature(match[0],function)!=abi['shape']:
        raise ValueError('byte-view redraft changes public return/parameter types: '
            f"expected {abi['shape']!r}; observed {type_transaction.signature(match[0],function)!r}")
    from solver import callee_prototype_repair
    callee_report=callee_prototype_repair.hypotheses(repo,result.stdout,function)
    declarations=''
    prototypes=()
    if callee_report['prototypes']:
        prototypes=tuple((p['return_type'],p['name'],tuple(p['parameters'])) for p in callee_report['prototypes'])
        headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
        revised,revised_meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers,
            function_prototypes=prototypes,valid_syntax=True)
        if revised.returncode:
            raise ValueError('callee-interface redraft failed: '+revised.stderr[-1000:])
        project(revised)
        revised_definition,_=repair_context.definition(revised.stdout,function)
        if type_transaction.signature(revised_definition[0],function)!=abi['shape']:
            raise ValueError('callee-interface redraft changes public return/parameter types')
        result,meta=revised,revised_meta
        lowered=None
        declarations=''.join(f'extern {ret} {name}({", ".join(params) or "void"});\n' for ret,name,params in prototypes)
    from solver import m2c_context
    indexed_plans=m2c_context.indexed_externs(result.stdout)[:4]
    if indexed_plans:
        array_declarations=''.join(p['declaration']+'\n' for p in indexed_plans)
        headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
        revised,revised_meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers,
            function_prototypes=prototypes,extra_declarations=array_declarations,valid_syntax=True)
        if revised.returncode:
            raise ValueError('indexed-declaration redraft failed: '+revised.stderr[-1000:])
        project(revised)
        revised_definition,_=repair_context.definition(revised.stdout,function)
        if type_transaction.signature(revised_definition[0],function)!=abi['shape']:
            raise ValueError('indexed-declaration redraft changes public return/parameter types')
        result,meta=revised,revised_meta
        declarations+=array_declarations
        lowered=None
    if lowered is None:
        from solver import m2c_copy
        if 'M2C_UNALIGNED32' in result.stdout:
            result.stdout,storage_report=m2c_copy.destination_storage(result.stdout,function,(ws/'target.s').read_text())
            meta={**meta,'copy_destination_storage':storage_report}
            result.stdout,cursor_report=m2c_copy.source_cursor(result.stdout,function,(ws/'target.s').read_text())
            meta={**meta,'copy_source_cursor':cursor_report}
            result.stdout,feed_report=m2c_copy.feeding_cursor(result.stdout,function,(ws/'target.s').read_text(),
                [row['cursor'] for row in cursor_report['changes']])
            meta={**meta,'copy_feeding_cursor':feed_report}
            result.stdout,index_report=m2c_copy.indexed_stack_read(result.stdout,function,(ws/'target.s').read_text(),storage_report['changes'])
            meta={**meta,'copy_indexed_stack_read':index_report}
        lowered=m2c_byte_view.lower(result.stdout,function,known_types=abi.get('known_header_types',()),target_assembly=(ws/'target.s').read_text() if 'M2C_UNALIGNED32' in result.stdout or re.search(r'\b(?!M2C_UNK\b)\w+\s*\**\s*\(\*\*\)',result.stdout) else '')
    # Local/parameter byte fields need no global-pointer hypothesis. These are
    # independent reconstruction classes, not two halves of an admission gate.
    if not (lowered['fields'] or lowered.get('named_record_reads') or lowered.get('unaligned_stores')
            or callee_report.get('prototypes')):
        raise ValueError('no supported typed-byte reconstruction')
    headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
    candidate=''.join(f'#include "{inc}"\n' for inc in headers)+declarations+lowered['source']
    from solver import m2c_copy,callback_abi
    copy_layout=m2c_copy.unaligned_loop_layout((ws/'target.s').read_text()) if lowered.get('unaligned_reads') else None
    callback_arguments=callback_abi.argument_evidence((ws/'target.s').read_text()) if any(h.get('kind')=='callback-pointer-field' for h in lowered.get('hypotheses',[])) else None
    return candidate,{'stage':'assembly-byteview-redraft','draft':meta,'lowering':lowered,
        'header_first':header_first,
        'callback_argument_evidence':callback_arguments,
        'unaligned_copy_layout':copy_layout,
        'prior_abi_decline':prior,'callee_prototype_hypotheses':callee_report,
        'indexed_extern_hypotheses':indexed_plans,
        'public_abi':abi,'parameter_projections':projections,'status':'candidate','reference_bodies_used':False}


def byteview_redrafts(repo, ws, function, source):
    """Keep the first candidate; unresolved syntax permits one header alternative."""
    fresh, report = byteview_redraft(repo, ws, function, source)
    yield 'assembly-byteview-redraft', fresh, report
    unresolved = re.search(r'\b(?:M2C_UNK|M2C_UNALIGNED32|M2C_FIELD)\b',
                           project_headers._mask_noncode(fresh))
    if unresolved and not report.get('draft', {}).get('headers') and INCLUDES.findall(source):
        alternative, evidence = byteview_redraft(repo, ws, function, source, header_first=True)
        evidence['alternative_trigger'] = 'lowerable assembly draft retains unresolved syntax'
        if alternative != fresh:
            yield 'header-byteview-alternative', alternative, evidence


def variants(conn, repo, function, ws, source, attempt):
    rows, reports = [], []
    target = (attempt.compiler_recipe or {}).get('target', '')
    if not target:
        return [], [{'stage': 'compile-recovery', 'status': 'declined', 'reason': 'no configured TU identity'}]
    asm = (ws/'target.s').read_text()
    absolute = m2c_adapter.resolve_absolute_unknowns(repo, source)
    if absolute.source != source:
        rows.append(('absolute-symbols', absolute.source))
    reports.append({'stage': 'absolute-symbols',
        'resolved': list(absolute.resolved_absolute_symbols),
        'authority': 'explicit linker values, address-only draft uses; compiler adjudicates'})
    clean, report = header_variant(repo, function, asm, absolute.source, target)
    reports.append(report)
    from solver import hardware_environment
    registers=hardware_environment.register_views(clean,function,(ws/'target.s').read_text())
    if registers['changes']:
        reports.append({'stage':'encoded-register-views','reconstruction':registers})
        clean=registers['source']
    if clean != source:
        rows.append(('compatible-headers', clean))
    declared, report = globals_variant(conn, repo, function, clean,
        (attempt.frontend or {}).get('diagnostics', '') + attempt.compiler_stderr)
    reports.append(report)
    if declared != clean:
        rows.append(('binary-global-declarations', declared))
    from solver import local_call_interface
    local_declared, local_report = local_call_interface.propose(repo,declared,function,asm)
    reports.append({'stage':'local-call-interface', **local_report})
    if local_declared != declared:
        declared = local_declared
        rows.append(('local-call-interface',declared))
    opaque, report = compile_obligations.opaque_variant(repo, function, declared, asm)
    reports.append(report)
    if opaque != declared:
        rows.append(('opaque-parameter-layout', opaque))
        declared = opaque
    byte_view, report = compile_obligations.byte_pointer_variant(declared,function,asm)
    reports.append(report)
    if byte_view != declared:
        rows.append(('call-bound-byte-pointer-view',byte_view))
        declared = byte_view
    from solver import address_units
    address_report = address_units.propose(declared,function,asm,
        absolute_symbols=m2c_adapter.absolute_symbols(repo))
    reports.append({'stage': 'binary-address-units', **address_report})
    if address_report['changes']:
        declared = address_report['source']
        rows.append(('binary-address-units',declared))
    from solver import indexed_address_repair
    indexed = indexed_address_repair.recover(repo,ws,declared,function,asm,target)
    reports.append({'stage': 'measured-indexed-address', **indexed})
    if indexed['changes']:
        declared = indexed['source']
        rows.append(('measured-indexed-address',declared))
    if '->' in source and (repo/'.venv/bin/m2c').is_file():
        try:
            for label,fresh,report in byteview_redrafts(repo,ws,function,clean):
                rows.insert(0,(label,fresh))
                reports.append(report)
                if report.get('callback_argument_evidence'):
                    from solver import callback_abi,type_constraints
                    try:
                        measurement=type_constraints.measure(repo,ws,fresh,function,target)
                        callback_candidates=callback_abi.parameter_candidates(asm,measurement)
                        reports.append({'stage':'callback-parameter-layout-candidates',
                            'measurement_receipt':measurement['receipt_path'],
                            'source_sha256':measurement['source_sha256'],
                            **callback_candidates})
                        from solver import callback_repair
                        repaired,callback_report=callback_repair.propose(fresh,function,asm,callback_candidates)
                        reports.append({'stage':'callback-call-reconstruction',**callback_report})
                        if repaired!=fresh:
                            fresh=repaired
                            rows.insert(0,('measured-callback-call-reconstruction',fresh))
                    except (OSError,ValueError,subprocess.SubprocessError) as exc:
                        reports.append({'stage':'callback-parameter-layout-candidates','status':'declined','reason':str(exc)})
                fixed=m2c_adapter.resolve_absolute_unknowns(repo,fresh)
                recovered,global_report=globals_variant(conn,repo,function,fixed.source,'')
                addresses=address_units.propose(recovered,function,asm,
                    absolute_symbols=m2c_adapter.absolute_symbols(repo))
                recovered=addresses['source']
                from solver import local_call_interface
                recovered,local_interface=local_call_interface.propose(repo,recovered,function,asm)
                indexed=indexed_address_repair.recover(repo,ws,recovered,function,asm,target)
                recovered=indexed['source']
                from solver import record_word_copy
                record_copy=record_word_copy.recover(repo,ws,recovered,function,asm,target)
                recovered=record_copy['source']
                reports.append({'stage':'byteview-declaration-recovery','variant':label,
                    'resolved_absolute_symbols':list(fixed.resolved_absolute_symbols),
                    'globals':global_report,'address_units':addresses,'indexed_addresses':indexed,
                    'local_call_interface':local_interface,'record_word_copy':record_copy})
                if recovered!=fresh:
                    rows.insert(0,(label+'+declarations',recovered))
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            reports.append({'stage':'assembly-byteview-redraft','status':'declined','reason':str(exc)})
    # A fresh header-aware draft restores consistent word operations after
    # earlier local edits mixed partial fields with whole-field assignments.
    # The marker is a trigger, not proof of the correct C expression.
    if re.search(r'/\*\s*u64\+0x0\s*\*/',source) and (repo/'.venv/bin/m2c').is_file():
        from solver import type_constraints, wide_reconstruction
        try:
            headers=tuple(INCLUDES.findall(clean))
            draft,meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers)
            if draft.returncode:
                reports.append({'stage':'wide-operation-redraft','status':'declined','reason':draft.stderr[-1200:],**meta})
            else:
                measured=type_constraints.measure(repo,ws,clean,function,target)
                fresh=''.join(f'#include "{inc}"\n' for inc in headers)+draft.stdout
                lifted,wide=wide_reconstruction.reconstruct(fresh,measured['layouts'],function=function)
                reports.append({'stage':'wide-operation-redraft','draft':meta,'reconstruction':wide,
                    'layout_measurement':measured})
                if wide['changes']:
                    rows.insert(0,('measured-wide-operations',lifted))
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            reports.append({'stage':'wide-operation-redraft','status':'declined','reason':str(exc)})
    from tools.score_repo_function import rewrite_do_while
    for label, candidate in [('compile-context', declared), *list(rows)]:
        try:
            lowered = rewrite_do_while(candidate)
            if lowered != candidate:
                rows.append((label + '+do-while', lowered))
        except ValueError as exc:
            reports.append({'stage': 'do-while', 'status': 'declined', 'reason': str(exc)})
    # A compatible context can resolve stack objects and prototypes inside m2c.
    if clean != source and (repo/'.venv/bin/m2c').is_file():
        result, meta = m2c_input.draft(repo, ws/'target.s', context_headers=tuple(INCLUDES.findall(clean)))
        reports.append({'stage': 'compatible-header-redraft', **meta, 'returncode': result.returncode,
                        'diagnostics': result.stderr[-2000:]})
        if result.returncode == 0:
            redraft = ''.join(f'#include "{inc}"\n' for inc in INCLUDES.findall(clean)) + result.stdout
            redraft, _ = project_headers.reconcile_declarations(repo, redraft)
            rows.append(('compatible-header-redraft', redraft))
            try:
                lowered = rewrite_do_while(redraft)
                if lowered != redraft:
                    rows.append(('compatible-header-redraft+do-while', lowered))
            except ValueError as exc:
                reports.append({'stage': 'do-while', 'status': 'declined', 'reason': str(exc)})
    # Generate workspace hypotheses before ordinary array/loop normalization so
    # all resulting drafts pass through the same downstream recovery machinery.
    if re.search(r'(?m)^\s*(?:\?|M2C_UNK)\s+sp[0-9A-Fa-f]+\s*;',clean):
        from solver import stack_workspace_hints
        try:
            workspace_hints=stack_workspace_hints.recover(repo,ws,clean,function,target)
            candidates=workspace_hints.pop('candidates')
            reports.append({'stage':'stack-workspace-hints',**workspace_hints})
            for candidate in candidates:
                rows.insert(0,('stack-workspace-hints',rewrite_do_while(candidate)))
        except (ValueError, subprocess.TimeoutExpired) as exc:
            reports.append({'stage':'stack-workspace-hints','status':'declined','reason':str(exc)})
    # Stack-array dialect requires normalized loop syntax; retain originals but
    # prioritize reconstructed children before the existing candidate cap.
    from solver import stack_word_arrays
    reconstructed = []
    for label, candidate in rows:
        stack_words = stack_word_arrays.propose(candidate, function, asm)
        if stack_words['changes']:
            reports.append({'stage': 'stack-word-arrays', 'reconstruction': stack_words})
            reconstructed.append((label+'+stack-word-arrays', stack_words['source']))
    rows = reconstructed + rows
    from solver import stack_interface_arrays
    interface_children = []
    for label, candidate in rows:
        if not re.search(r'\b(?:s16|u16|s32|u32)\s+sp[0-9A-Fa-f]+\s*;', candidate):
            continue
        interface_arrays = stack_interface_arrays.recover(repo, candidate, function, asm)
        if interface_arrays['changes']:
            reports.append({'stage': 'stack-interface-arrays', 'reconstruction': interface_arrays})
            interface_children.append((label+'+stack-interface-arrays', interface_arrays['source']))
    rows = interface_children + rows
    from solver import stack_read_buffers
    read_children = []
    for label, candidate in [('compile-context',declared),*rows]:
        read_buffer = stack_read_buffers.propose(candidate,function,asm)
        if read_buffer['changes']:
            reports.append({'stage':'stack-read-buffer','reconstruction':read_buffer})
            read_children.append((label+'+stack-read-buffer',read_buffer['source']))
    rows = read_children + rows
    register_children=[]
    for label,candidate in rows:
        registers=hardware_environment.register_views(candidate,function,(ws/'target.s').read_text())
        if registers['changes']:
            reports.append({'stage':'encoded-register-views','reconstruction':registers})
            register_children.append((label+'+register-views',registers['source']))
    rows=register_children+rows
    unique, seen = [], {source}
    for label, code in rows:
        if code not in seen:
            unique.append((label, code))
            seen.add(code)
    return unique[:8], reports
