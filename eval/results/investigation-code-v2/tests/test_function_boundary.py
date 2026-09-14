import struct
import json
import sqlite3

import pytest

from solver import byte_certificate, function_boundary as boundary


def elf(text, *, size=16, symbol="callee", relocation=0, data_section=b"", relocations=None):
    names = b"\0.text\0.rodata\0.rel.text\0.symtab\0.strtab\0.shstrtab\0"
    strings = b"\0f\0" + symbol.encode() + b"\0"
    symbols = (b"\0" * 16 + struct.pack(">IIIBBH", 1, 0, size, 18, 0, 1)
               + struct.pack(">IIIBBH", 3, 0, 0, 16, 0, 0))
    rows, payload = [(0,) * 10], bytearray(b"\0" * 52)
    for name, kind, flags, content, link, info, align, entry in (
        (".text", 1, 6, text, 0, 0, 16, 0),
        (".rodata", 1, 2, data_section, 0, 0, 4, 0),
        (".rel.text", 9, 0, b"".join(struct.pack(">II", at, (2 << 8) | kind)
          for at, kind in (relocations if relocations is not None else [(relocation, 4)])), 4, 1, 4, 8),
        (".symtab", 2, 0, symbols, 5, 1, 4, 16),
        (".strtab", 3, 0, strings, 0, 0, 1, 0),
        (".shstrtab", 3, 0, names, 0, 0, 1, 0),
    ):
        rows.append((names.index(name.encode()), kind, flags, 0, len(payload),
                     len(content), link, info, align, entry))
        payload.extend(content)
    offset = len(payload)
    for row in rows:
        payload.extend(struct.pack(">IIIIIIIIII", *row))
    payload[:16] = b"\x7fELF\x01\x02\x01" + b"\0" * 9
    struct.pack_into(">HHIIIIIHHHHHH", payload, 16, 1, 8, 1, 0, 0, offset, 0,
                     52, 0, 0, 40, len(rows), 6)
    return bytes(payload)


TEXT = bytes.fromhex("0c0000000000000003e0000800000000")


@pytest.fixture
def inputs(tmp_path):
    paths = {key: tmp_path / name for key, name in dict(target="target.o", candidate="candidate.o",
        assembly="target.s", rom="rom.z64", config="config.yaml", symbols="symbols.txt").items()}
    paths["target"].write_bytes(elf(TEXT + b"\0" * 16))
    paths["candidate"].write_bytes(elf(TEXT))
    words = [0x0c000410, 0, 0x03e00008, 0, 0, 0, 0]
    asm = ["glabel f"]
    for i, word in enumerate(words):
        if i == 4:
            asm.append("endlabel f")
        asm.append(f"/* {16+i*4:X} {0x80001000+i*4:08X} {word:08X} */ " +
                   ("nop" if not word else "jal callee" if i == 0 else "jr $ra"))
    paths["assembly"].write_text("\n".join(asm) + "\n")
    paths["rom"].write_bytes(b"\0" * 16 + b"".join(w.to_bytes(4, "big") for w in words) + b"\0" * 84)
    paths["config"].write_text("segments:\n  - {type: code, start: 0, vram: 2147487728}\n")
    paths["symbols"].write_text("f = 0x80001000;\ncallee = 0x80001040;\n")
    return dict(**paths, function="f", address=0x80001000, size=16)


def test_exact_function_is_not_exact_object_or_rom(inputs):
    report = boundary.certify(**inputs)
    assert report.get("function_exact"), report
    assert report["annotated_trailing_bytes"] == 12
    assert report["assembler_alignment_bytes"] == 4
    assert report["target_trailing_bytes"] == 16
    assert not report["whole_rom_verified"]
    assert not byte_certificate.certify(inputs["target"], inputs["candidate"], source="C")["exact"]
    assert boundary.revalidate(report)
    inputs["rom"].write_bytes(b"tampered")
    assert not boundary.revalidate(report)


@pytest.mark.parametrize("change", ["function_byte", "tail_byte", "symbol_size", "wrong_callee",
    "relocation_in_tail", "rodata", "rom", "map", "metadata", "endlabel", "tail_directive"])
