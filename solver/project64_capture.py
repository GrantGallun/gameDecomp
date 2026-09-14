"""Import the paired paused-state export from Project64 6f7612b.

This adapter consumes explicit byte hex, not Duktape Buffer.toString('hex'),
which is not the Node Buffer encoding API. It never labels this export GDB.
ROM binding, canonical o32 registers and bounded RAM retain the replay gates.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from solver import evidence_schedule, runtime_capture


REVISION = '6f7612b'
GPRS = ('zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 '
        's0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra').split()


def register_map():
    """Physical MIPS slots; preserve every high half before o32 validation."""
    return {**{name: {'number': i, 'bytes': 8} for i, name in enumerate(GPRS)},
            'hi': {'number': 32, 'bytes': 8}, 'lo': {'number': 33, 'bytes': 8},
            'pc': {'number': 34, 'bytes': 4}}


def import_export(raw, plan, rom):
    """Validate two actual stopped snapshots against an independently supplied plan.

    A checksum preserves provenance; it does not authenticate an emulator or
    make a one-entry comparison a whole-program correctness certificate.
    """
    runtime_capture.validate_plan(plan)
    if plan['registers'] != register_map():
        raise ValueError('Project64 requires its explicit full-width physical register map')
    if (raw.get('schema_version') != 1 or raw.get('kind') != 'project64-debug-paused-export'
            or raw.get('producer_revision') != REVISION or raw.get('entry') != plan['entry']):
        raise ValueError('unsupported Project64 export identity')
    samples = raw.get('samples')
    if not isinstance(samples, list) or len(samples) != 2 or samples[0] != samples[1]:
        raise ValueError('Project64 requires two identical stopped-state samples')
    sample = samples[0]
    if sample.get('paused') is not True or sample.get('pc') != plan['entry']:
        raise ValueError('Project64 was not debug-paused at the selected entry')
    def word(value):
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise ValueError('invalid Project64 register half')
        return value
    for field in ('gpr', 'ugpr'):
        if not isinstance(sample.get(field), list) or len(sample[field]) != 32:
            raise ValueError('incomplete Project64 integer register array')
    values = {name: word(sample['gpr'][i]) | (word(sample['ugpr'][i]) << 32)
              for i, name in enumerate(GPRS)}
    for name in ('hi', 'lo'):
        values[name] = word(sample[name]) | (word(sample['u' + name]) << 32)
    values['pc'] = word(sample['pc'])
    if values['zero'] != 0:
        raise ValueError('invalid MIPS zero register')
    runtime_capture.validate_registers(values, plan)
    rom_bytes = Path(rom).read_bytes()
    if (len(rom_bytes) < 64 or rom_bytes[:4] != bytes.fromhex('80371240')
            or hashlib.sha256(rom_bytes).hexdigest() != plan['rom_sha256']):
        raise ValueError('Project64 ROM identity differs from supplied plan')
    info = raw.get('rom_info', {})
    if any(info.get(name) != int.from_bytes(rom_bytes[offset:offset + 4], 'big')
           for name, offset in (('crc1', 16), ('crc2', 20))):
        raise ValueError('Project64 loaded ROM header differs from bound ROM')
    record = {'schema_version': 1, 'kind': 'project64-stopped-entry-capture',
              'plan': plan, 'registers': values, 'memory': sample['memory'],
              'code_hex': sample['code_hex'], 'stop': 'EMU_DEBUG_PAUSED; debug.paused=true',
              'authority': 'observed Project64 debugger state with ROM-bound instruction window',
              'producer': {'revision': REVISION, 'raw_sha256': evidence_schedule.fingerprint(raw)},
              'limitations': ['one integer-leaf entry; not whole-game behavioral equivalence',
                              'device state, other threads, and external effects are not captured',
                              'RAM ranges and stack classification are supplied assumptions']}
    record['sha256'] = evidence_schedule.fingerprint(record)
    runtime_capture.verify(record, rom)
    return record
