"""ROM-backed certificate schema 3: rodata the function reads and absolute-address literals.

Motivating residuals (2026-09-14 failure census), reproduced on synthetic objects:
- waitForTitleDemoRaceIntroStart: the candidate's own `.rodata` float where the asm-built
  target relocates against its annotated `.late_rodata` dlabel (whole-ROM verified);
- drawTitleScreenStartPrompt: a candidate extern named like the target's rodata label;
- __osSiRawStartDma: `lui 0xa480` literals where the target names SI_DRAM_ADDR_REG,
  which the symbol file does not define (whole-ROM verified);
- initMainMenuSettings: `_593D10_ROM_START` segment bounds, which the real link defines.
Declined: drawRaceSplitscreenSelectEntryFee's own string literal, which passed the
function check but failed the whole-ROM checksum.
"""
import struct

import pytest

from solver import function_boundary as boundary

FUNCTION = 0x80001000
WORDS = [0x3C018000, 0xC4241020, 0x03E00008, 0x00000000]   # lui at,0x8000; lwc1 $f4,0x1020(at); jr ra; nop
DATUM = bytes.fromhex("3FAAAAAB")                           # 1.3333334f, as waitForTitleDemoRaceIntroStart
STRING_WORDS = [0x3C048000, 0x24841020, 0x03E00008, 0x00000000]  # lui a0,0x8000; addiu a0,a0,0x1020
HARDWARE = [0x3C04A480, 0x24840004, 0x03E00008, 0x00000000]  # lui a0,0xa480; addiu a0,a0,4


def elf(text, sections=(), symbols=(), relocations=(), data_relocations=()):
    """sections: [(name, bytes)]; symbols: [(name, value, size, info, section_name|None)];
    relocations: [(offset, kind, symbol_index)] against .text (symbol index counts from 1)."""
    names = [".text"] + [name for name, _ in sections] + [".rel.text"] + ([".rel.rodata"] if data_relocations else []) \
        + [".symtab", ".strtab", ".shstrtab"]
    shstr = b"\0" + b"".join(n.encode() + b"\0" for n in names)
    index = {name: i + 1 for i, name in enumerate(names)}
    strtab = bytearray(b"\0")
    symtab = bytearray(b"\0" * 16)
    for name, value, size, info, section in symbols:
        offset = len(strtab) if name else 0
        if name:
            strtab += name.encode() + b"\0"
        symtab += struct.pack(">IIIBBH", offset, value, size, info, 0, index[section] if section else 0)
    rel = b"".join(struct.pack(">II", at, (sym << 8) | kind) for at, kind, sym in relocations)
    data_rel = b"".join(struct.pack(">II", at, (sym << 8) | kind) for at, kind, sym in data_relocations)
    rows, payload = [(0,) * 10], bytearray(b"\0" * 52)
    specs = [(".text", 1, 6, text, 0, 0, 16, 0)]
    specs += [(name, 1, 2, content, 0, 0, 4, 0) for name, content in sections]
    specs += [(".rel.text", 9, 0, rel, index[".symtab"], index[".text"], 4, 8)]
    if data_relocations:
        specs += [(".rel.rodata", 9, 0, data_rel, index[".symtab"], index[".rodata"], 4, 8)]
    specs += [(".symtab", 2, 0, bytes(symtab), index[".strtab"], 1, 4, 16),
              (".strtab", 3, 0, bytes(strtab), 0, 0, 1, 0), (".shstrtab", 3, 0, shstr, 0, 0, 1, 0)]
    for name, kind, flags, content, link, info, align, entry in specs:
        rows.append((shstr.index(name.encode() + b"\0"), kind, flags, 0, len(payload), len(content),
                     link, info, align, entry))
        payload.extend(content)
    table = len(payload)
    for row in rows:
        payload.extend(struct.pack(">IIIIIIIIII", *row))
    payload[:16] = b"\x7fELF\x01\x02\x01" + b"\0" * 9
    struct.pack_into(">HHIIIIIHHHHHH", payload, 16, 1, 8, 1, 0, 0, table, 0, 52, 0, 0, 40, len(rows), index[".shstrtab"])
    return bytes(payload)


def unlinked(words):
    """Object words carry REL addends only: zero the HI16/LO16 immediates."""
    return b"".join(w.to_bytes(4, "big") for w in [words[0] & 0xFFFF0000, words[1] & 0xFFFF0000] + words[2:])


def target_object():
    return elf(unlinked(WORDS), sections=[(".rodata", DATUM)],
               symbols=[("f", 0, 16, 18, ".text"), ("D_80001020", 0, 4, 1, ".rodata")],
               relocations=[(0, 5, 2), (4, 6, 2)])


