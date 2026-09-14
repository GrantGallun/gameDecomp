"""Bounded C89 branch-default and absolute-difference lifetime alternatives.

These source hypotheses require the normal compiler and semantic replay gates.
The branch recognizers deliberately accept only scalar-local assignments.
"""
import re
from solver import code_shapes, principle_variants


def candidates(source, function, maximum=24):
    region = code_shapes._body(source, function)
    if not region:
        return []
    _, start, end = region
    prefix = f'void {function}(void) {{'
    isolated = prefix + source[start:end] + '}'
    return [principle_variants.Variant(v.label, source[:start] + v.source[len(prefix):-1] + source[end:])
            for v in _candidates(isolated, function, maximum)]


def _rename(text, before, after):
    masked = code_shapes._mask(text)
    spans = [m.span() for m in re.finditer(r'\b' + re.escape(before) + r'\b', masked)
             if not masked[:m.start()].rstrip().endswith(('.', '->'))]
    for start, end in reversed(spans):
        text = text[:start] + after + text[end:]
    return text


def _candidates(source, function, maximum):
    region = code_shapes._body(source, function)
    if not region or maximum <= 0:
        return []
    masked, start, end = region
    if re.search(r'\b(?:volatile|goto|asm|for|while|do)\b', masked[start:end]):
        return []
    declarations = principle_variants._leading_declarations(source[start:end])[1]
    names = [d.name for d in declarations
             if d.type_text.strip() in ('int', 's32') and not d.stars]
    local = set(names)
    if len(local) != len(names) or any(re.search(r'&\s*\b'+re.escape(n)+r'\b', masked[start:end]) for n in local):
        return []
    # Nested scopes and escaping locals need a real scope/dataflow analysis.
    for decl in re.finditer(r'\b(?:int|s32)\s+\w+\s*;', masked[start:end]):
        before = masked[start:start+decl.start()]
        if before.count('{') != before.count('}'):
            return []
    def top_level(match):
        before=masked[start:match.start()]
        return before.count('{')==before.count('}') and masked[:match.start()].rstrip().endswith((';','{','}'))
    result = []
    def add(label, code):
        if code != source and all(v.source != code for v in result):
            result.append(principle_variants.Variant(label, code))
    pattern = re.compile(r'if\s*\(([^{};]+)\)\s*\{\s*(\w+)\s*=\s*([01])\s*;\s*(\w+)\s*=\s*(\w+)\s*-\s*(\w+)\s*;\s*\}\s*else\s*\{\s*\2\s*=\s*([01])\s*;\s*\4\s*=\s*\6\s*-\s*\5\s*;\s*\}')
    for match in pattern.finditer(masked, start, end):
        if not top_level(match):
            continue
        cond, flag, first, dest, left, right, second = match.groups()
        if not {flag, dest, left, right} <= local or first == second:
            continue
        if flag in (dest, left, right) or left == right or not re.fullmatch(r'\s*'+re.escape(right)+r'\s*(?:<=|<|>=|>)\s*'+re.escape(left)+r'\s*', cond):
            continue
        # The input may be overwritten only after its final use outside this
        # branch. Both mutually-exclusive arms compute from the original input.
        destinations = [dest] + [v for v in (left, right) if dest not in (left, right)
            if len(re.findall(r'\b'+re.escape(dest)+r'\b',masked[start:match.start()]))==1
            if not re.search(r'\b' + re.escape(v) + r'\b', masked[match.end():end])]
        for output in destinations:
            for hoist in (False, True):
                replacement = ((f'{flag} = {second};\n    ' if hoist else '') +
                    f'if ({cond}) {{\n        {flag} = {first};\n        {output} = {left} - {right};\n    }} else {{\n' +
                    ('' if hoist else f'        {flag} = {second};\n') +
                    f'        {output} = {right} - {left};\n    }}')
                tail = source[match.end():end]
                if output != dest:
                    tail = _rename(tail, dest, output)
                code = source[:match.start()] + replacement + tail + source[end:]
                if output != dest:
                    code = re.sub(r'(?m)^\s*(?:int|s32|u32)\s+' + re.escape(dest) + r'\s*;\n', '', code, count=1)
                add(f'absolute-difference-reuse:{output}:default-{hoist}', code)
    pair = re.compile(r'\b(\w+)\s*=\s*(\w+)\s*/\s*(0x[\da-fA-F]+|[1-9]\d*)\s*;\s*(\w+)\s*=\s*\2\s*%\s*\3\s*;')
    for match in pair.finditer(masked, start, end):
        if not top_level(match):
            continue
        quotient, value, divisor, remainder = match.groups()
        if len({quotient, value, remainder}) != 3 or not {quotient, value, remainder} <= local:
            continue
        if len(re.findall(r'\b'+re.escape(quotient)+r'\b',masked[start:match.start()]))!=1:
            continue
        if re.search(r'\b' + re.escape(value) + r'\b', masked[match.end():end]):
            continue
        replacement = f'{remainder} = {value} % {divisor};\n    {value} /= {divisor};'
        tail = _rename(source[match.end():end], quotient, value)
        code = source[:match.start()] + replacement + tail + source[end:]
        code = re.sub(r'(?m)^\s*(?:int|s32|u32)\s+' + re.escape(quotient) + r'\s*;\n', '', code, count=1)
        add(f'divmod-reuse:{quotient}-into-{value}', code)
    nested = re.compile(r'\b(\w+)\s*=\s*\(\s*(\w+)\s*/\s*(0x[\da-fA-F]+|[1-9]\d*)\s*\)\s*%\s*(0x[\da-fA-F]+|[1-9]\d*)\s*;')
    for match in nested.finditer(masked, start, end):
        if not top_level(match):
            continue
        dest, value, divisor, modulus = match.groups()
        if dest == value or not {dest, value} <= local or re.search(r'\b' + re.escape(value) + r'\b', masked[match.end():end]):
            continue
        replacement = f'{value} /= {divisor};\n    {dest} = {value} % {modulus};'
        add(f'nested-divmod-reuse:{value}', source[:match.start()] + replacement + source[match.end():])
    # Recognize a complete three-field quotient/remainder decomposition. Stores
    # retain their original order; only unobservable scalar locals are removed.
    chain = re.compile(r'(?P<q>\w+)\s*=\s*(?P<v>\w+)\s*/\s*(?P<d>0x[\da-fA-F]+|[1-9]\d*)\s*;\s*(?P<r>\w+)\s*=\s*(?P=v)\s*%\s*(?P=d)\s*;\s*(?P<p>\w+)->(?P<f>\w+)\s*=\s*\((?P<ft>s16|s8|int|s32)\)\s*(?P=r)\s*;\s*(?P<s>\w+)\s*=\s*(?P=q)\s*%\s*(?P<sd>[1-9]\d*)\s*;\s*(?P<m>\w+)\s*=\s*\((?P=q)\s*/\s*(?P=sd)\)\s*%\s*(?P<md>[1-9]\d*)\s*;\s*(?P=p)->(?P<sf>\w+)\s*=\s*\((?P<st>s8|s16|int|s32)\)\s*(?P=s)\s*;\s*(?P=p)->(?P<mf>\w+)\s*=\s*\((?P<mt>s8|s16|int|s32)\)\s*(?P=m)\s*;')
    for match in chain.finditer(masked,start,end):
        if not top_level(match):
            continue
        g=match.groupdict()
        eliminated=[g[k] for k in ('q','r','s','m')]
        if len(set(eliminated+[g['v']]))!=5 or not set(eliminated+[g['v']])<=local:
            continue
        if any(re.search(r'\b'+re.escape(n)+r'\b',masked[match.end():end]) for n in eliminated+[g['v']]):
            continue
        if any(len(re.findall(r'\b'+re.escape(n)+r'\b',masked[start:match.start()]))!=1 for n in eliminated):
            continue
        replacement=(f"{g['p']}->{g['f']} = ({g['ft']})({g['v']} % {g['d']});\n    {g['v']} /= {g['d']};\n    "
            f"{g['p']}->{g['sf']} = ({g['st']})({g['v']} % {g['sd']});\n    {g['v']} /= {g['sd']};\n    "
            f"{g['p']}->{g['mf']} = ({g['mt']})({g['v']} % {g['md']});")
        code=source[:match.start()]+replacement+source[match.end():]
        for name in eliminated:
            code=re.sub(r'(?m)^\s*(?:int|s32)\s+'+re.escape(name)+r'\s*;\n','',code,count=1)
        add('direct-field-divmod-chain:'+g['v'],code)
    return result[:maximum]
