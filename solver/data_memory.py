"""Admit bounded ROM-verified read-only initial data into differential inputs.

These are emitted static spans, not inferred C object extents or current heap
snapshots. Writable data, BSS, devices and unbound addresses stay unsupported.
"""
import hashlib
import re
from solver import binary_data

NAME = r'[A-Za-z_.$][\w.$]*'


def select(catalog, assembly, assembly_sha256, *, max_bytes=4096):
    if catalog.get('catalog_sha256') != binary_data.identity({k:v for k,v in catalog.items() if k != 'catalog_sha256'}):
        raise ValueError('data memory catalog checksum mismatch')
    if hashlib.sha256(assembly.encode()).hexdigest() != assembly_sha256:
        raise ValueError('data memory target assembly changed')
    used = set(re.findall(r'%(?:hi|lo)\((' + NAME + r')(?:[+-][^)]*)?\)', assembly))
    regions, declines, consumed = [], [], 0
    for region in catalog['regions']:
        names = sorted(used.intersection(region['labels']))
        if not names:
            continue
        if (region['storage'] != 'readonly_initial' or not region['rom_verified']
                or not region['bytes_hex'] or region['size'] > max_bytes - consumed
                or not 0x80000000 <= region['address'] < region['address'] + region['size'] <= 0x80800000):
            declines.append({'region_id': region['id'], 'symbols': names,
                             'reason': 'requires bounded ROM-verified readonly RAM span'})
            continue
        # Multiple labels at the same emitted start are aliases, not copies.
        for name in names:
            regions.append({'symbol': name, 'address': region['address'],
                            'bytes_hex': region['bytes_hex'], 'bytes_sha256': region['bytes_sha256'],
                            'region_id': region['id'], 'storage': region['storage'],
                            'rom_verified': True})
        consumed += region['size']
    result = {'schema_version': 1, 'catalog_sha256': catalog['catalog_sha256'],
              'target_assembly_sha256': assembly_sha256, 'regions': regions, 'declines': declines,
              'scope': 'ROM-verified readonly initial spans; no mutable globals, BSS, heap or MMIO seeding; not C sizeof'}
    result['sha256'] = binary_data.identity(result)
    return result


def apply(assembly, context, *, raw_target):
    from solver.mips_differential import Program
    if not context:
        return assembly, {'status': 'not_requested', 'regions': []}
    if binary_data.identity({k:v for k,v in context.items() if k != 'sha256'}) != context.get('sha256'):
        raise ValueError('data memory context checksum mismatch')
    if hashlib.sha256(raw_target.encode()).hexdigest() != context['target_assembly_sha256']:
        raise ValueError('data memory context belongs to another target')
    program = Program.parse('data-admission', assembly)
    owned = []
    unknown_owned = set()
    context_addresses = {row['symbol']:row['address'] for row in context['regions']}
    for mapping, width in ((program.data_bytes, 1), (program.data_words, 4)):
        for name, offset in mapping:
            absolute = re.fullmatch(r'D_([0-9A-Fa-f]+)', name)
            address = program.symbol_addresses.get(name, context_addresses.get(name))
            if address is None and absolute:
                address = int(absolute[1],16)
            if address is not None:
                start = address + offset
                owned.append((start, start + width))
            else:
                unknown_owned.add(name)
    additions, admitted, declined = [], [], list(context.get('declines', []))
    addressed = {}
    total = 0
    for row in context['regions']:
        name, address = row['symbol'], row['address']
        raw = bytes.fromhex(row['bytes_hex'])
        if (not re.fullmatch(NAME, name) or row['storage'] != 'readonly_initial' or row['rom_verified'] is not True
                or not raw or len(raw) > 4096 or not 0x80000000 <= address < address + len(raw) <= 0x80800000
                or hashlib.sha256(raw).hexdigest() != row['bytes_sha256']):
            raise ValueError('invalid static data admission')
        if name in program.symbol_addresses and program.symbol_addresses[name] != address:
            raise ValueError('static data symbol address disagrees with target')
        if unknown_owned or any(address < end and start < address + len(raw) for start,end in owned):
            declined.append({'region_id': row['region_id'], 'reason': 'target ELF already owns initialized data'})
            continue
        for offset, byte in enumerate(raw):
            physical = address + offset
            if physical in addressed and addressed[physical] != byte:
                raise ValueError('conflicting static data aliases')
            addressed[physical] = byte
        total = len(addressed)
        if total > 4096:
            raise ValueError('static data admission exceeds byte budget')
        additions.extend([f'# MIPS_DIFF_SYMBOL {name} {address}',
                          f'# MIPS_DIFF_BYTES {name} {raw.hex()}'])
        admitted.append({k:row[k] for k in ('symbol','address','region_id','bytes_sha256')})
    report = {'status': 'admitted' if admitted else 'no_eligible_external_data',
              'context_sha256': context['sha256'], 'catalog_sha256': context['catalog_sha256'],
              'regions': admitted, 'declines': declined, 'mapped_bytes': total, 'scope': context['scope']}
    return assembly + ('\n' + '\n'.join(additions) + '\n' if additions else ''), report