@pytest.fixture
def inputs(tmp_path):
    paths = {key: tmp_path / name for key, name in dict(target="target.o", candidate="candidate.o",
             assembly="target.s", rom="rom.z64", config="config.yaml", symbols="symbols.txt").items()}
    paths["target"].write_bytes(target_object())
    asm = [".section .rodata", "dlabel D_80001020", '    /* 20 80001020 */ .asciz "Hi!"', "enddlabel D_80001020",
           ".section .text", "glabel f"]
    asm += [f"/* {i * 4:X} {FUNCTION + i * 4:08X} {w:08X} */ insn" for i, w in enumerate(WORDS)]
    asm += ["endlabel f"]
    paths["assembly"].write_text("\n".join(asm) + "\n")
    paths["rom"].write_bytes(b"".join(w.to_bytes(4, "big") for w in WORDS) + b"\0" * 16 + DATUM + b"\0" * 92)
    paths["config"].write_text("segments:\n  - {type: code, start: 0, vram: 0x80001000}\n")
    paths["symbols"].write_text("f = 0x80001000;\n")
    return dict(**paths, function="f", address=FUNCTION, size=16)


def literal_candidate(datum=DATUM, padding=12, data_relocations=()):
    return elf(unlinked(WORDS), sections=[(".rodata", datum + b"\0" * padding)],
               symbols=[("f", 0, 16, 18, ".text"), ("", 0, 0, 3, ".rodata")],
               relocations=[(0, 5, 2), (4, 6, 2)], data_relocations=data_relocations)


def test_candidate_rodata_literal_certifies_against_the_rom_datum(inputs):
    inputs["candidate"].write_bytes(literal_candidate())
    result = boundary.certify(**inputs)
    assert result["function_exact"] is True and result["schema_version"] == 3
    assert result["data_sites"] == [{"offsets": [0, 4], "target_address": 0x80001020, "candidate_section": ".rodata",
                                     "candidate_offset": 0, "length": 4}]
    assert "rodata placement after integration" in result["excluded"]


def test_candidate_extern_named_like_the_target_label_certifies(inputs):
    inputs["candidate"].write_bytes(elf(unlinked(WORDS), symbols=[("f", 0, 16, 18, ".text"), ("D_80001020", 0, 0, 16, None)],
                                        relocations=[(0, 5, 2), (4, 6, 2)]))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is True and result["data_sites"] == [] and result["absolute_literals"] == []


