"""Opt-in, passive m2c lifting observations and source-bound model guidance.

Hooks require an isolated single-threaded translation. They forward upstream
operations exactly once, never format an extra expression or unify a type.
Assembly associations and lexical C locations are not instruction ownership.
Only address-add fallthrough and late pointer-store views are observed here;
absence of observations does not establish that the rest of the C is sound.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Observer:
    def __init__(self, *, max_hazards=64, max_expressions=4):
        if any(type(v) is not int or v < 1 for v in (max_hazards, max_expressions)):
            raise ValueError('observation limits must be positive integers')
        self.max_hazards, self.max_expressions = max_hazards, max_expressions
        self.rows, self.by_identity, self.errors = [], {}, set()
        self.omitted = 0
        self.saved = None
        self.files = {}

    def _safe(self, operation, *args):
        try:
            operation(*args)
        except Exception as exc:
            self.errors.add(type(exc).__name__)

    def __enter__(self):
        from m2c import evaluate
        from m2c.translate import BinaryOp, StoreStmt
        if self.saved is not None or hasattr(evaluate.handle_add_real, '_uncertainty_observer'):
            raise RuntimeError('observer requires an isolated non-nested translation')
        self.saved = (evaluate.handle_add_real, evaluate.add_imm, BinaryOp.format, StoreStmt.format)
        add, imm, binary_format, store_format = self.saved

        def handle_add(lhs, rhs, args):
            result = add(lhs, rhs, args)
            self._safe(self.observe_add, result, args)
            return result

        def add_imm(output_reg, source, value, args):
            result = imm(output_reg, source, value, args)
            self._safe(self.observe_add, result, args)
            return result

        def format_binary(expression, fmt):
            result = binary_format(expression, fmt)
            self._safe(self._formatted, expression, result)
            return result

        def format_store(statement, fmt):
            # Upstream naturally resolves late fields here. Observe afterward;
            # resolving them early would alter type inference and the output.
            result = store_format(statement, fmt)
            self._safe(self.observe_store, statement, result)
            return result

        handle_add._uncertainty_observer = self
        evaluate.handle_add_real, evaluate.add_imm = handle_add, add_imm
        BinaryOp.format, StoreStmt.format = format_binary, format_store
        return self

    def __exit__(self, *exc):
        from m2c import evaluate
        from m2c.translate import BinaryOp, StoreStmt
        evaluate.handle_add_real, evaluate.add_imm, BinaryOp.format, StoreStmt.format = self.saved
        self.saved = None

    def _record(self, expression, row):
        key = id(expression)
        if key in self.by_identity:
            return
        if len(self.rows) >= self.max_hazards:
            self.omitted += 1
            return
        # Retain actual identities so Python cannot recycle them during a pass.
        self.by_identity[key] = (expression, row)
        row['c_expressions'] = []
        self.rows.append(row)

    def _instruction(self, instruction):
        meta = instruction.meta
        filename = meta.filename
        row = {'filename': filename, 'line': meta.lineno, 'synthetic': meta.synthetic,
               'mnemonic': instruction.mnemonic, 'instruction': str(instruction),
               'association': 'operation observed during lifting; not complete dataflow provenance'}
        if filename:
            if filename not in self.files and len(self.files) < 16:
                path = Path(filename)
                if path.is_file() and path.stat().st_size <= 2_000_000:
                    self.files[filename] = path.read_text()
            text = self.files.get(filename)
            if text is not None:
                row['assembly_sha256'] = sha(text)
                lines = text.splitlines()
                line = lines[meta.lineno - 1] if 0 < meta.lineno <= len(lines) else ''
                match = re.search(r'/\*\s*([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s*\*/', line)
                if match and not meta.synthetic:
                    row.update(file_offset=match[1], address=match[2], word=match[3],
                               byte_authority='disassembly annotation; independently check against target object')
        return row

    def observe_add(self, result, args):
        from m2c.translate import BinaryOp
        if not isinstance(result, BinaryOp) or result.op != '+':
            return
        left, right = result.left, result.right
        lp, rp = left.type.is_pointer_or_array(), right.type.is_pointer_or_array()
        if lp == rp:
            return
        base, offset = (left, right) if lp else (right, left)
        if offset.type.is_float() or not offset.type.is_reg():
            return
        target = base.type.get_pointer_target()
        size = target.get_size_bytes() if target is not None else None
        if size == 1:
            return
        self._record(result, {'kind': 'unrecovered-address-add',
            'instruction': self._instruction(args.instruction_ref.instruction),
            'target_size_hypothesis': size, 'machine_units': 'byte address addition',
            'uncertainty': 'upstream array/field recovery left a raw pointer addition; C may introduce pointee scaling',
            'type_authority': 'mutable m2c hypothesis; no original layout proved'})

    def _formatted(self, expression, text):
        entry = self.by_identity.get(id(expression))
        if entry is not None:
            self._text(entry[1], text)

    def _text(self, row, text):
        if len(text) > 1200:
            row['omitted_expression_text'] = True
            return
        if text not in row['c_expressions']:
            if len(row['c_expressions']) < self.max_expressions:
                row['c_expressions'].append(text)
            else:
                row['omitted_expression_text'] = True

    def observe_store(self, statement, text):
        from m2c.translate import BinaryOp, Cast, late_unwrap
        source, destination = statement.source, statement.dest
        if not source.type.is_pointer() or not destination.type.is_pointer():
            return
        underlying = late_unwrap(source.expr) if isinstance(source, Cast) else source
        address_cast = (isinstance(source, Cast) and isinstance(underlying, BinaryOp)
            and underlying.op == '+' and underlying.left.type.is_pointer_or_array()
            != underlying.right.type.is_pointer_or_array())
        if source.type.data() is destination.type.data() and not address_cast:
            return
        self._record(statement, {'kind': 'late-pointer-store-view', 'instruction': None,
            'uncertainty': 'late pointer assignment uses distinct provisional type identities or an address cast',
            'type_authority': 'mutable type identity; distinct identities alone do not prove incompatible C types',
            'machine_units': 'pointer bits; destination view unresolved'})
        entry = self.by_identity.get(id(statement))
        if entry is not None:
            self._text(entry[1], text)

    def report(self, source):
        rows = [{**row, 'c_expressions': list(row['c_expressions'])} for row in self.rows]
        partial = bool(self.omitted or self.errors or any(r.get('omitted_expression_text') for r in rows))
        return {'status': 'partial' if partial else 'observed', 'source_sha256': sha(source),
                'hazards': rows, 'omitted_hazards': self.omitted, 'errors': sorted(self.errors),
                'scope': 'address fallthrough and late pointer-store hypotheses only',
                'training_eligible': False}


def _locations(source, expressions):
    # Text association is deliberately lexical; whitespace differences from
    # function extraction/C89 lowering do not create a causal ownership claim.
    from solver import project_headers
    masked = project_headers._mask_noncode(source)
    locations, seen, omitted = [], set(), 0
    for text in expressions:
        alternatives = [text]
        if text.startswith('(') and text.endswith(')'):
            alternatives.append(text[1:-1])
        for alternative in alternatives:
            if not alternative.strip():
                continue
            tokens = re.findall(r'\w+|[^\w\s]', alternative)
            pattern = r'\s*'.join((r'(?<!\w)' + re.escape(p) + r'(?!\w)')
                                 if re.fullmatch(r'\w+', p) else re.escape(p) for p in tokens)
            found = False
            for match in re.finditer(pattern, masked):
                found = True
                span = (match.start(), match.end())
                if span in seen:
                    continue
                seen.add(span)
                if len(locations) >= 8:
                    omitted += 1
                    continue
                row = {'start_line': source.count('\n', 0, match.start()) + 1,
                       'end_line': source.count('\n', 0, max(match.start(), match.end()-1)) + 1,
                       'start': match.start(), 'stop': match.end(),
                       'excerpt': source[match.start():match.end()][:1200],
                       'association': 'lexical expression occurrence'}
                locations.append(row)
            if found:
                break
    return locations, omitted


def rebind(source, report, *, origin_source):
    """Carry observations through explicit draft extraction, retaining gaps.