def test_declines_unproven_boundaries_and_mismatches(inputs, change):
    if change == "function_byte":
        inputs["candidate"].write_bytes(elf(TEXT[:8] + b"abcd" + TEXT[12:]))
    elif change == "tail_byte":
        inputs["target"].write_bytes(elf(TEXT + b"x" + b"\0" * 15))
    elif change == "symbol_size":
        inputs["candidate"].write_bytes(elf(TEXT, size=12))
    elif change == "wrong_callee":
        inputs["candidate"].write_bytes(elf(TEXT, symbol="wrong"))
    elif change == "relocation_in_tail":
        for key in ("target", "candidate"):
            inputs[key].write_bytes(elf(TEXT + b"\0" * 16, relocation=16))
    elif change == "rodata":
        for key in ("target", "candidate"):
            inputs[key].write_bytes(elf(TEXT, data_section=b"data"))
    elif change == "rom":
        inputs["rom"].write_bytes(b"x" * 128)
    elif change == "map":
        inputs["config"].write_text("segments: []")
    elif change == "metadata":
        inputs["size"] = 12
    elif change == "endlabel":
        inputs["assembly"].write_text(inputs["assembly"].read_text().replace("endlabel f", "endlabel other"))
    elif change == "tail_directive":
        with_text = inputs["assembly"].read_text() + ".word 0\n"
        inputs["assembly"].write_text(with_text)
    assert not boundary.certify(**inputs)["function_exact"]


def test_scoped_integration_replays_boundary_and_checks_source_lineage(inputs, tmp_path):
    from eval import prepare_integration as prep
    (tmp_path / "src").mkdir()
    (tmp_path / "src/f.c").write_text("int f(void) {return 0;}\n")
    candidate = inputs["candidate"].with_suffix(".c")
    candidate.write_text("int f(void) {return 1;}\n")
    verification = byte_certificate.certify(inputs["target"], inputs["candidate"], source=candidate.read_text())
    verification["function_boundary"] = boundary.certify(**inputs)
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE functions(name,addr,size,tu_id); CREATE TABLE tus(id,name); "
                           "CREATE TABLE attempts(id,func_addr,source_code);")
        conn.execute("INSERT INTO functions VALUES ('f',?,16,1)", (inputs["address"],))
        conn.execute("INSERT INTO tus VALUES(1,'build/src/f.o')")
        conn.execute("INSERT INTO attempts VALUES(1,?,?)", (inputs["address"], candidate.read_text()))
    entry = dict(function="f", attempt_id=1, source=str(candidate), verification=verification)
    kwargs = dict(repo=tmp_path, db=db, entries=[entry], reference_rom="rom.z64")
    manifest = prep.prepare(**kwargs, output_dir=tmp_path / "prepared")
    assert json.loads(manifest.read_text())["lineage"][0]["admission_scope"] == "function_extent_only"
    assert not verification["exact"]
    assert (tmp_path / "src/f.c").read_text() == "int f(void) {return 0;}\n"
    inputs["target"].write_bytes(elf(TEXT))
    with pytest.raises(ValueError, match="source-bound"):
        prep.prepare(**kwargs, output_dir=tmp_path / "tampered")


@pytest.mark.parametrize("symbol,low,expected_hi,expected_lo", [
    (0x80008000, 0, 0x8001, 0x8000),
    (0x80001040, 0xfffc, 0x8000, 0x103c),
])
def test_rom_certifies_reordered_pairs_and_signed_addends(inputs, symbol, low, expected_hi, expected_lo):
    words = [0x3c080000, 0x25080000 | low, 0x0c000000, 0]
    text = b"".join(w.to_bytes(4, "big") for w in words)
    inputs['target'].write_bytes(elf(text + b'\0' * 16, relocations=[(0, 5), (4, 6), (8, 4)]))
    inputs['candidate'].write_bytes(elf(text, relocations=[(8, 4), (0, 5), (4, 6)]))
    linked = [0x3c080000 | expected_hi, 0x25080000 | expected_lo,
              0x0c000000 | ((symbol >> 2) & 0x03ffffff), 0, 0, 0, 0]
    asm = ['glabel f']
    for i, w in enumerate(linked):
        if i == 4:
            asm.append('endlabel f')
        asm.append(f'/* {16+i*4:X} {0x80001000+i*4:08X} {w:08X} */ ' + ('nop' if not w else 'instruction'))
    inputs['assembly'].write_text('\n'.join(asm) + '\n')
    inputs['rom'].write_bytes(b'\0' * 16 + b''.join(w.to_bytes(4, 'big') for w in linked) + b'\0' * 84)
    inputs['symbols'].write_text(f'f = 0x80001000;\ncallee = {symbol:#x};\n')
    report = boundary.certify(**inputs)
    assert report['function_exact'], report
    assert report['schema_version'] == 2
    assert boundary.revalidate(report)
    inputs['symbols'].write_text(f'f = 0x80001000;\ncallee = {symbol+16:#x};\n')
    assert not boundary.certify(**inputs)['function_exact']
    assert not boundary.revalidate(report)


