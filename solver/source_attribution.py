"""Capture IDO's original object line records before the matching helper strips them.

No compiler flags change. Address records are accepted only after allocated
sections/relocations match the final object and the production normalizer
reproduces its entire instruction listing. Unmapped/header locations stay gaps.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import struct

from solver import byte_certificate


# Stored form. `collect()` returns `instructions` as a list, and every attempt's `sampling` JSON used to
# carry it verbatim: ~30 KB per row, two thirds of the campaign DB (2026-09-22: 7.5 GB of `sampling` in
# each of four copies). It is derived from the recorded compile and no reader takes it from the database,
# so `eval.attempt_compact` stores it as `instructions_packed` under this codec. Read it with
# `instructions_of`, which accepts both forms.
INSTRUCTIONS_CODEC = 'zlib+base64+json/v1'


def instructions_of(attribution: dict | None) -> list:
    """The per-instruction rows of an attribution record, whether stored plain or packed."""
    if not attribution:
        return []
    if 'instructions' in attribution:
        return attribution['instructions']
    if attribution.get('instructions_codec') == INSTRUCTIONS_CODEC:
        import base64, json, zlib
        return json.loads(zlib.decompress(base64.b64decode(attribution['instructions_packed'])))
    if 'instructions_packed' in attribution:
        raise ValueError(f"unknown attribution codec {attribution.get('instructions_codec')!r}")
    return []


def pack_instructions(attribution: dict) -> dict:
    """The same record with `instructions` packed; unchanged if there is nothing to pack."""
    if not isinstance(attribution.get('instructions'), list) or not attribution['instructions']:
        return attribution
    import base64, json, zlib
    raw = json.dumps(attribution['instructions'], sort_keys=True, separators=(',', ':')).encode()
    packed = {k: v for k, v in attribution.items() if k != 'instructions'}
    packed['instructions_codec'] = INSTRUCTIONS_CODEC
    packed['instructions_packed'] = base64.b64encode(zlib.compress(raw, 9)).decode('ascii')
    return packed


def unpack_instructions(attribution: dict) -> dict:
    """Inverse of `pack_instructions`."""
    if 'instructions_packed' not in attribution:
        return attribution
    plain = {k: v for k, v in attribution.items() if k not in ('instructions_codec', 'instructions_packed')}
    plain['instructions'] = instructions_of(attribution)
    return plain


STRIP = '"$OBJCOPY" --remove-section .mdebug "$OBJECT_OUTPUT"'
CAPTURE = '''# Preserve the original line-bearing object before the existing strip.
{
    cp -- "$OBJECT_OUTPUT" "${OBJECT_OUTPUT%.o}.source-lines.o" &&
    cp -- "$INPUT" "${OBJECT_OUTPUT%.o}.source-lines.input.c" &&
    cp -- "$SOURCE_SNAPSHOT" "${OBJECT_OUTPUT%.o}.source-lines.c" &&
    printf '%s' "$SOURCE_SNAPSHOT" > "${OBJECT_OUTPUT%.o}.source-lines.path" &&
    "$OBJDUMP" -drzl -m mips:4300 "$OBJECT_OUTPUT" > "${OBJECT_OUTPUT%.o}.source-lines.dump"
} || true
'''


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def section_bytes(data: bytes) -> dict:
    """ELF32 big-endian section contents; called after object_image validation."""
    header = struct.unpack_from('>HHIIIIIHHHHHH', data, 16)
    rows = [struct.unpack_from('>IIIIIIIIII', data, header[5] + i * 40)
            for i in range(header[11])]
    names_row = rows[header[12]]
    names = data[names_row[4]:names_row[4] + names_row[5]]
    result = {}
    for row in rows:
        finish = names.find(b'\0', row[0])
        name = names[row[0]:finish].decode()
        if row[2] & 2 and row[1] == 1:
            result[name] = (row[3], data[row[4]:row[4] + row[5]])
    return result


def prepare(ws: Path, name: str, script: Path) -> Path:
    """Adapt a private helper copy under the existing workspace scoring lock."""
    for suffix in ('o', 'c', 'input.c', 'path', 'dump', 'json'):
        (ws / f'{name}.source-lines.{suffix}').unlink(missing_ok=True)
    if not script.is_file():
        return script
    original = script.read_text()
    if original.splitlines().count(STRIP) != 1:
        return script
    modified = original.replace(STRIP, CAPTURE + STRIP)
    output = ws / f'.source-lines-{sha(modified)[:20]}.sh'
    if output.exists():
        if output.read_text() != modified:
            raise ValueError('source attribution helper identity changed')
    else:
        output.write_text(modified)
    return output


def parse_dump(text: str) -> tuple[list[dict], list[str]]:
    """Read objdump's *post-assembly* address-to-line records, including delays."""
    records, raw = [], ['']  # production objdump.py skips its first header row
    section = None
    file, line = None, None
    for entry in text.splitlines():
        match = re.match(r'Disassembly of section (.+):$', entry)
        if match:
            section, file, line = match[1], None, None
            continue
        if re.match(r'^[0-9a-fA-F]+ <.*>:$', entry):
            file, line = None, None
            continue
        if re.match(r'^.+:\?(?:\s.*)?$', entry.strip()):
            file, line = None, None
            continue
        match = re.match(r'^(.+?):(\d+)(?:\s+\(discriminator \d+\))?$', entry.strip())
        if match:
            file, line = match[1], int(match[2])
            continue
        match = re.match(r'^\s*([0-9a-fA-F]+):\s+([0-9a-fA-F]{8})\s+(.+)$', entry)
        if match:
            records.append({'section': section, 'address': int(match[1], 16),
                            'bytes': match[2].lower(), 'file': file, 'line': line,
                            'instruction': match[3].strip()})
            raw.append(entry)
        elif re.match(r'^\s*[0-9a-fA-F]+:\s+R_MIPS_', entry):
            raw.append(entry)
    return records, raw


