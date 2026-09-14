"""Conservative build-context projection and compiler-dialect repairs.

Reference C is inspected only for includes and a known, balanced diagnostic
decoration around the named definition. No implementation statement is returned.
Every source variant is separately compiled and logged by its caller.
"""
import hashlib
import re
from pathlib import Path

from solver import c89, project_headers, type_transaction


def definition(source, function):
    masked = project_headers._mask_noncode(source)
    prefix = r'(?m)^[ \t]*([\w *]+)\b'+re.escape(function)+r'\s*\('
    matches = []
    for opening in re.finditer(prefix, masked):
        depth, cursor = 1, opening.end()
        while cursor < len(masked) and depth:
            if masked[cursor] in ';{}':
                break
            depth += (masked[cursor] == '(') - (masked[cursor] == ')')
            cursor += 1
        if depth:
            continue
        # Preserve the Match API and group spans used by existing ABI edits.
        # Balanced scanning handles nested callback declarators without
        # interpreting them as types or accepting K&R declaration tails.
        length = cursor-1-opening.end()
        pattern = prefix+r'([\s\S]{'+str(length)+r'})\)\s*\{'
        match = re.compile(pattern).match(masked, opening.start())
        if match:
            matches.append(match)
    if len(matches) != 1:
        raise ValueError('requires one ordinary function definition')
    match = matches[0]
    depth, end = 1, match.end()
    while end < len(masked) and depth:
        depth += (masked[end] == '{') - (masked[end] == '}')
        end += 1
    if depth:
        raise ValueError('unbalanced function body')
    return match, end


def project(repo, function, source, target):
    report = {'kind':'source-context-projection', 'reference_body_supplied':False,
              'authority':'existing project header/build metadata; not binary-only'}
    if not re.fullmatch(r'build/src/[\w./-]+\.o', target) or '..' in Path(target).parts:
        return source, {**report, 'status':'unavailable', 'reason':'no safe TU identity'}
    path = repo / (target[6:-2]+'.c')
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to((repo/'src').resolve()):
        return source, {**report,'status':'unavailable','reason':'C TU metadata unavailable'}
    raw = path.read_text(errors='replace')
    report.update(path=str(path.relative_to(repo)), sha256=hashlib.sha256(raw.encode()).hexdigest())
    try:
        original, end = definition(raw, function)
        current, current_end = definition(source, function)
    except ValueError as exc:
        return source, {**report,'status':'declined','reason':str(exc)}
    # Only direct include names in a top-level, unconditional prefix. No
    # condition evaluation, source-body import, or arbitrary pragma copying.
    prefix = project_headers._mask_comments(raw[:original.start()])
    headers, nesting = [], 0
    for line in prefix.splitlines():
        if re.match(r'\s*#\s*(if|ifdef|ifndef)\b',line): nesting += 1
        elif re.match(r'\s*#\s*endif\b',line): nesting -= 1
        m = re.fullmatch(r'\s*#\s*include\s*[<"]([\w./-]+)[>"]\s*',line)
        if m and nesting == 0 and '..' not in Path(m[1]).parts:
            if (repo/'include'/m[1]).is_file(): headers.append(m[1])
    if nesting != 0:
        return source, {**report,'status':'declined','reason':'conditional definition context'}
    supplied = re.findall(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]',source)
    additions = [h for h in dict.fromkeys(headers) if h not in supplied]
    # Restore ONLY the exact known scoped policy when both ends are present.
    before = re.sub(r'/\*.*?\*/|//[^\n]*','',raw[:original.start()],flags=re.S)
    after = raw[end:]
    scope = bool(re.search(r'CLANG_DIAGNOSTIC_PUSH\s+CLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE\s*$',before)
                 and re.match(r'\s*CLANG_DIAGNOSTIC_POP\b',after))
    if scope and 'CLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE' not in source:
        header = repo/'include/compiler_diagnostics.h'
        if not header.is_file() or 'clang diagnostic ignored \\"-Wreturn-type\\"' not in header.read_text():
            return source, {**report,'status':'declined','reason':'unrecognized diagnostic macro definition'}
        source = (source[:current.start()]+'CLANG_DIAGNOSTIC_PUSH\nCLANG_DIAGNOSTIC_IGNORE_RETURN_TYPE\n'
                  +source[current.start():current_end]+'\nCLANG_DIAGNOSTIC_POP'+source[current_end:])
        if 'compiler_diagnostics.h' not in supplied+additions: additions.append('compiler_diagnostics.h')
    source = ''.join(f'#include "{h}"\n' for h in additions)+source
    abi = type_transaction.contract(repo,source,function)
    if abi['status'] == 'locked':
        current, _ = definition(source,function)
        shape = type_transaction.signature(source[current.start():current.end()],function)
        if shape and shape[1] == abi['shape'][1] and shape[0] != abi['shape'][0]:
            # Header return spelling only; never rewrite parameter ABI here.
            source = source[:current.start(1)]+' '.join(abi['shape'][0])+' '+source[current.end(1):]
    return source, {**report,'status':'projected','added_headers':additions,'legacy_return_scope':scope}


