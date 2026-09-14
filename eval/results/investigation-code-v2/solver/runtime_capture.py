"""Stopped-target GDB capture and bounded replay; never a whole-game verdict.

The adapter requires an explicit register map and RAM windows. It does not
guess a stub's register numbering, read MMIO, or supply missing memory as zero.
Protocol reference: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Packets.html
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
import json
from pathlib import Path
import re
import socket

from solver import evidence_schedule, mips_differential as d


class Remote:
    def __init__(self, sock):
        self.sock = sock

    def _byte(self):
        data = self.sock.recv(1)
        if not data:
            raise ConnectionError('debugger disconnected')
        return data

    def request(self, command):
        raw = command.encode('ascii')
        self.sock.sendall(b'$' + raw + b'#' + f'{sum(raw) & 255:02x}'.encode())
        for _ in range(64):
            start = self._byte()
            if start == b'-':
                raise ValueError('debugger rejected packet checksum')
            if start == b'+':
                continue
            if start != b'$':
                raise ValueError('unexpected debugger packet')
            data = bytearray()
            while (char := self._byte()) != b'#':
                data.extend(char)
                if len(data) > 1024 * 1024:
                    raise ValueError('debugger packet too large')
            checksum = self._byte() + self._byte()
            if int(checksum, 16) != sum(data) & 255:
                self.sock.sendall(b'-')
                raise ValueError('debugger response checksum mismatch')
            self.sock.sendall(b'+')
            text = data.decode('ascii')
            if text.startswith('O') and text != 'OK':
                continue  # console output is not the command result
            if '*' in text or '}' in text:
                raise ValueError('compressed/escaped response unsupported; capture declined')
            if (text.startswith('E') and len(text) == 3) or text.startswith('E.'):
                raise ValueError('debugger error: ' + text)
            return text
        raise ValueError('too many console/ack packets')

    def memory(self, address, size):
        data = bytearray()
        for offset in range(0, size, 256):
            count = min(256, size - offset)
            block = bytes.fromhex(self.request(f'm{address + offset:x},{count:x}'))
            if len(block) != count:
                raise ValueError('partial debugger memory reply')
            data.extend(block)
        return bytes(data)


def validate_plan(plan):
    if plan.get('architecture') != 'mips-o32-be' or plan.get('byte_order') != 'big':
        raise ValueError('capture requires explicit mips-o32-be register layout')
    registers = plan.get('registers', {})
    required = set(d._seed_registers(d.TestCase('register-schema', 0))) | {'pc', 'hi', 'lo'}
    if not required <= set(registers):
        raise ValueError('capture plan omits integer entry registers or pc')
    slots = []
    for spec in registers.values():
        if spec.get('bytes') not in {4, 8} or not isinstance(spec.get('number'), int) or spec['number'] < 0:
            raise ValueError('invalid explicit register map')
        slots.append(spec['number'])
    if len(set(slots)) != len(slots):
        raise ValueError('register aliases require an explicit adapter')
    windows = sorted(plan.get('ram', []), key=lambda row: row['address'])
    if not windows or sum(r['size'] for r in windows) > 8 * 1024 * 1024:
        raise ValueError('capture needs bounded RAM windows')
    end = 0
    names = set()
    for row in windows:
        start, size = row['address'], row['size']
        if size <= 0 or start < end or start < 0x80000000 or start + size > 0x80800000:
            raise ValueError('only nonoverlapping cached N64 RDRAM windows are supported')
        if row['name'] in names or row.get('kind') not in {'persistent', 'stack'}:
            raise ValueError('invalid RAM window identity/kind')
        names.add(row['name'])
        end = start + size
    if not isinstance(plan.get('entry'), int) or plan.get('code_size', 0) <= 0 or plan['code_size'] > 65536:
        raise ValueError('invalid function entry/extent')
    if plan['entry'] % 4 or plan['code_size'] % 4:
        raise ValueError('unaligned function identity')
    if not 0x80000000 <= plan['entry'] < plan['entry'] + plan['code_size'] <= 0x80800000:
        raise ValueError('function capture must be inside cached N64 RDRAM')
    if not isinstance(plan.get('rom_offset'), int) or plan['rom_offset'] < 0:
        raise ValueError('invalid ROM offset')
    return windows


def capture(remote, plan, rom):
    windows = validate_plan(plan)
    raw_rom = Path(rom).read_bytes()
    if hashlib.sha256(raw_rom).hexdigest() != plan['rom_sha256']:
        raise ValueError('capture ROM identity mismatch')
    stop = remote.request('?')
    if not re.match(r'^[ST][0-9a-fA-F]{2}', stop):
        raise ValueError('debugger must already be stopped at the target entry')
    def read_registers():
        values = {}
        for name, spec in plan['registers'].items():
            data = bytes.fromhex(remote.request(f"p{spec['number']:x}"))
            if len(data) != spec['bytes']:
                raise ValueError('debugger register width differs from plan')
            values[name] = int.from_bytes(data, 'big')
        return values
    registers = read_registers()
    validate_registers(registers, plan)
    if registers['pc'] & 0xffffffff != plan['entry']:
        raise ValueError('debugger PC is not the selected function entry')
    code = remote.memory(plan['entry'], plan['code_size'])
    offset = plan['rom_offset']
    if offset < 0 or code != raw_rom[offset:offset + len(code)]:
        raise ValueError('runtime instructions do not match the bound ROM extent')
    memory = [{**r, 'hex': remote.memory(r['address'], r['size']).hex()} for r in windows]
    if any(remote.memory(r['address'], r['size']).hex() != r['hex'] for r in memory):
        raise ValueError('RAM changed while target was stopped')
    if registers != read_registers() or stop != remote.request('?'):
        raise ValueError('target moved during capture')
    result = {'schema_version': 1, 'kind': 'gdb-stopped-entry-capture', 'plan': plan,
              'registers': registers, 'memory': memory, 'code_hex': code.hex(),
              'stop': stop, 'authority': 'observed debugger state with ROM-bound instruction window',
              'limitations': ['device state, other threads, and external effects are not captured',
                             'RAM ranges and stack classification are supplied assumptions']}
    result['sha256'] = evidence_schedule.fingerprint(result)
    return result


def verify(record, rom):
    body = {k: v for k, v in record.items() if k != 'sha256'}
    if evidence_schedule.fingerprint(body) != record.get('sha256'):
        raise ValueError('capture checksum mismatch')
    plan = record['plan']
    windows = validate_plan(plan)
    validate_registers(record['registers'], plan)
    raw = Path(rom).read_bytes()
    code = bytes.fromhex(record['code_hex'])
    if hashlib.sha256(raw).hexdigest() != plan['rom_sha256'] or len(code) != plan['code_size']:
        raise ValueError('capture ROM/extent changed')
    if code != raw[plan['rom_offset']:plan['rom_offset'] + len(code)]:
        raise ValueError('capture instructions differ from ROM')
    if [{k: row[k] for k in ('address', 'size', 'name', 'kind')} for row in record['memory']] != windows:
        raise ValueError('capture RAM differs from plan')
    for row in record['memory']:
        if len(bytes.fromhex(row['hex'])) != row['size']:
            raise ValueError('capture RAM incomplete')


def validate_registers(registers, plan):
    if set(registers) != set(plan['registers']):
        raise ValueError('capture registers differ from explicit map')
    for name, value in registers.items():
        width = plan['registers'][name]['bytes']
        if type(value) is not int or not 0 <= value < 1 << (width * 8):
            raise ValueError('invalid captured register value')
        if width == 8:
            low = value & 0xffffffff
            expected = low | (0xffffffff00000000 if low & 0x80000000 else 0)
            if value != expected:
                raise ValueError('noncanonical 64-bit register requires a full MIPS64 execution backend')
    if registers['pc'] & 0xffffffff != plan['entry']:
        raise ValueError('captured register PC differs from function entry')


def replay(record, rom, target_assembly, candidate_assembly, *, repo=None, return_registers=('v0',), max_steps=10000):
    verify(record, rom)
    plan = record['plan']
    if repo is None:
        raise ValueError('replay requires the target project for independent instruction reassembly')
    from solver import linked_callee
    binding = linked_callee.bind(Path(repo), plan['function'], target_assembly, plan['entry'], plan['code_size'])
    if binding['text_sha256'] != hashlib.sha256(bytes.fromhex(record['code_hex'])).hexdigest():
        raise ValueError('reassembled target differs from capture')
    target = replace(d.Program.parse('captured_target', target_assembly), text_base=plan['entry'])
    candidate = d.Program.parse('captured_candidate', candidate_assembly)
    # The annotation words must cover precisely the ROM-bound instruction window.
    words = re.findall(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s*\*/', target_assembly)
    if (len(words) != len(target.instructions) or len(words) * 4 != plan['code_size']
            or ''.join(w[1].lower() for w in words) != record['code_hex'].lower()
            or [int(w[0], 16) for w in words] != list(range(plan['entry'], plan['entry'] + plan['code_size'], 4))):
        raise ValueError('target assembly annotations do not bind the captured instructions')
    for program in (target, candidate):
        if any(i.opcode in {'jal', 'jalr', 'bal', 'syscall'} or 'c1' in i.opcode
               or '.' in i.opcode or i.opcode.startswith('d') for i in program.instructions):
            raise ValueError('capture replay currently supports integer leaves only; calls/FPU/64-bit require contracts')
    addresses = {**target.symbol_addresses, **candidate.symbol_addresses}
    names = target.symbols | candidate.symbols
    if not names <= addresses.keys():
        raise ValueError('captured replay requires concrete linker addresses for every symbol')
    for name in target.symbol_addresses.keys() & candidate.symbol_addresses.keys():
        if target.symbol_addresses[name] != candidate.symbol_addresses[name]:
            raise ValueError('conflicting symbol bindings')
    symbols = d.SymbolTable(names, addresses)
    registers = {k: v & 0xffffffff for k, v in record['registers'].items() if k in d.REGISTER_NAMES | {'hi', 'lo'}}
    # Return interception is already part of the function runner. Preserve all
    # captured entry values, including the real return address and stack pointer.
    runs = []
    for program in (target, candidate):
        memory = d.Memory([], {})
        for row in record['memory']:
            memory.regions.append(d.Region(row['name'], row['address'], row['size'], row['kind']))
            memory.data.update({row['address'] + i: value for i, value in enumerate(bytes.fromhex(row['hex']))})
        memory.mark_clean()
        runs.append(d.Runner(program, memory, registers, symbols, max_steps=max_steps,
                             return_registers=return_registers).execute())
    compared = d._compare_runs(d.TestCase('runtime-capture-' + record['sha256'][:12], 0), *runs)
    return {'capture_sha256': record['sha256'], 'target_binding': binding, 'comparison': compared.to_dict(),
            'authoritative': False, 'scope': 'one captured integer-leaf entry under explicit RAM assumptions'}
