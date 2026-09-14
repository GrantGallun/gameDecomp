"""Bounded float division-chain storage alternatives, without reassociation.

These candidates retain both divisions and their order. They explore whether
the first quotient remains live in one local through the second division.
Every candidate still requires the compiler and differential oracle.
"""
import re
from solver import code_shapes
from solver.principle_variants import Variant


def counted_loop(source, function):
    """Recover a top-tested loop from our generated constant-counter scaffold.

    The nonempty, exactly divisible trip count proves the first check true.
    Retain the counter update's position so uses inside the body are unchanged.
    """
    pattern = re.compile(r'(?P<counter>\w+)\s*=\s*0;\s*\{\s*while\s*\(1\)\s*\{\s*\{\s*'
                         r'(?P=counter)\s*\+=\s*(?P<step>\d+);(?P<body>.*?)'
                         r'if\s*\(!\((?P=counter)\s*!=\s*(?P<bound>\d+)\)\)\s*break;\s*\}\s*\}\s*\}', re.S)
    region = code_shapes._body(source, function)
    if not region:
        return None
    masked, start, end = region
    match = pattern.search(masked, start, end)
    if not match:
        return None
    counter, step, bound = match['counter'], int(match['step']), int(match['bound'])
    body = match['body']
    # The lazy regex may span nested blocks; demand the scaffold's matched
    # braces are balanced instead of consuming a neighbouring loop/function.
    scaffold = masked[match.start():match.end()]
    depth = 0
    for char in scaffold:
        depth += (char == '{') - (char == '}')
        if depth < 0:
            return None
    if depth:
        return None
    if (step <= 0 or bound <= 0 or bound >= 2**31 or bound % step or
            re.search(r'\b(?:break|continue|goto|return)\b', body) or
            re.search(r'\b' + re.escape(counter) + r'\b', body)):
        return None
    emitted_body = source[match.start('body'):match.end('body')]
    loop = f'for ({counter} = 0; {counter} != {bound};) {{ {counter} += {step};' + emitted_body + '}'
    return source[:match.start()] + loop + source[match.end():]


def candidates(source, function, maximum=32):
    if maximum <= 0 or re.search(r'\b(?:volatile|asm|goto)\b', source):
        return []
    # Recognize the explicit, nested staging form emitted by our own operator.
    pattern = re.compile(
        r'\{\s*f32\s+(\w+);\s*\1\s*=\s*\(f32\)\s*(\w+);\s*'
        r'\{\s*f32\s+(\w+);\s*\3\s*=\s*\(\(f32\)\s*(\w+)\s*/\s*'
        r'([0-9.]+f)\);\s*return\s*\(s32\)\s*\(\1\s*\*\s*'
        r'\(\3\s*/\s*\5\)\);\s*\}\s*\}')
    region = code_shapes._body(source, function)
    if not region:
        return []
    masked, start, end = region
    match = pattern.search(masked, start, end)
    if not match:
        return []
    value, arg, stage, seed, divisor = match.groups()
    if len({value, arg, stage, seed}) != 4:
        return []
    variants = []
    for declaration_order in (0, 1):
        declarations = f'f32 {stage}; f32 {value};' if declaration_order == 0 else f'f32 {value}; f32 {stage};'
        for value_position in range(3):
            for compound in (True, False):
                for reverse_product in (False, True):
                    statements = [f'{stage} = (f32) {seed} / {divisor};',
                                  f'{stage} /= {divisor};' if compound else f'{stage} = {stage} / {divisor};']
                    statements.insert(value_position, f'{value} = (f32) {arg};')
                    product = f'{stage} * {value}' if reverse_product else f'{value} * {stage}'
                    tail = '{ ' + declarations + ' ' + ' '.join(statements) + f' return (s32) ({product});' + ' }'
                    variants.append(Variant(f'float-chain-reuse:{declaration_order}:{value_position}:{int(compound)}:{int(reverse_product)}',
                        source[:match.start()] + tail + source[match.end():]))
    # A paired candidate crosses the neutral source-shape plateau without
    # requiring another whole frontier expansion.
    result = []
    for variant in variants:
        result.append(variant)
        recovered = counted_loop(variant.source, function)
        if recovered:
            result.append(Variant(variant.label + '+counted-loop', recovered))
    return result[:maximum]