def test_absolute_hardware_literal_certifies_where_the_target_names_an_undefined_register(inputs):
    inputs["target"].write_bytes(elf(unlinked(HARDWARE), symbols=[("f", 0, 16, 18, ".text"), ("SI_PIF_REG", 0, 0, 16, None)],
                                     relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["candidate"].write_bytes(elf(b"".join(w.to_bytes(4, "big") for w in HARDWARE),
                                        symbols=[("f", 0, 16, 18, ".text")]))
    inputs["rom"].write_bytes(b"".join(w.to_bytes(4, "big") for w in HARDWARE) + b"\0" * 124)
    inputs["assembly"].write_text("glabel f\n" + "".join(f"/* {i * 4:X} {FUNCTION + i * 4:08X} {w:08X} */ insn\n"
                                                        for i, w in enumerate(HARDWARE)) + "endlabel f\n")
    result = boundary.certify(**inputs)
    assert result["function_exact"] is True
    assert result["absolute_literals"] == [{"offsets": [0, 4], "symbol": "SI_PIF_REG", "address": 0xA4800004}]


def test_declines_a_candidate_datum_that_differs_from_the_rom(inputs):
    inputs["candidate"].write_bytes(literal_candidate(datum=bytes.fromhex("4E9EFBC4")))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is False and "differs from ROM" in result["schema_3_error"]


def test_declines_nonzero_candidate_bytes_beyond_the_target_extent(inputs):
    inputs["candidate"].write_bytes(literal_candidate(datum=DATUM + b"XY", padding=10))
    assert boundary.certify(**inputs)["function_exact"] is False


def test_declines_a_literal_for_an_address_inside_a_file_backed_segment(inputs):
    # The target names an undefined external at 0x80001020 (mapped): a literal there is not an absolute address.
    inputs["target"].write_bytes(elf(unlinked(WORDS), symbols=[("f", 0, 16, 18, ".text"), ("gData", 0, 0, 16, None)],
                                     relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["candidate"].write_bytes(elf(b"".join(w.to_bytes(4, "big") for w in WORDS), symbols=[("f", 0, 16, 18, ".text")]))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is False and "file-backed segment" in result["schema_3_error"]


def test_declines_candidate_rodata_the_function_does_not_read(inputs):
    inputs["candidate"].write_bytes(elf(unlinked(WORDS), sections=[(".rodata", DATUM + b"\0\0\0\0" + b"late")],
                                        symbols=[("f", 0, 16, 18, ".text"), ("", 0, 0, 3, ".rodata")],
                                        relocations=[(0, 5, 2), (4, 6, 2)]))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is False and "does not read" in result["schema_3_error"]


def test_declines_relocated_data_such_as_jump_tables(inputs):
    inputs["candidate"].write_bytes(literal_candidate(data_relocations=[(0, 2, 1)]))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is False and "outside schema 3" in result["schema_3_error"]


def test_workspace_asks_the_certificate_only_for_operand_only_differences():
    from solver import workspace
    rodata = "@@ -4,2 +4,2 @@\n-lui    at,%hi(D_800E08B8)\n-lwc1    $f4,%lo(D_800E08B8)(at)\n+lui    at,%hi(.rodata)\n+lwc1    $f4,%lo(.rodata)(at)\n li    t6,0xd0\n"
    assert workspace.operand_only_diff(rodata)
    moved = "@@ -1,3 +1,3 @@\n lbu    t6,0(a1)\n-addiu    v0,a1,1\n sh    zero,0xc2(a0)\n+addiu    v0,a1,1\n"
    assert not workspace.operand_only_diff(moved)
    reshaped = "@@\n-lui    a0,%hi(D_593D10)\n-addiu    a0,a0,%lo(D_593D10)\n+lui    a0,0x59\n+ori    a0,a0,0x3d10\n"
    assert not workspace.operand_only_diff(reshaped)
    assert not workspace.operand_only_diff("")


def test_declines_an_address_taken_string_literal_in_candidate_rodata(inputs):
    # drawRaceSplitscreenSelectEntryFee passed an earlier form of this check and then failed
    # the whole-ROM checksum: a string's placement in the real TU differs. It needs the symbol.
    string = b"Hi!\0"
    image = b"".join(w.to_bytes(4, "big") for w in STRING_WORDS)
    inputs["target"].write_bytes(elf(unlinked(STRING_WORDS), sections=[(".rodata", string)],
                                     symbols=[("f", 0, 16, 18, ".text"), ("D_80001020", 0, 4, 1, ".rodata")],
                                     relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["candidate"].write_bytes(elf(unlinked(STRING_WORDS), sections=[(".rodata", string + b"\0" * 12)],
                                        symbols=[("f", 0, 16, 18, ".text"), ("", 0, 0, 3, ".rodata")],
                                        relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["rom"].write_bytes(image + b"\0" * 16 + string + b"\0" * 92)
    inputs["assembly"].write_text(inputs["assembly"].read_text().replace("C4241020", "24841020").replace("3C018000", "3C048000"))
    result = boundary.certify(**inputs)
    assert result["function_exact"] is False and "named symbol" in result["schema_3_error"]


def test_segment_bound_symbols_resolve_from_the_segment_config(inputs):
    # `(void *)&_593D10_ROM_START` links in the real build; splat's `D_593D10` does not
    # (initMainMenuSettings integration: undefined reference, 2026-09-14).
    words = [0x3C040059, 0x24843D10, 0x03E00008, 0x00000000]   # lui a0,0x59; addiu a0,a0,0x3d10
    inputs["target"].write_bytes(elf(unlinked(words), symbols=[("f", 0, 16, 18, ".text"), ("D_593D10", 0, 0, 16, None)],
                                     relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["candidate"].write_bytes(elf(unlinked(words), symbols=[("f", 0, 16, 18, ".text"),
                                                                  ("_593D10_ROM_START", 0, 0, 16, None)],
                                        relocations=[(0, 5, 2), (4, 6, 2)]))
    inputs["rom"].write_bytes(b"".join(w.to_bytes(4, "big") for w in words) + b"\0" * 124)
    inputs["assembly"].write_text("glabel f\n" + "".join(f"/* {i * 4:X} {FUNCTION + i * 4:08X} {w:08X} */ insn\n"
                                                        for i, w in enumerate(words)) + "endlabel f\n")
    inputs["config"].write_text("segments:\n  - {type: code, start: 0, vram: 0x80001000}\n"
                                "  - {type: bin, start: 0x593D10, name: _593D10, vram: 0}\n  - [0x598A70]\n")
    result = boundary.certify(**inputs)
    assert result["function_exact"] is True
    assert boundary._segment_symbols({"segments": [{"start": 0x593D10, "name": "_593D10"}, [0x598A70]]}, 0x800000) == {
        "_593D10_ROM_START": 0x593D10, "_593D10_ROM_END": 0x598A70}
