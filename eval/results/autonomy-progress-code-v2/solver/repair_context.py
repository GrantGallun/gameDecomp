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
    matches = list(re.finditer(r'(?m)^[ \t]*([\w *]+)\b'+re.escape(function)+
                              r'\s*\(([^()]*)\)\s*\{', masked))
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
