"""Fuse an immediately tested split byte load; proposals still require the oracle.

The existing initialized-temporary rewrite cannot see `u8 t; ... t = *p;`
followed by an immediate zero test. IDO allocates the named load and an anonymous
load differently. This family preserves the load count and its sequencing and
only handles matching, explicitly declared byte types within one function.
"""
from __future__ import annotations

import re

from solver import code_shapes
from solver.principle_variants import Variant


def candidates(source: str, function: str, max_variants: int = 4) -> tuple[Variant, ...]:
    if max_variants <= 0:
        return ()
    masked = code_shapes._mask(source)
    if re.search(r'(?m)^\s*#\s*(?!include\b)\w+', masked):
        return ()
    region = code_shapes._body(source, function)
    if region is None:
        return ()
    masked, begin, end = region
    body = masked[begin:end]
    if re.search(r'\bvolatile\b', body):
        return ()
    # Leading declarations only: no nested declaration, initializer, array,
    # qualifier, comma declaration or shadowing is inferred by this parser.
    leading = re.match(r'\s*(?:(?:u8|s8|unsigned\s+char|signed\s+char)\s+\*?\s*[A-Za-z_]\w*\s*;\s*)+', body)
    if leading is None:
        return ()
    declarations = list(re.finditer(
        r'(?P<type>u8|s8|unsigned\s+char|signed\s+char)\s+(?P<star>\*)?\s*(?P<name>[A-Za-z_]\w*)\s*;',
        body[:leading.end()]))
    out = []
    for decl in declarations:
        if decl['star']:
            continue
        name = decl['name']
        token = r'\b' + re.escape(name) + r'\b'
        uses = list(re.finditer(token, body))
        if len(uses) != 3 or uses[0].start() != decl.start('name'):
            continue
        # Only whitespace separates assignment and branch. A zero test has no
        # other evaluated expression, so there is no intervening side effect.
        assignment = re.match(
            re.escape(name) + r'\s*=\s*\*\s*(?P<ptr>[A-Za-z_]\w*)\s*;\s*'
            r'(?P<branch>if)\s*\(\s*(?P<test>!\s*' + re.escape(name)
            + r'|!\s*\(\s*' + re.escape(name) + r'\s*!=\s*0\s*\)'
            + r'|' + re.escape(name) + r'\s*(?:==|!=)\s*0)\s*\)',
            body[uses[1].start():])
        if assignment is None:
            continue
        start = uses[1].start()
        if body[:start].rstrip()[-1:] not in {';', '{', '}'}:
            continue
        if not start <= uses[2].start() < start + assignment.end():
            continue
        ptr = assignment['ptr']
        pointer_decls = [d for d in declarations if d['name'] == ptr]
        if len(pointer_decls) != 1 or not pointer_decls[0]['star']:
            continue
        if ' '.join(pointer_decls[0]['type'].split()) != ' '.join(decl['type'].split()):
            continue
        # Decline another declaration of either identifier anywhere, including
        # nested scopes; explicit address-taking of the removed local is already
        # excluded by its exact three-token-use requirement.
        if re.search(r'\b[A-Za-z_]\w*\s+\**\s*'
                     + re.escape(ptr) + r'\s*[;=,\[]', body[leading.end():]):
            continue
        branch_start = start + assignment.start('branch')
        read = uses[2]
        changes = [(begin + decl.start(), begin + decl.end(), ''),
                   (begin + start, begin + branch_start, ''),
                   (begin + read.start(), begin + read.end(), '(*' + ptr + ')')]
        changed = source
        for first, last, replacement in sorted(changes, reverse=True):
            changed = changed[:first] + replacement + changed[last:]
        out.append(Variant('inline-split-byte-zero-test:' + name, changed))
        if len(out) >= max_variants:
            break
    return tuple(out)
