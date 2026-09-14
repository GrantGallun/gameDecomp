"""Bounded, read-only catalog of hash-pinned binary data assembly.

Emitted spans are not C objects. Initial .data bytes are not current RAM; BSS
is unbacked storage and peripheral addresses are never admitted as RAM.
Literal reconstruction, ROM extraction and typed-C verification are separate.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import re
import struct


VERSION = 1
NAME = r'[A-Za-z_.$][\w.$]*'
ANNOTATED = re.compile(r'^\s*/\*\s*([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F\s]+)\*/\s*\.(\w+)\s+(.*?)\s*$')
LITERAL = re.compile(r'^\s*/\*\s*([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s*\*/\s*\.(\w+)\s+(.*?)\s*$')
REFERENCE = re.compile(r'^(' + NAME + r')(?:\s*([+-])\s*(0x[0-9a-fA-F]+|[0-9]+))?$')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def _integer(text):
    if not re.fullmatch(r'[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)', text.strip()):
        raise ValueError('unsupported integer expression')
    token = text.strip()
    return int(token, 16 if 'x' in token.lower() else 10)


def _encode(directive, expression, symbols):
    """Reconstruct only closed literals or independently bound symbol words."""
    values = [v.strip() for v in expression.split(',')]
    if directive in {'byte', 'short', 'half', 'word', 'dword'}:
        width = {'byte': 1, 'short': 2, 'half': 2, 'word': 4, 'dword': 8}[directive]
        chunks = []
        for value in values:
            try:
                number = _integer(value)
            except ValueError:
                reference = REFERENCE.fullmatch(value) if directive == 'word' else None
                if not reference or reference[1] not in symbols:
                    return None
                number = symbols[reference[1]] + ((-1 if reference[2] == '-' else 1) * int(reference[3], 0) if reference[3] else 0)
            if not -(1 << (width * 8 - 1)) <= number < 1 << (width * 8):
                return None
            chunks.append((number % (1 << (width * 8))).to_bytes(width, 'big'))
        return b''.join(chunks)
    if directive in {'float', 'double'}:
        if any(not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', v) for v in values):
            return None
        floats = [float(v) for v in values]
        if not all(math.isfinite(v) for v in floats):
            return None  # NaN payload/assembler spelling is not reconstructed.
        try:
            return b''.join(struct.pack('>f' if directive == 'float' else '>d', v) for v in floats)
        except (OverflowError, struct.error):
            return None
    if directive in {'ascii', 'asciz', 'asciiz'}:
        # A narrow escape dialect avoids Python/GAS octal/unicode differences.
        if not re.fullmatch(r'"(?:[^"\\\r\n]|\\[\\"nt]|\\[0-7]{3})*"', expression):
            return None
        try:
            text = ast.literal_eval(expression)
            return text.encode('ascii') + (b'\0' if directive != 'ascii' else b'')
        except (ValueError, SyntaxError, UnicodeError):
            return None
    return None


def _storage(section, address, writable=False):
    if 0xA4000000 <= address < 0xA5000000:
        return 'mmio_not_ram'
    return {'.rodata': 'mutable_initial' if writable else 'readonly_initial', '.data': 'mutable_initial',
            '.sdata': 'mutable_initial', '.bss': 'unbacked_bss',
            '.sbss': 'unbacked_bss'}.get(section, 'unknown')


def build(assemblies: dict[str, str], pins: dict[str, str], *,
          rom: bytes | None = None, rom_sha256: str | None = None,
          symbols: dict[str, int] | None = None, symbols_sha256: str | None = None,
          max_regions: int = 20000, max_bytes: int = 16 * 1024 * 1024) -> dict:
    """Check all inputs, then emit deterministic bounded spans and references.

    `symbols_sha256` binds sorted compact JSON of the supplied address map.
    Its caller must supply independently derived symbol evidence, not resolve
    symbols from the same annotated words it intends to verify.
    Unsupported directives are reported, never filled with invented bytes.
    """
    if max_regions < 1 or max_bytes < 1:
        raise ValueError('catalog limits must be positive')
    if (rom is None) != (rom_sha256 is None):
        raise ValueError('ROM bytes and expected hash must be supplied together')
    if rom is not None and digest(rom) != rom_sha256:
        raise ValueError('ROM hash differs from expected pin')
    symbol_map_provided = symbols is not None or symbols_sha256 is not None
    symbols = {} if symbols is None else symbols
    if symbol_map_provided and (identity(symbols) != symbols_sha256 or any(
            not re.fullmatch(NAME, k) or isinstance(v, bool) or not isinstance(v, int)
            or not 0 <= v <= 0xffffffff for k, v in symbols.items())):
        raise ValueError('invalid or unpinned symbol map')
    inputs = []
    for path, text in sorted(assemblies.items()):
        actual = digest(text.encode())
        if actual != pins.get(path):
            raise ValueError('assembly hash differs from expected pin: ' + path)
        inputs.append({'path': path, 'sha256': actual})

    regions, references, declines = [], [], []
    used_bytes = 0
    omitted_regions = omitted_bytes = 0
    intervals = []

    def decline(path, line, reason):
        declines.append({'path': path, 'line': line, 'reason': reason})

    def emit(region):
        nonlocal used_bytes, omitted_regions, omitted_bytes
        if region is None:
            return
        size = region['size']
        if len(regions) >= max_regions or used_bytes + size > max_bytes:
            omitted_regions += 1
            omitted_bytes += size
            return
        region['id'] = identity({k: region[k] for k in ('path', 'input_sha256', 'line', 'address', 'size')})
        region['extent_scope'] = 'contiguous emitted span; not C sizeof or exclusive ownership'
        region['typed_c_verified'] = False
        raw = bytes.fromhex(region.pop('_hex'))
        region['bytes_hex'] = raw.hex() if raw else None
        region['bytes_sha256'] = digest(raw) if raw else None
        region['rom_verified'] = bool(raw and rom is not None)
        region['reconstructed_bytes'] = sum(s['size'] for s in region['spans'] if s['reconstruction'] == 'matched')
        region['reconstruction'] = ('complete' if region['reconstructed_bytes'] == size else
                                    'partial' if region['reconstructed_bytes'] else 'unavailable')
        for reference in region.pop('_references'):
            references.append({**reference, 'region_id': region['id']})
        regions.append(region)
        used_bytes += size

    for path, text in sorted(assemblies.items()):
        section = None
        writable = False
        labels = []
        current = None
        for line_number, line in enumerate(text.splitlines(), 1):
            section_match = re.match(r'^\s*\.section\s+(\.[\w.]+)(?:\s*,.*)?$', line)
            label = re.match(r'^\s*(?:dlabel|glabel|jlabel)\s+(' + NAME + r')\s*$', line)
            if section_match or re.match(r'^\s*enddlabel\b', line):
                emit(current)
                current, labels = None, []
                if section_match:
                    section = section_match[1]
                    flags = re.search(r',\s*"([^"]*)"', line)
                    writable = bool(flags and 'w' in flags[1])
                continue
            if label:
                if current:
                    emit(current)
                    current, labels = None, []
                labels.append(label[1])
                continue
            match = ANNOTATED.match(line)
            has_byte_annotation = bool(match)
            literal = LITERAL.match(line) if not match else None
            if literal:
                encoded = _encode(literal[3], literal[4], symbols)
                if encoded is None:
                    emit(current)
                    current, labels = None, []
                    decline(path, line_number, 'unparsed directive: .' + literal[3])
                    continue
                match = (None, literal[1], literal[2], encoded.hex(), literal[3], literal[4])
            bss = re.match(r'^\s*/\*\s*([0-9a-fA-F]+)\s*\*/\s*\.space\s+(0x[0-9a-fA-F]+|[0-9]+)\s*$', line)
            if not match and not bss:
                clean = re.sub(r'/\*.*?\*/', '', line).strip()
                if clean.startswith('.') and not clean.startswith(('.include ', '.balign ', '.align ')):
                    emit(current)
                    current, labels = None, []
                    decline(path, line_number, 'unparsed directive: ' + clean[:100])
                elif clean.startswith(('.balign ', '.align ')):
                    # Never bridge inferred alignment bytes into a known span.
                    emit(current)
                    current, labels = None, []
                continue
            if bss:
                offset, address, raw, directive, expression = None, int(bss[1], 16), b'', 'space', bss[2]
                size = int(expression, 0)
                if section not in {'.bss', '.sbss'} or size <= 0:
                    decline(path, line_number, 'space outside BSS or invalid extent')
                    continue
                reconstructed = None
            else:
                offset, address = int(match[1], 16), int(match[2], 16)
                try:
                    raw = bytes.fromhex(re.sub(r'\s', '', match[3]))
                except ValueError:
                    raise ValueError(f'invalid byte annotation at {path}:{line_number}')
                directive, expression = match[4], match[5]
                size = len(raw)
                if not size:
                    continue
                if section in {'.bss', '.sbss'}:
                    raise ValueError('file bytes claimed for unbacked BSS')
                if rom is not None and (offset + size > len(rom) or rom[offset:offset + size] != raw):
                    raise ValueError(f'annotated bytes differ from pinned ROM at {path}:{line_number}')
                reconstructed = _encode(directive, expression, symbols)
                if reconstructed is not None and reconstructed != raw:
                    raise ValueError(f'reconstructed directive differs from annotated bytes at {path}:{line_number}')
                if reconstructed is None:
                    decline(path, line_number, 'directive bytes not independently reconstructed: .' + directive)
            if not 0 <= address < 0x100000000 or address + size > 0x100000000:
                raise ValueError('emitted address range exceeds 32 bits')
            intervals.append((address, address + size, raw, offset, _storage(section, address, writable)))
            if current and (current['address'] + current['size'] != address or
                    current['section'] != section or
                    (offset is not None and current['rom_offset'] + current['size'] != offset) or
                    (offset is None) != (current['rom_offset'] is None)):
                emit(current)
                current, labels = None, []
            if current is None:
                current = {'path': path, 'input_sha256': pins[path], 'line': line_number,
                           'section': section, 'storage': _storage(section, address, writable),
                           'labels': list(labels), 'address': address, 'rom_offset': offset,
                           'size': 0, '_hex': '', 'spans': [], '_references': []}
            current['size'] += size
            current['_hex'] += raw.hex()
            span = {'line': line_number, 'line_end': line_number, 'address': address, 'rom_offset': offset,
                    'size': size, 'directive': directive, 'annotated_bytes': has_byte_annotation,
                    'reconstruction': 'matched' if reconstructed is not None else 'unavailable'}
            prior_span = current['spans'][-1] if current['spans'] else None
            if prior_span and all(prior_span[k] == span[k] for k in ('directive', 'reconstruction', 'annotated_bytes')) and prior_span['line_end'] + 1 == line_number:
                prior_span['size'] += size
                prior_span['line_end'] = line_number
            else:
                current['spans'].append(span)
            if directive == 'word':
                expressions = [v.strip() for v in expression.split(',')]
                if len(expressions) * 4 == len(raw):
                    for index, value in enumerate(expressions):
                        ref = REFERENCE.fullmatch(value)
                        if ref:
                            addend = (-1 if ref[2] == '-' else 1) * int(ref[3], 0) if ref[3] else 0
                            encoded = int.from_bytes(raw[index*4:index*4+4], 'big')
                            current['_references'].append({'symbol': ref[1], 'addend': addend,
                                'address': address + index*4, 'encoded_address': encoded,
                                'line': line_number, 'path': path, 'input_sha256': pins[path],
                                'kind': 'explicit_named_address',
                                'resolution': 'independent_symbol_match' if ref[1] in symbols else 'annotated_symbol_only',
                                'dispatch_proven': False, 'abi_known': False})
        emit(current)
    # Validate overlap in address order; the normal disjoint corpus stays linear
    # after sorting instead of comparing every word with the whole catalog.
    active = []
    for address, end, raw, offset, storage in sorted(intervals, key=lambda r: (r[0], r[1])):
        active = [r for r in active if r[1] > address]
        for start, old_end, old_raw, old_offset, old_storage in active:
            lo, hi = address, min(end, old_end)
            if storage != old_storage or bool(raw) != bool(old_raw) or (
                    raw and (old_raw[lo-start:hi-start] != raw[:hi-lo]
                             or old_offset + lo-start != offset)):
                raise ValueError('conflicting overlapping data at ' + hex(address))
        active.append((address, end, raw, offset, storage))
    def union_size(ranges):
        total = end = 0
        for start, stop in sorted(ranges):
            total += max(0, stop - max(start, end))
            end = max(end, stop)
        return total

    def span_bytes(predicate):
        return union_size((s['address'], s['address'] + s['size'])
                          for r in regions for s in r['spans'] if predicate(r, s))

    reconstructed = span_bytes(lambda r, s: s['reconstruction'] == 'matched')
    catalog = {'schema_version': VERSION, 'kind': 'pinned-binary-data-catalog',
               'implementation_sha256': digest(Path(__file__).read_bytes()),
               'inputs': inputs, 'rom_sha256': rom_sha256, 'rom_size': len(rom) if rom is not None else None,
               'symbols_sha256': symbols_sha256, 'regions': regions, 'references': references,
               'declines': declines[:128], 'omitted_declines': max(0, len(declines)-128),
               'limits': {'max_regions': max_regions, 'max_bytes': max_bytes},
               'counts': {'regions': len(regions), 'listed_bytes': used_bytes,
                          'emitted_bytes': span_bytes(lambda r, s: True),
                          'file_backed_bytes': span_bytes(lambda r, s: r['bytes_hex'] is not None),
                          'reconstructed_bytes': reconstructed,
                          'rom_verified_bytes': span_bytes(lambda r, s: r['rom_verified']),
                          'rom_file_bytes': union_size((s['rom_offset'], s['rom_offset']+s['size'])
                              for r in regions for s in r['spans'] if r['rom_verified']),
                          'reconstructed_rom_file_bytes': union_size((s['rom_offset'], s['rom_offset']+s['size'])
                              for r in regions for s in r['spans'] if r['rom_verified'] and s['reconstruction'] == 'matched'),
                          'readonly_initial_bytes': span_bytes(lambda r, s: r['storage'] == 'readonly_initial'),
                          'mutable_initial_bytes': span_bytes(lambda r, s: r['storage'] == 'mutable_initial'),
                          'unbacked_bss_bytes': span_bytes(lambda r, s: r['storage'] == 'unbacked_bss'),
                          'typed_c_verified_bytes': 0, 'address_references': len(references),
                          'unparsed_directives': sum(d['reason'].startswith('unparsed directive:') for d in declines),
                          'omitted_regions': omitted_regions, 'omitted_bytes': omitted_bytes},
               'scope': 'binary initial data and emitted spans; no live RAM, device behavior, C layout, or dispatch ABI inferred'}
    catalog['catalog_sha256'] = identity(catalog)
    return catalog


def packet(catalog, function, assembly, assembly_sha256, *, max_regions=8, max_chars=6000):
    """Source-bound incoming table membership and outgoing data-reference hints."""
    if digest(assembly.encode()) != assembly_sha256:
        raise ValueError('target assembly hash differs from expected pin')
    if catalog.get('catalog_sha256') != identity({k: v for k, v in catalog.items() if k != 'catalog_sha256'}):
        raise ValueError('catalog identity changed')
    if not re.fullmatch(NAME, function) or max_regions < 1 or max_chars < 512:
        raise ValueError('invalid packet request')
    entry = None
    armed = False
    for line in assembly.splitlines():
        label = re.match(r'^\s*glabel\s+(' + NAME + r')\s*$', line)
        if label:
            armed = label[1] == function
        match = re.match(r'^\s*/\*\s*[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]{8}\s*\*/', line)
        if armed and match:
            entry = int(match[1], 16)
            break
    used = set(re.findall(r'%(?:hi|lo)\((' + NAME + r')(?:\s*[+-][^)]*)?\)', assembly))
    incoming = [r for r in catalog['references'] if r['symbol'] == function and
                entry is not None and (r['encoded_address'] - r['addend']) & 0xffffffff == entry]
    conflicted = sum(r['symbol'] == function and r not in incoming for r in catalog['references'])
    related = {r['region_id'] for r in incoming}
    rows = []
    for region in catalog['regions']:
        if region['id'] not in related and not used.intersection(region['labels']):
            continue
        row = {k: region[k] for k in ('id', 'labels', 'address', 'size', 'section', 'storage',
                                      'path', 'input_sha256', 'line', 'rom_offset', 'rom_verified',
                                      'reconstruction', 'extent_scope')}
        row['bytes_prefix_hex'] = region['bytes_hex'][:128] if region['bytes_hex'] else None
        row['bytes_omitted'] = max(0, region['size'] - 64) if region['bytes_hex'] else region['size']
        row['relations'] = ([] if region['id'] not in related else ['address_taken_data_reference']) + (
            ['target_relocation_reference'] if used.intersection(region['labels']) else [])
        locations = {r['address'] for r in catalog['references'] if r['region_id'] == region['id']}
        row['table_candidate'] = any(a % 4 == 0 and a+4 in locations for a in locations)
        row['dispatch_proven'] = False
        row['reference_sites'] = [{'address': r['address'], 'encoded_address': r['encoded_address'],
                                  'addend': r['addend']} for r in incoming if r['region_id'] == region['id']][:8]
        rows.append(row)
    result = {'schema_version': VERSION, 'function': function, 'target_assembly_sha256': assembly_sha256,
              'catalog_sha256': catalog['catalog_sha256'], 'regions': [], 'omitted_regions': len(rows),
              'unresolved_incoming_references': conflicted,
              'scope': 'address-taken and relocation evidence; no proven caller, ABI, C sizeof, or current memory values'}
    if len(json.dumps(result, sort_keys=True)) > max_chars:
        raise ValueError('packet identity metadata exceeds character budget')
    for row in rows[:max_regions]:
        proposal = {**result, 'regions': [*result['regions'], row],
                    'omitted_regions': result['omitted_regions']-1}
        if len(json.dumps(proposal, sort_keys=True)) > max_chars:
            break
        result = proposal
    return result


def verify_candidate(catalog, region_id, candidate_bytes, *, offset=0):
    """Compare supplied bytes without promoting a data object or a C source.

    The caller owns any compilation/source lineage for candidate_bytes. This
    result is only a bounded byte comparison against a ROM-verified span.
    """
    if catalog.get('catalog_sha256') != identity({k: v for k, v in catalog.items() if k != 'catalog_sha256'}):
        raise ValueError('catalog identity changed')
    rows = [r for r in catalog['regions'] if r['id'] == region_id]
    if len(rows) != 1 or not rows[0]['rom_verified'] or rows[0]['bytes_hex'] is None:
        raise ValueError('comparison needs one ROM-verified file-backed region')
    region = rows[0]
    if not isinstance(candidate_bytes, bytes) or not candidate_bytes or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset + len(candidate_bytes) > region['size']:
        raise ValueError('candidate comparison range is empty or outside the emitted span')
    expected = bytes.fromhex(region['bytes_hex'])[offset:offset+len(candidate_bytes)]
    differences = [i for i, (a, b) in enumerate(zip(expected, candidate_bytes)) if a != b]
    first = differences[0] if differences else None
    return {'schema_version': VERSION, 'catalog_sha256': catalog['catalog_sha256'],
            'region_id': region_id, 'offset': offset, 'compared_bytes': len(candidate_bytes),
            'equal_bytes': len(candidate_bytes)-len(differences), 'different_bytes': len(differences),
            'exact_compared_bytes': not differences,
            'whole_region_compared': offset == 0 and len(candidate_bytes) == region['size'],
            'first_difference': None if first is None else {'region_offset': offset+first,
                'address': region['address']+offset+first, 'expected': expected[first],
                'candidate': candidate_bytes[first]},
            'candidate_sha256': digest(candidate_bytes), 'expected_sha256': digest(expected),
            'typed_c_verified': False, 'promoted': False,
            'scope': 'supplied byte comparison only; candidate compilation and C ownership not established'}