This does not claim that observations survive arbitrary edits. Callers provide
the exact observed stdout; only expressions still present become C locations.
"""
    if report.get('source_sha256') != sha(origin_source):
        raise ValueError('observation origin source is stale')
    return {**report, 'origin_source_sha256': report['source_sha256'],
            'source_sha256': sha(source), 'binding': 'explicit extraction; surviving lexical expressions only'}


def packet(source, report, *, max_regions=6):
    if type(max_regions) is not int or not 1 <= max_regions <= 16:
        raise ValueError('region limit must be 1..16')
    result = {'source_sha256': sha(source), 'instruction_ownership_proven': False,
              'training_eligible': False, 'regions': [], 'omitted_regions': 0}
    if report.get('source_sha256') != result['source_sha256']:
        return {**result, 'status': 'stale-source-observation'}
    rows = report.get('hazards', [])
    unmapped, omitted_locations = 0, 0
    for row in rows:
        locations, omitted = _locations(source, row.get('c_expressions', []))
        omitted_locations += omitted
        if locations:
            if len(result['regions']) < max_regions:
                result['regions'].append({**row, 'c_locations': locations, 'omitted_c_locations': omitted})
            else:
                result['omitted_regions'] += 1
        else:
            unmapped += 1
    result.update(status='partial' if omitted_locations or result['omitted_regions'] else report.get('status', 'unknown'),
                  unmapped_hazards=unmapped, omitted_c_locations=omitted_locations,
                  omitted_hazards=report.get('omitted_hazards', 0), errors=report.get('errors', []))
    return result


def render(source, report):
    data = packet(source, report)
    return ('\nM2C RECONSTRUCTION UNCERTAINTY (source-bound, partial):\n'
        + json.dumps(data, separators=(',', ':'))
        + '\nThese are candidate interpretations, not facts about original C or complete instruction ownership. '
          'Select ONE region and propose ONE bounded edit using the existing schema. Reason about dependent '
          'uses and preserve observed address calculations, memory widths and control flow. For address '
          'scaling consider a byte view, an array view justified by the observed stride, or retaining the '
          'current interpretation. Do not invent a layout or silence errors with arbitrary casts. State the '
          'predicted assembly effect in hypothesis. Compiler and full-function checks decide acceptance. '
          'Unmapped, synthetic or omitted observations remain unknown.\n')