def collect(ws: Path, name: str, source: str, compile_source: str, diff: str) -> dict:
    result = {'source_sha256': sha(source), 'diff_sha256': sha(diff),
              'kind': 'original-object-compiler-line-attribution',
              'status': 'unavailable', 'instructions': []}
    try:
        debug = ws / f'{name}.source-lines.o'
        final = ws / f'{name}.o'
        if not debug.exists():
            result['reason'] = 'no pre-strip line-bearing object captured'
            return result
        before, after = debug.read_bytes(), final.read_bytes()
        result['line_object_sha256'] = byte_certificate.digest(before)
        result['candidate_object_sha256'] = byte_certificate.digest(after)
        left, right = byte_certificate.object_image(before), byte_certificate.object_image(after)
        # Stronger than textual disassembly equality: section bytes, addresses
        # relative to the section, sizes, alignment and relocations are unchanged.
        if left != right:
            raise ValueError('pre-strip and final allocated object images differ')
        captured_input = (ws / f'{name}.source-lines.input.c').read_text()
        if captured_input != compile_source:
            raise ValueError('captured build input differs from current compiler source')
        snapshot = (ws / f'{name}.source-lines.c').read_text()
        result['compile_source_sha256'] = sha(captured_input)
        result['converted_source_sha256'] = sha(snapshot)
        if len(snapshot.splitlines()) != len(compile_source.splitlines()):
            raise ValueError('source conversion changed line topology')
        if re.search(r'(?m)^\s*#\s*(?:line\b|\d+)', source):
            raise ValueError('user source remaps its own line numbers')
        snapshot_path = (ws / f'{name}.source-lines.path').read_text().strip()
        # The only accepted synthetic filename is the wrapper's explicit
        # #line 1 "candidate.c" directly preceding the unchanged user source.
        wrapped = compile_source.endswith('#line 1 "candidate.c"\n' + source)
        if compile_source != source and not wrapped:
            raise ValueError('unrecognized compiler source wrapper')
        dump = (ws / f'{name}.source-lines.dump').read_text()
        result['line_dump_sha256'] = sha(dump)
        records, raw = parse_dump(dump)
        spec = importlib.util.spec_from_file_location('_production_objdump', ws / 'objdump.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        normalized = module.process_objdump_lines(raw)
        expected = (ws / f'{name}_object_dump_normalized.s').read_text().splitlines()
        if normalized != expected or len(normalized) > len(records):
            raise ValueError('line-bearing instruction stream does not reproduce production normalization')
        for trailing in records[len(normalized):]:
            if trailing['instruction'].split()[0] != 'nop':
                raise ValueError('normalizer omitted a non-padding instruction')
        # Independently bind every address/word to the final allocated bytes.
        contents = section_bytes(after)
        for row in records:
            section = contents.get(row['section'])
            if section is None:
                raise ValueError('disassembled section absent from allocated image')
            vma, data = section
            offset = row['address'] - vma
            if offset < 0 or data[offset:offset + 4].hex() != row['bytes']:
                raise ValueError('line-bearing instruction address/bytes do not match final object')
        for number, row in enumerate(records[:len(normalized)], 1):
            allowed_file = (row['file'] == 'candidate.c' if wrapped else
                            row['file'] == snapshot_path)
            mapped = allowed_file and row['line'] is not None and 1 <= row['line'] <= len(source.splitlines())
            result['instructions'].append({**row, 'normalized_line': number,
                                           'candidate_line': row['line'] if mapped else None,
                                           'evidence': 'direct-compiler-line' if mapped else 'unmapped-or-external'})
        count = sum(r['candidate_line'] is not None for r in result['instructions'])
        result.update(status='verified' if count else 'no-candidate-line-records',
                      directly_mapped=count, instruction_count=len(result['instructions']),
                      allocated_image_identical=True,
                      normalizer_sha256=byte_certificate.digest((ws / 'objdump.py').read_bytes()))
    except (OSError, ValueError, KeyError, IndexError, ImportError, AttributeError, struct.error) as exc:
        result.update(status='unavailable', reason=str(exc), instructions=[])
    return result
