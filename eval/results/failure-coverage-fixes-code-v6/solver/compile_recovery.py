"""Bounded compile-context recovery, reusing existing header/global adapters.

All declarations are candidate hypotheses. No reference C bodies or KB writes.
The caller compiles/logs every emitted variant and retains its actual parent.
"""
from pathlib import Path
import re
import subprocess

from solver import buildtypes, compile_obligations, globaldecl, m2c_adapter, m2c_input, project_headers, unknowns


INCLUDES = re.compile(r'(?m)^[ \t]*#\s*include\s*[<"]([^>"\n]+)[>"][^\n]*(?:\n|$)')


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
    unknown_extern = re.compile(r'(?m)^[ \t]*extern\s+\?\s+([A-Za-z_]\w*)\s*;[^\n]*(?:\n|$)')
    wanted.update(unknown_extern.findall(source))
    provided = set(project_headers._included_declarations(repo, source))
    stripped = unknown_extern.sub('', source)
    plans = globaldecl.plan(conn, stripped, function, buildtypes.type_names(repo) | provided,
                            unknowns.symbol_table(repo))
    plans = [p for p in plans if p['name'] in wanted and p['name'] not in provided]
    accepted = {p['name'] for p in plans}
    # Unknown declarations with no evidence must remain visibly unresolved.
    stripped = unknown_extern.sub(lambda m: '' if m.group(1) in accepted else m.group(), source)
    return globaldecl.apply(stripped, plans), {'stage': 'binary-global-declarations',
        'plans': plans, 'unresolved': sorted(wanted - accepted - provided),
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
    if type_transaction.signature(match[0],function)!=abi['shape']:
        raise ValueError('byte-view redraft changes public return/parameter types')
    lowered=m2c_byte_view.lower(result.stdout,function)
    if not lowered['fields'] or not lowered['hypotheses']:
        raise ValueError('no supported typed-byte reconstruction')
    headers=tuple(dict.fromkeys(['common.h',*INCLUDES.findall(source)]))
    candidate=''.join(f'#include "{inc}"\n' for inc in headers)+lowered['source']
    return candidate,{'stage':'assembly-byteview-redraft','draft':meta,'lowering':lowered,
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
    if '->' in source and (repo/'.venv/bin/m2c').is_file():
        try:
            fresh,report=byteview_redraft(repo,ws,function,clean)
            rows.insert(0,('assembly-byteview-redraft',fresh))
            reports.append(report)
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