def normalize(source, stderr, function=None):
    rows = []
    if function and ('Unacceptable operand' in stderr or 'Bad operand type for' in stderr):
        match,end=definition(source,function)
        masked=project_headers._mask_noncode(source)
        body=masked[match.end():end-1]
        # Explicit m2c pointer loads retain void* arithmetic. Convert its
        # units to bytes, then preserve the void* result for typed call sites.
        loaded = re.compile(r'(\(\*\(void\s*\*\*\)\(\(u8\s*\*\)\(\w+\)\s*\+\s*(?:0x[0-9A-Fa-f]+|[0-9]+)\)\))\s*([+-])\s*(\(\w+\s*<<\s*[0-9]+\)|\w+)(?=\s*[,;)])')
        edits=[]
        for expression in loaded.finditer(body):
            pointer=re.sub(r'void\s*\*\*', 'unsigned char **', expression[1])
            edits.append((match.end()+expression.start(),match.end()+expression.end(),
                          f'(void *)({pointer} {expression[2]} {expression[3]})'))
        candidate=source
        for start,stop,text in reversed(edits):
            candidate=candidate[:start]+text+candidate[stop:]
        if edits and len(edits)<=128:
            rows.append(('void-loaded-pointer-byte-arithmetic',candidate))
        names=re.findall(r'(?m)^[ \t]*void\s*\*\s*(\w+)\s*;',body)
        edits=[]
        for name in sorted(set(names)):
            # Only uniquely declared ordinary locals, not parameters or members.
            declarations=re.findall(r'(?m)^[ \t]*[A-Za-z_]\w*(?:[ \t]+[A-Za-z_]\w*)*[ \t]+\**\s*'+re.escape(name)+r'\s*[;=]',body)
            if names.count(name)!=1 or len(declarations)!=1 or re.search(r'\b'+re.escape(name)+r'\b',match[2]):
                continue
            for use in re.finditer(r'\b'+re.escape(name)+r'\b(?=\s*[+-](?![+=\->]))',body):
                if re.search(r'(?:\.|->)\s*$',body[:use.start()]):
                    continue
                edits.append((match.end()+use.start(),match.end()+use.end(),
                              '((unsigned char *)'+name+')'))
        if edits and len(edits)<=128:
            candidate=source
            for start,stop,text in sorted(edits,reverse=True):
                candidate=candidate[:start]+text+candidate[stop:]
            # GNU void-pointer arithmetic returned void*, allowing assignment
            # to another object pointer. Preserve that conversion for ordinary
            # local pointer destinations; byte dereferences still need u8 units.
            pointer_names=re.findall(r'(?m)^[ \t]*(?:struct\s+)?\w+\s*\*\s*(\w+)\s*;',body)
            roots='|'.join(re.escape(n) for n in sorted(set(names)))
            assignment=re.compile(r'(?m)^([ \t]*)(\w+)([ \t]*=[ \t]*)(\(\(unsigned char \*\)(?:'+roots+r')\)\s*[+-][^;\n]*);')
            def preserve_pointer_result(m):
                if pointer_names.count(m[2])!=1:
                    return m[0]
                return m[1]+m[2]+m[3]+'(void *)('+m[4]+');'
            candidate=assignment.sub(preserve_pointer_result,candidate)
            rows.append(('void-local-byte-arithmetic',candidate))
    if function and ('Unacceptable operand' in stderr or 'Bad operand type for' in stderr):
        match,end=definition(source,function)
        mask=project_headers._mask_noncode(source)
        body=mask[match.end():end-1]
        parameters=[]
        for part in match[2].split(','):
            parameter=re.fullmatch(r'\s*void\s*\*\s*(\w+)\s*',part)
            if parameter: parameters.append(parameter[1])
        assignments=[]
        for expression in re.finditer(r'(?m)^[ \t]*(\w+)\s*=\s*((\w+)\s*([+-])\s*(0x[0-9A-Fa-f]+|[0-9]+))\s*;',body):
            dest,_,base,op,literal=expression.groups()
            locals_=re.findall(r'(?m)^[ \t]*(?:struct\s+)?\w+\s*\*\s*'+re.escape(dest)+r'\s*;',body)
            if (parameters.count(base)!=1 or len(locals_)!=1 or int(literal,0)>65536 or
                    re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+re.escape(base)+r'\s*[;=]',body) or
                    re.search(r'\b'+re.escape(dest)+r'\b',match[2])):continue
            assignments.append((match.end()+expression.start(2),match.end()+expression.end(2),
                f'(void *)((unsigned char *){base} {op} {literal})'))
        normalized=source
        for start,stop,text in reversed(assignments):
            normalized=normalized[:start]+text+normalized[stop:]
        if assignments and len(assignments)<=128:
            rows.append(('void-parameter-byte-assignment',normalized))
        edits=[]
        for expression in re.finditer(r'\(void\s*\*\)\s*\(\s*(\w+)\s*([+-])\s*(0x[0-9A-Fa-f]+|[0-9]+)\s*\)',body):
            name,op,literal=expression.groups()
            if parameters.count(name)!=1 or re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+name+r'\s*[;=]',body):
                continue
            edits.append((match.end()+expression.start(),match.end()+expression.end(),
                f'(void *)((unsigned char *){name} {op} {literal})'))
        normalized=source
        for start,stop,text in sorted(edits,reverse=True):
            normalized=normalized[:start]+text+normalized[stop:]
        if edits: rows.append(('void-pointer-byte-expression',normalized))
        # A plain void-pointer offset passed as a call argument needs byte
        # arithmetic too, even when m2c omitted the outer (void *) cast.
        edits=[]
        for expression in re.finditer(r'\b(\w+)\s*([+-])\s*(0x[0-9A-Fa-f]+|[0-9]+)\b(?=\s*[,)])',body):
            name,op,literal=expression.groups()
            if parameters.count(name)!=1 or re.search(r'(?m)^[ \t]*\w+\s+\**\s*'+name+r'\s*[;=]',body):
                continue
            prefix=body[:expression.start()]
            if not re.search(r'(?:\b\w+\s*\(|,)\s*$',prefix):
                continue
            edits.append((match.end()+expression.start(),match.end()+expression.end(),
                f'(void *)((unsigned char *){name} {op} {literal})'))
        normalized=source
        for start,stop,text in sorted(edits,reverse=True):
            normalized=normalized[:start]+text+normalized[stop:]
        if edits: rows.append(('void-pointer-call-byte-offset',normalized))
    if function and 'Bad operand type for' in stderr:
        match,end=definition(source,function)
        mask=project_headers._mask_noncode(source)
        body=mask[match.end():end-1]
        locals_=re.findall(r'(?m)^[ \t]*void\s*\*\s*(\w+)\s*;',body)
        edits=[]
        for step in re.finditer(r'(?m)^[ \t]*(\w+)\s*([+-])=\s*(0x[0-9A-Fa-f]+|[0-9]+)\s*;',body):
            name,op,literal=step.groups()
            if locals_.count(name)!=1:
                continue
            replacement=f'{name} = (void *)((unsigned char *){name} {op} {literal});'
            edits.append((match.end()+step.start(),match.end()+step.end(),replacement))
        normalized=source
        for start,stop,text in sorted(edits,reverse=True):
            normalized=normalized[:start]+text+normalized[stop:]
        if edits:
            rows.append(('void-pointer-byte-step',normalized))
    if function and '(bitwise' in c89._mask(source):
        from solver import m2c_context
        normalized, plans = m2c_context.lower_bitcasts(source, function)
        if plans:
            rows.append(('bitcast-union', normalized))
    if re.search(r'syntax|declaration|C89', stderr, re.I):
        normalized = c89.to_c89(source)
        if normalized != source: rows.append(('c89', normalized))
    if 'contains a do-while loop' in stderr:
        from tools.score_repo_function import rewrite_do_while
        try:
            normalized = rewrite_do_while(source)
            if normalized != source: rows.append(('do-while',normalized))
        except ValueError:
            pass
    return rows


def obligations(source, stderr):
    """Explain detected syntax constructs without supplying target-specific edits."""
    rows = []
    if re.search(r'syntax',stderr,re.I):
        for number,line in enumerate(source.splitlines(),1):
            m = c89.DECL_RE.match(line)
            if m and m['rest'] and re.search(r'\b'+re.escape(m['name'])+r'\s*;',source):
                rows.append({'line':number,'variable':m['name'], 'construct':line.strip(),
                    'hypothesis':'initialized declaration may follow statements; this compiler requires C89',
                    'constraint':'preserve the initializer and evaluation position; do not replace input-derived values with constants'})
    return rows[:16]
