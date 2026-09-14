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
                if declared or project_headers._typedef_definition(text, name):
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
        address_report = address_units.address_only_globals(result,function,
            workspace.target_asm(ws,function),unknowns.symbol_table(repo),provided)
        result = address_report['source']
    address_names = {p['global'] for p in address_report['changes']}
    return result, {'stage': 'binary-global-declarations',
        'plans': plans, 'address_only_views': address_report,
        'unresolved': sorted(wanted - accepted - provided - address_names),
        'authority': 'candidate types from observed accesses; not proven field types or extents'}


def byteview_redraft(repo, ws, function, source):
    """Fresh assembly-only reconstruction gated by the included public ABI."""
    from solver import m2c_byte_view, repair_context, type_transaction
    abi=type_transaction.contract(repo,source,function)
    if abi['status']!='locked':
        raise ValueError('byte-view redraft requires an unambiguous header ABI')
    result,meta=m2c_input.draft(repo,ws/'target.s',valid_syntax=True)
    if result.returncode:
        raise ValueError('byte-view m2c failed: '+result.stderr[-1000:])
    match,_=repair_context.definition(result.stdout,function)
    prior=None
    lowered=None
    shape=type_transaction.signature(match[0],function)
    reason=None
    if shape!=abi['shape']:
        reason='assembly-only draft differs from header ABI'
    else:
        try:
            lowered=m2c_byte_view.lower(result.stdout,function,known_types=abi.get('known_header_types',()))
        except ValueError as exc:
            reason='assembly-only byte lowering unavailable: '+str(exc)
    if reason is not None:
        prior={'draft':meta,'shape':type_transaction.signature(match[0],function),
            'reason':reason}
        headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
        result,meta=m2c_input.draft(repo,ws/'target.s',context_headers=headers,valid_syntax=True)
        if result.returncode:
            raise ValueError('header-aware byte-view m2c failed: '+result.stderr[-1000:])
        match,_=repair_context.definition(result.stdout,function)
    if type_transaction.signature(match[0],function)!=abi['shape']:
        raise ValueError('byte-view redraft changes public return/parameter types')
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
        revised_definition,_=repair_context.definition(revised.stdout,function)
        if type_transaction.signature(revised_definition[0],function)!=abi['shape']:
            raise ValueError('indexed-declaration redraft changes public return/parameter types')
        result,meta=revised,revised_meta
        declarations+=array_declarations
        lowered=None
    if lowered is None:
        lowered=m2c_byte_view.lower(result.stdout,function,known_types=abi.get('known_header_types',()))
    # Local/parameter byte fields need no global-pointer hypothesis. These are
    # independent reconstruction classes, not two halves of an admission gate.
    if not lowered['fields']:
        raise ValueError('no supported typed-byte reconstruction')
    headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
    candidate=''.join(f'#include "{inc}"\n' for inc in headers)+declarations+lowered['source']
    return candidate,{'stage':'assembly-byteview-redraft','draft':meta,'lowering':lowered,
        'prior_abi_decline':prior,'callee_prototype_hypotheses':callee_report,
        'indexed_extern_hypotheses':indexed_plans,
        'public_abi':abi,'status':'candidate','reference_bodies_used':False}


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
    if clean != source:
        rows.append(('compatible-headers', clean))
    declared, report = globals_variant(conn, repo, function, clean,
        (attempt.frontend or {}).get('diagnostics', '') + attempt.compiler_stderr)
    reports.append(report)
    if declared != clean:
        rows.append(('binary-global-declarations', declared))
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
            fresh,report=byteview_redraft(repo,ws,function,clean)
            rows.insert(0,('assembly-byteview-redraft',fresh))
            reports.append(report)
            fixed=m2c_adapter.resolve_absolute_unknowns(repo,fresh)
            recovered,global_report=globals_variant(conn,repo,function,fixed.source,'')
            addresses=address_units.propose(recovered,function,asm,
                absolute_symbols=m2c_adapter.absolute_symbols(repo))
            recovered=addresses['source']
            indexed=indexed_address_repair.recover(repo,ws,recovered,function,asm,target)
            recovered=indexed['source']
            reports.append({'stage':'byteview-declaration-recovery',
                'resolved_absolute_symbols':list(fixed.resolved_absolute_symbols),
                'globals':global_report,'address_units':addresses,'indexed_addresses':indexed})
            if recovered!=fresh:
                rows.insert(0,('assembly-byteview-redraft+declarations',recovered))
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
    unique, seen = [], {source}
    for label, code in rows:
        if code not in seen:
            unique.append((label, code))
            seen.add(code)
    return unique[:8], reports
