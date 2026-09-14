"""Project-header-backed register references that lack an admitted environment.

This detects unresolved register-macro relocations, not every hardware access.
It neither assigns guessed addresses nor models device behavior.
"""
import hashlib
import re


class Required(ValueError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('hardware register environment required: '+', '.join(
            row['symbol'] for row in evidence['register_references']))


def obligations(repo, assembly):
    header = repo/'include/PR/rcp.h'
    if not header.is_file():
        return None
    # Only explicit relocation memory operands, not symbol-name resemblance,
    # unused header macros, immediate constants or function names.
    referenced = set(re.findall(
        r'(?m)^\s*(?:lb|lbu|lh|lhu|lw|lwl|lwr|ld|sb|sh|sw|swl|swr|sd)\s+'
        r'[^,\n]+,\s*%lo\((\w+)\)\s*\(', assembly))
    rows = []
    text = header.read_text(errors='replace')
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r'\s*#\s*define\s+(\w+_REG)\s+(.+)', line)
        if match and match[1] in referenced:
            rows.append({'symbol':match[1], 'line':number, 'macro':line.strip()})
    if not rows:
        return None
    return {'kind':'hardware-register-environment-required',
        'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'header':'include/PR/rcp.h', 'header_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),
        'register_references':rows,
        'authority':'project RCP register header plus target relocation memory operands',
        'scope':'unresolved register references; not a complete hardware-access classifier',
        'next_action':'provide an independently validated device/callee-effect environment; do not seed registers as RAM'}