EXT = ('external', 'x', 1, 0)
LOCAL = ('section', '.text', 0, 1, 0)


@pytest.mark.parametrize('rows', [
    [(0, 5, EXT)], [(0, 6, EXT)], [(0, 5, EXT), (4, 5, EXT), (8, 6, EXT)],
    [(0, 5, EXT), (4, 6, LOCAL)], [(0, 2, EXT), (0, 4, EXT)],
    [(2, 2, EXT)], [(16, 2, EXT)], [(0, 7, EXT)],
    [(0, 2, ('section', '.rodata', 0, 1, 0))],
])
def test_declines_unsupported_relocation_groups(rows):
    with pytest.raises(ValueError):
        boundary.relocation_groups(rows, 16)


def test_scalar_and_local_resolution_and_rejections():
    groups = boundary.relocation_groups([(0, 2, EXT)], 4)
    assert boundary.relocate(bytes.fromhex('fffffffc'), groups, {'x': 0x80001000}, 0x80001000).hex() == '80000ffc'
    with pytest.raises(ValueError, match='unresolved'):
        boundary.relocate(b'\0' * 4, groups, {}, 0x80001000)
    local = boundary.relocation_groups([(0, 5, LOCAL), (4, 6, LOCAL)], 8)
    assert boundary.relocate(bytes.fromhex('3c08000025080004'), local, {}, 0x80001000).hex() == '3c08800025081004'
    with pytest.raises(ValueError, match='escapes'):
        boundary.relocate(bytes.fromhex('3c08000025080008'), local, {}, 0x80001000)
    jump = boundary.relocation_groups([(0, 4, EXT)], 4)
    for value in (0x90000000, 0x80001001):
        with pytest.raises(ValueError):
            boundary.relocate(bytes.fromhex('0c000000'), jump, {'x': value}, 0x80001000)


@pytest.mark.parametrize('different_addends', [False, True])
def test_each_objects_hi_lo_pairing_is_resolved_independently(inputs, different_addends):
    # Motivating real residual: waitEndingTommyPhase39 swaps two HI partners.
    # Equal low addends are equivalent; different low addends across a carry are not.
    words = [0x3c080000, 0x3c090000, 0x25080000, 0x25290000 | (0x8000 if different_addends else 0)]
    raw = b''.join(w.to_bytes(4, 'big') for w in words)
    inputs['target'].write_bytes(elf(raw + b'\0' * 16, relocations=[(0, 5), (8, 6), (4, 5), (12, 6)]))
    inputs['candidate'].write_bytes(elf(raw, relocations=[(4, 5), (8, 6), (0, 5), (12, 6)]))
    inputs['symbols'].write_text('f = 0x80001000;\ncallee = 0x80008000;\n')
    # Independently specified target-link bytes across a signed-low carry.
    linked = [0x3c088001, 0x3c098001 if not different_addends else 0x3c098000,
              0x25088000, 0x25298000 if not different_addends else 0x25290000, 0, 0, 0]
    asm = ['glabel f']
    for i, w in enumerate(linked):
        if i == 4:
            asm.append('endlabel f')
        asm.append(f'/* {16+i*4:X} {0x80001000+i*4:08X} {w:08X} */ ' + ('nop' if not w else 'instruction'))
    inputs['assembly'].write_text('\n'.join(asm) + '\n')
    inputs['rom'].write_bytes(b'\0' * 16 + b''.join(w.to_bytes(4, 'big') for w in linked) + b'\0' * 84)
    result = boundary.certify(**inputs)
    assert result['function_exact'] is not different_addends, result
