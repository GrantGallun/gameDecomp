"""Narrow ROM-backed function extent certificate, separate from object exactness.

Only a single text function, with zero-only trailing bytes and bounded REL
relocations, is supported. No padding is deleted or added to either object.
The annotated extent is checked against both ELF symbols, DB metadata, segment
mapping and ROM bytes. Whole-TU padding remains an integration obligation.
"""
from __future__ import annotations

from pathlib import Path
import re
import struct

from solver import byte_certificate as cert
from solver.sdk_intake import rom_offset


def relocation_groups(rows: list, size: int) -> tuple:
    """Keep adjacent HI/LO pairs intact; reorder only disjoint groups.

    Deliberately excludes GNU multi-HI extensions and orphan LO16 entries.
    ABI pairing reference: https://sourceware.org/pipermail/binutils/2023-February/125959.html
    """
    groups, seen, index = [], set(), 0
    while index < len(rows):
        at, kind, identity = rows[index]
        identity = tuple(identity)
        if not (identity[0] == "external" or
                identity[0] == "section" and identity[1] == ".text" and identity[2] == 0):
            raise ValueError("unsupported relocation symbol")
        if kind not in {2, 4, 5}:
            raise ValueError("unsupported or orphan relocation")
        group = [(at, kind, identity)]
        if kind == 5:
            if index + 1 >= len(rows):
                raise ValueError("orphan HI16")
            low, low_kind, low_identity = rows[index + 1]
            if low_kind != 6 or tuple(low_identity) != identity:
                raise ValueError("requires adjacent same-symbol HI16/LO16 pair")
            group.append((low, low_kind, identity))
            index += 1
        for offset, _, _ in group:
            if offset < 0 or offset % 4 or offset + 4 > size or offset in seen:
                raise ValueError("overlapping or out-of-function relocation")
            seen.add(offset)
        groups.append(tuple(group))
        index += 1
    return tuple(sorted(groups))


def relocate(text: bytes, groups: tuple, addresses: dict, address: int) -> bytes:
    """Resolve supported REL groups from original words, never patched addends."""
    linked = bytearray(text)
    def word(at):
        return int.from_bytes(text[at:at + 4], "big")
    def put(at, value):
        linked[at:at + 4] = (value & 0xffffffff).to_bytes(4, "big")
    for group in groups:
        at, kind, identity = group[0]
        local = identity[0] == "section"
        symbol = address if local else addresses.get(identity[1])
        if symbol is None or not 0 <= symbol <= 0xffffffff:
            raise ValueError("unresolved or invalid symbol address")
        value = word(at)
        if kind == 5:
            low = group[1][0]
            low_word = word(low)
            imm = low_word & 0xffff
            addend = ((value & 0xffff) << 16) + (imm - 0x10000 if imm & 0x8000 else imm)
            destination = (symbol + addend) & 0xffffffff
            put(at, (value & 0xffff0000) | (((destination + 0x8000) >> 16) & 0xffff))
            put(low, (low_word & 0xffff0000) | (destination & 0xffff))
        elif kind == 4:
            destination = symbol + ((value & 0x03ffffff) << 2)
            if value >> 26 != 3 or destination % 4 or not 0 <= destination <= 0xffffffff:
                raise ValueError("requires aligned jal destination")
            if (destination ^ (address + at + 4)) & 0xf0000000:
                raise ValueError("call crosses MIPS jump region")
            put(at, (value & 0xfc000000) | ((destination >> 2) & 0x03ffffff))
        else:  # R_MIPS_32
            destination = (symbol + value) & 0xffffffff
            put(at, destination)
        if local and not address <= destination < address + len(text):
            raise ValueError("local relocation escapes certified function")
    return bytes(linked)


def text_extent(data: bytes, function: str) -> tuple[bytes, int]:
    # Validate the common ELF structure/relocations first.
    cert.object_image(data)
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    rows = [struct.unpack_from(">IIIIIIIIII", data, header[5] + i * 40)
            for i in range(header[11])]
    def contents(row):
        return data[row[4]:row[4] + row[5]]
    def string(table, offset):
        end = table.find(b"\0", offset)
        if not 0 <= offset <= end:
            raise ValueError("invalid symbol string")
        return table[offset:end].decode()
    names = contents(rows[header[12]])
    text_index, = [i for i, row in enumerate(rows) if string(names, row[0]) == ".text"]
    functions = []
    for row in rows:
        if row[1] != 2:
            continue
        if row[9] != 16 or row[5] % 16:
            raise ValueError("unsupported symbol table")
        strings = contents(rows[row[6]])
        for name, value, size, info, other, section in struct.iter_unpack(">IIIBBH", contents(row)):
            if section == text_index and info & 15 == 2:
                functions.append((string(strings, name), value, size))
    if len(functions) != 1 or functions[0][:2] != (function, 0) or functions[0][2] <= 0:
        raise ValueError("expected one named function at text offset zero with explicit size")
    text = contents(rows[text_index])
    size = functions[0][2]
    if size > len(text) or size % 4 or any(text[size:]):
        raise ValueError("invalid extent or nonzero bytes outside function")
    return text, size


def certify(*, target: Path, candidate: Path, assembly: Path, rom: Path,
            config: Path, symbols: Path, function: str, address: int, size: int) -> dict:
    result = {"kind": "rom_backed_function_extent", "schema_version": 1,
              "function": function, "address": address, "size": size,
              "function_exact": False, "whole_rom_verified": False,
              "scope": "annotated function bytes and external call relocations only",
              "excluded": ["trailing text bytes", "TU/link layout", "whole ROM"],
              "requires_isolated_integration": True}
    try:
        import yaml
        paths = dict(target=target, candidate=candidate, assembly=assembly, rom=rom,
                     config=config, symbols=symbols)
        data = {key: path.read_bytes() for key, path in paths.items()}
        result["inputs"] = {key: {"path": str(path), "sha256": cert.digest(data[key])}
                            for key, path in paths.items()}
        left = cert.object_image(data["target"])["sections"]
        right = cert.object_image(data["candidate"])["sections"]
        if set(left) != {".text"} or set(right) != {".text"}:
            raise ValueError("function-only check does not support allocated data/BSS")
        lt, ls = text_extent(data["target"], function)
        rt, rs = text_extent(data["candidate"], function)
        if ls != size or rs != size or lt[:size] != rt[:size]:
            raise ValueError("function sizes or bytes differ")
        for key in ("type", "flags", "alignment"):
            if left[".text"][key] != right[".text"][key]:
                raise ValueError("text attributes or relocation expressions differ")
        left_groups = relocation_groups(left[".text"]["relocations"], size)
        right_groups = relocation_groups(right[".text"]["relocations"], size)
        # Pairings can differ between assemblers even with identical relocation
        # sites. Keep each object's pairing intact and resolve both independently
        # below; equality is for this bound ROM/link environment only.
        if sorted(item for group in left_groups for item in group) != sorted(
                item for group in right_groups for item in group):
            raise ValueError("relocation sites or symbols differ")
        asm = data["assembly"].decode()
        start = re.findall(rf"(?m)^glabel\s+{re.escape(function)}\s*$", asm)
        end = re.findall(rf"(?m)^endlabel\s+{re.escape(function)}\s*$", asm)
        if len(start) != 1 or len(end) != 1:
            raise ValueError("missing unambiguous assembly extent")
        body = re.split(rf"(?m)^glabel\s+{re.escape(function)}\s*$", asm)[1]
        body, tail = re.split(rf"(?m)^endlabel\s+{re.escape(function)}\s*$", body)
        pattern = r"/\*\s*([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s*\*/"
        words = [tuple(int(v, 16) for v in row) for row in re.findall(pattern, body)]
        tail_words = [tuple(int(v, 16) for v in row) for row in re.findall(pattern, tail)]
        if len(words) * 4 != size:
            raise ValueError("assembly annotation does not cover function")
        rom_data = data["rom"]
        try:
            mapping = yaml.safe_load(data["config"])
        except yaml.YAMLError as exc:
            raise ValueError("invalid segment configuration") from exc
        offset = rom_offset(mapping, address, size + len(tail_words) * 4, len(rom_data))
        for i, (file_offset, pc, word) in enumerate(words + tail_words):
            if (file_offset, pc) != (offset + i * 4, address + i * 4):
                raise ValueError("noncontiguous annotation or ROM mapping disagreement")
            if rom_data[file_offset:file_offset + 4] != word.to_bytes(4, "big"):
                raise ValueError("assembly word disagrees with ROM")
        if any(word for _, _, word in tail_words):
            raise ValueError("nonzero annotated trailing words")
        # Only ordinary annotated nops may follow endlabel; reject directives,
        # hidden labels, or instructions without an address/word annotation.
        remaining_tail = re.sub(pattern + r"\s*nop\s*(?:\r?\n|$)", "", tail)
        if remaining_tail.strip():
            raise ValueError("unsupported assembly after endlabel")
        alignment = left[".text"]["alignment"]
        if alignment not in {4, 8, 16}:
            raise ValueError("unsupported text alignment")
        annotated_size = size + len(tail_words) * 4
        if len(lt) != (annotated_size + alignment - 1) // alignment * alignment:
            raise ValueError("unexplained target section extent")
        if len(rt) != (size + alignment - 1) // alignment * alignment:
            raise ValueError("unexplained candidate section extent")
        symbol_rows = re.findall(r"(?m)^\s*([\w.$]+)\s*=\s*(0x[0-9a-fA-F]+)\s*;", data["symbols"].decode())
        addresses = {}
        for name, value in symbol_rows:
            if name in addresses and addresses[name] != int(value, 16):
                raise ValueError("conflicting linker symbol addresses")
            addresses[name] = int(value, 16)
        if addresses.get(function) != address:
            raise ValueError("function symbol address disagrees with metadata")
        linked = relocate(lt[:size], left_groups, addresses, address)
        candidate_linked = relocate(rt[:size], right_groups, addresses, address)
        if linked != candidate_linked or linked != rom_data[offset:offset + size]:
            raise ValueError("relocated function object does not reproduce ROM")
        # Preserve replay equality of legacy certificates for the original subset.
        legacy = (left[".text"]["relocations"] == right[".text"]["relocations"] and
                  all(kind == 4 and identity[0] == "external" and
                      not int.from_bytes(lt[at:at + 4], "big") & 0x03ffffff
                      for at, kind, identity in left[".text"]["relocations"]))
        if not legacy:
            result.update(schema_version=2,
                          scope="annotated function bytes; external and function-local REL relocations",
                          relocation_policy="disjoint R_MIPS_32/jal/intact HI16-LO16 pairs")
        result.update(function_exact=True, status="function_exact_pending_integration",
                      rom_offset=offset, function_bytes=size,
                      target_trailing_bytes=len(lt) - size, candidate_trailing_bytes=len(rt) - size,
                      annotated_trailing_bytes=len(tail_words) * 4,
                      assembler_alignment_bytes=len(lt) - annotated_size)
    except (OSError, ValueError, struct.error, IndexError, KeyError, TypeError, UnicodeError) as exc:
        result.update(status="unverified", error=f"{type(exc).__name__}: {exc}")
        # Schema 3 runs only where schema 2 refuses, so every receipt schema 2
        # accepts replays byte-for-byte (revalidate compares whole receipts).
        if isinstance(exc, ValueError) and str(exc) in V3_FALLBACK:
            try:
                extended = _certify_v3(target=target, candidate=candidate, assembly=assembly, rom=rom,
                                       config=config, symbols=symbols, function=function,
                                       address=address, size=size)
                result.pop("error")
                result.update(extended)
            except (OSError, ValueError, struct.error, IndexError, KeyError, TypeError, UnicodeError) as extended_exc:
                result["schema_3_error"] = f"{type(extended_exc).__name__}: {extended_exc}"
    return result


# Schema 2 refusals that schema 3 re-examines against the ROM.
V3_FALLBACK = {
    "function-only check does not support allocated data/BSS",
    "function sizes or bytes differ",
    "relocation sites or symbols differ",
    "unsupported relocation symbol",
    "unresolved or invalid symbol address",
}
DATA_SECTIONS = frozenset({".rodata", ".late_rodata"})
DATA_LABEL = re.compile(r"(?m)^dlabel\s+(\w+)\s*\n\s*/\*\s*([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})(?:\s+[0-9A-Fa-f]+)?\s*\*/")


def _elf_sections(data: bytes) -> tuple[list, list[str]]:
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    rows = [struct.unpack_from(">IIIIIIIIII", data, header[5] + i * 40) for i in range(header[11])]
    names = data[rows[header[12]][4]:rows[header[12]][4] + rows[header[12]][5]]
    labels = [names[row[0]:names.index(b"\0", row[0])].decode() for row in rows]
    return rows, labels


def _section_bytes(data: bytes, name: str) -> bytes:
    rows, labels = _elf_sections(data)
    matches = [row for row, label in zip(rows, labels) if label == name]
    if len(matches) != 1:
        raise ValueError("missing or duplicate section " + name)
    return data[matches[0][4]:matches[0][4] + matches[0][5]]


def _data_symbols(data: bytes) -> dict[str, list[tuple[int, str]]]:
    """Named non-section symbols inside rodata sections: {section: [(value, name)]}."""
    rows, labels = _elf_sections(data)
    found: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        if row[1] != 2:
            continue
        strings = data[rows[row[6]][4]:rows[row[6]][4] + rows[row[6]][5]]
        for name, value, _size, info, _other, index in struct.iter_unpack(">IIIBBH", data[row[4]:row[4] + row[5]]):
            if not 0 < index < len(labels) or labels[index] not in DATA_SECTIONS or info & 15 == 3:
                continue
            text = strings[name:strings.index(b"\0", name)].decode()
            if text:
                found.setdefault(labels[index], []).append((value, text))
    return {section: sorted(rows) for section, rows in found.items()}


def _segment_symbols(mapping: dict, rom_size: int) -> dict[str, int]:
    """`<name>_ROM_START` / `<name>_ROM_END` for every named segment, as the splat linker script defines them."""
    starts = []
    for segment in mapping.get("segments", []):
        if isinstance(segment, dict):
            start, name = segment.get("start"), segment.get("name")
        elif isinstance(segment, list) and segment:
            start, name = segment[0], (segment[2] if len(segment) > 2 else None)
        else:
            continue
        if isinstance(start, int):
            starts.append((start, name if isinstance(name, str) else None))
    symbols, conflicts = {}, set()
    for index, (start, name) in enumerate(starts):
        if not name:
            continue
        end = next((s for s, _ in starts[index + 1:] if s > start), rom_size)
        for key, value in ((f"{name}_ROM_START", start), (f"{name}_ROM_END", end)):
            if symbols.setdefault(key, value) != value:
                conflicts.add(key)
    return {key: value for key, value in symbols.items() if key not in conflicts}


def _unmapped(mapping: dict, address: int, rom_size: int) -> bool:
    """True when no file-backed code segment could contain the address (an absolute/hardware address)."""
    segments = mapping["segments"]
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or not all(isinstance(segment.get(k), int) for k in ("start", "vram")):
            continue
        following = [s.get("start") if isinstance(s, dict) else s[0] for s in segments[index + 1:]
                     if isinstance(s, (dict, list))]
        end = next((v for v in following if isinstance(v, int)), rom_size)
        offset = address - segment["vram"] + segment["start"]
        if segment["start"] <= offset < min(end, rom_size):
            return False
    return True


def _data_rom_offset(mapping: dict, address: int, length: int, rom_size: int) -> int:
    start = address & ~3
    span = (address - start) + length
    return rom_offset(mapping, start, (span + 3) & ~3, rom_size) + (address - start)


def _groups_v3(rows: list, size: int) -> list[tuple]:
    """relocation_groups, additionally admitting rodata section symbols and unresolved externals."""
    groups, seen, index = [], set(), 0
    while index < len(rows):
        at, kind, identity = rows[index]
        identity = tuple(identity)
        text_local = identity[0] == "section" and identity[1] == ".text" and identity[2] == 0
        data_local = identity[0] == "section" and identity[1] in DATA_SECTIONS
        if not (identity[0] == "external" or text_local or data_local):
            raise ValueError("unsupported relocation symbol")
        if kind not in {2, 4, 5} or (data_local and kind != 5):
            raise ValueError("unsupported or orphan relocation")
        group = [(at, kind, identity)]
        if kind == 5:
            if index + 1 >= len(rows):
                raise ValueError("orphan HI16")
            low, low_kind, low_identity = rows[index + 1]
            if low_kind != 6 or tuple(low_identity) != identity:
                raise ValueError("requires adjacent same-symbol HI16/LO16 pair")
            group.append((low, low_kind, identity))
            index += 1
        for offset, _, _ in group:
            if offset < 0 or offset % 4 or offset + 4 > size or offset in seen:
                raise ValueError("overlapping or out-of-function relocation")
            seen.add(offset)
        groups.append(tuple(group))
        index += 1
    return sorted(groups)


def _data_relocations(data: bytes, section: str) -> list[tuple[int, int, bool]]:
    """(offset, type, against own .text section symbol) for a rodata section, read straight from the ELF.

    The object image skips non-ALLOC sections, and asm-processor leaves a target's `.late_rodata` jump table
    non-ALLOC (updateRaceSplitscreenSelectOption1Frame, 2026-09-30), so its relocations never reached the image."""
    rows, labels = _elf_sections(data)
    if labels.count(section) != 1:
        return []
    index = labels.index(section)
    text = labels.index(".text") if labels.count(".text") == 1 else -1
    out = []
    for row in rows:
        if row[1] not in (4, 9) or row[7] != index:
            continue
        if row[1] != 9 or row[9] != 8:
            raise ValueError("unsupported relocation format in rodata")
        symtab = rows[row[6]]
        for offset, info in struct.iter_unpack(">II", data[row[4]:row[4] + row[5]]):
            _name, value, _size, st_info, _other, shndx = struct.unpack_from(">IIIBBH", data, symtab[4] + (info >> 8) * 16)
            own_text = shndx == text and value == 0 and st_info & 15 == 3        # the .text section symbol
            out.append((offset, info & 255, own_text))
    return out


def _check_data_relocations(data: bytes) -> None:
    """Every relocation in any rodata section must be a jump-table word: R_MIPS_32 against the object's own .text."""
    _rows, labels = _elf_sections(data)
    for section in set(labels) & DATA_SECTIONS:
        for at, kind, own_text in _data_relocations(data, section):
            if kind != 2 or at % 4 or not own_text:
                raise ValueError("relocated data (jump tables, pointer tables) is outside schema 3")


def _linked_data(data: bytes, image: dict, section: str, address: int, size: int) -> tuple[bytes, set[int]]:
    """Section bytes with each jump-table word linked to the function at `address`, plus the linked offsets.

    Only words relocated against the object's own .text reach here (`_check_data_relocations`). An entry must land
    inside the certified function, word-aligned: a table that jumps anywhere else is not this function's."""
    raw = bytearray(_section_bytes(data, section))
    linked = set()
    for at, _kind, _own in _data_relocations(data, section):
        addend = int.from_bytes(raw[at:at + 4], "big")
        if addend % 4 or not 0 <= addend < size:
            raise ValueError("jump table entry outside the certified function")
        raw[at:at + 4] = ((address + addend) & 0xffffffff).to_bytes(4, "big")
        linked.add(at)
    return bytes(raw), linked


def _addend(text: bytes, group: tuple) -> int:
    word = int.from_bytes(text[group[0][0]:group[0][0] + 4], "big")
    if group[0][1] == 5:
        low = int.from_bytes(text[group[1][0]:group[1][0] + 4], "big") & 0xffff
        return ((word & 0xffff) << 16) + (low - 0x10000 if low & 0x8000 else low)
    if group[0][1] == 4:
        return (word & 0x03ffffff) << 2
    return word


def _link(text: bytes, resolved: list[tuple[tuple, int]], address: int) -> bytes:
    """Patch each group with its resolved symbol value; words not relocated keep their bytes."""
    linked = bytearray(text)

    def word(at):
        return int.from_bytes(text[at:at + 4], "big")

    def put(at, value):
        linked[at:at + 4] = (value & 0xffffffff).to_bytes(4, "big")
    for group, symbol in resolved:
        at, kind, identity = group[0]
        destination = (symbol + _addend(text, group)) & 0xffffffff
        if kind == 5:
            put(at, (word(at) & 0xffff0000) | (((destination + 0x8000) >> 16) & 0xffff))
            put(group[1][0], (word(group[1][0]) & 0xffff0000) | (destination & 0xffff))
        elif kind == 4:
            if word(at) >> 26 != 3 or destination % 4 or (destination ^ (address + at + 4)) & 0xf0000000:
                raise ValueError("requires aligned jal destination in the MIPS jump region")
            put(at, (word(at) & 0xfc000000) | ((destination >> 2) & 0x03ffffff))
        else:
            put(at, destination)
        if identity[0] == "section" and identity[1] == ".text" and not address <= destination < address + len(text):
            raise ValueError("local relocation escapes certified function")
    return bytes(linked)


def _certify_v3(*, target: Path, candidate: Path, assembly: Path, rom: Path, config: Path, symbols: Path,
                function: str, address: int, size: int) -> dict:
    """Function bytes plus the rodata they read, both against the ROM.

    Admits, beyond schema 2:
    - rodata/late_rodata in either object. A target data site resolves through
      the annotated dlabel of its section; a candidate's own datum resolves
      through the target site at the same instruction offsets, and must equal
      the ROM bytes of the target datum's extent, with only zero padding beyond;
    - candidate externs named like a target rodata label;
    - an address literal where the target relocates against an external that the
      symbol file does not define, only when that address is outside every
      file-backed segment (hardware/absolute addresses; libultra IO macros).
    Both functions, relocated, must reproduce the ROM function bytes. TU/link
    placement of the data is left to the whole-ROM integration gate.
    """
    import yaml
    target_data, candidate_data = target.read_bytes(), candidate.read_bytes()
    rom_data, asm = rom.read_bytes(), assembly.read_text()
    mapping = yaml.safe_load(config.read_bytes())
    left = cert.object_image(target_data)["sections"]
    right = cert.object_image(candidate_data)["sections"]
    for sections in (left, right):
        if ".text" not in sections or set(sections) - {".text"} - DATA_SECTIONS:
            raise ValueError("schema 3 admits only text plus rodata sections")
    # A jump table (32-bit words relocated against the object's own .text) is admitted; pointer tables to other
    # functions or data stay outside schema 3. Read from the ELF, so non-ALLOC late_rodata is checked too.
    _check_data_relocations(target_data)
    _check_data_relocations(candidate_data)
    for key in ("type", "flags", "alignment"):
        if left[".text"][key] != right[".text"][key]:
            raise ValueError("text attributes differ")
    lt, ls = text_extent(target_data, function)
    rt, rs = text_extent(candidate_data, function)
    if ls != rs or not 0 < ls <= size or size - ls >= 16:
        raise ValueError("function sizes differ")
    offset = rom_offset(mapping, address, size, len(rom_data))
    if any(rom_data[offset + ls:offset + size]):
        raise ValueError("metadata extent beyond the function symbol is not zero padding in ROM")
    # Metadata may count alignment padding after the symbol (__osSiRawStartDma: 176 vs 172).
    metadata_size, size = size, ls
    rom_function = rom_data[offset:offset + size]

    addresses = {}
    for name, value in re.findall(r"(?m)^\s*([\w.$]+)\s*=\s*(0x[0-9a-fA-F]+)\s*;", symbols.read_text()):
        if name in addresses and addresses[name] != int(value, 16):
            raise ValueError("conflicting linker symbol addresses")
        addresses[name] = int(value, 16)
    if addresses.get(function) != address:
        raise ValueError("function symbol address disagrees with metadata")
    segment_symbols = _segment_symbols(mapping, len(rom_data))

    annotated = {name: (int(rom_off, 16), int(vram, 16)) for name, rom_off, vram in DATA_LABEL.findall(asm)}
    target_labels = _data_symbols(target_data)
    bases, label_addresses = {}, {}
    for section, rows in target_labels.items():
        for value, name in rows:
            if name in annotated:
                base = annotated[name][1] - value
                if bases.setdefault(section, base) != base:
                    raise ValueError("inconsistent rodata label annotations")
        for value, name in rows:
            if section in bases:
                label_addresses[name] = bases[section] + value
    data_sites, absolute_literals, absolute_symbols = [], [], {}

    # Target: every group resolves from the symbol file, the annotated rodata labels,
    # or (external absolute addresses only) the ROM words at the site.
    target_resolved, target_destinations = [], {}
    for group in _groups_v3(left[".text"]["relocations"], size):
        identity = group[0][2]
        if identity[0] == "section" and identity[1] == ".text":
            symbol = address
        elif identity[0] == "section":
            if identity[1] not in bases:
                raise ValueError("target rodata section has no annotated label")
            symbol = bases[identity[1]] + identity[2]
        elif identity[1] in addresses or identity[1] in label_addresses or identity[1] in segment_symbols:
            symbol = addresses.get(identity[1], label_addresses.get(identity[1], segment_symbols.get(identity[1])))
        elif group[0][1] == 5:
            hi = int.from_bytes(rom_function[group[0][0]:group[0][0] + 4], "big") & 0xffff
            lo = int.from_bytes(rom_function[group[1][0]:group[1][0] + 4], "big") & 0xffff
            destination = ((hi << 16) + (lo - 0x10000 if lo & 0x8000 else lo)) & 0xffffffff
            if not _unmapped(mapping, destination, len(rom_data)):
                raise ValueError("undefined external inside a file-backed segment")
            symbol = (destination - _addend(lt, group)) & 0xffffffff
            if absolute_symbols.setdefault(identity[1], symbol) != symbol:
                raise ValueError("inconsistent absolute symbol address")
            absolute_literals.append({"offsets": [g[0] for g in group], "symbol": identity[1],
                                      "address": destination})
        else:
            raise ValueError("unresolved or invalid symbol address")
        target_resolved.append((group, symbol))
        target_destinations[tuple(g[0] for g in group)] = (identity, (symbol + _addend(lt, group)) & 0xffffffff)
    if _link(lt[:size], target_resolved, address) != rom_function:
        raise ValueError("relocated target does not reproduce ROM")

    # Target data read by those sites must itself be the ROM's bytes (jump tables linked to the function first).
    target_extents = {}
    for section, rows in target_labels.items():
        section_bytes, _tables = _linked_data(target_data, left, section, address, size)
        values = [v for v, _ in rows] + [len(section_bytes)]
        for (value, _name), end in zip(rows, values[1:]):
            target_extents[(section, value)] = section_bytes[value:end]

    candidate_resolved = []
    candidate_labels = {}
    for group in _groups_v3(right[".text"]["relocations"], size):
        identity = group[0][2]
        offsets = tuple(g[0] for g in group)
        if identity[0] == "section" and identity[1] == ".text":
            symbol = address
        elif identity[0] == "external":
            if identity[1] in addresses:
                symbol = addresses[identity[1]]
            elif identity[1] in label_addresses:
                symbol = label_addresses[identity[1]]
            elif identity[1] in segment_symbols:
                # Link-defined segment bounds (`_593D10_ROM_START`), from the segment config input.
                symbol = segment_symbols[identity[1]]
            elif identity[1] in absolute_symbols:
                # Same name as a target external resolved to an absolute address (e.g. ROM asset bounds).
                symbol = absolute_symbols[identity[1]]
            else:
                raise ValueError("unresolved candidate external")
        else:
            counterpart = target_destinations.get(offsets)
            if counterpart is None or counterpart[0][0] != "section" or counterpart[0][1] not in DATA_SECTIONS:
                raise ValueError("candidate rodata site without a target rodata site")
            target_identity, destination = counterpart
            _linked, candidate_tables = _linked_data(candidate_data, right, identity[1], address, size) \
                if identity[1] in right else (b"", set())
            jump_table = (int.from_bytes(rt[group[1][0]:group[1][0] + 4], "big") >> 26 == 0x23
                          and identity[2] + _addend(rt, group) in candidate_tables)
            if not jump_table and int.from_bytes(rt[group[1][0]:group[1][0] + 4], "big") >> 26 not in (0x31, 0x35):
                # Only float/double loads (lwc1/ldc1). An address-taken literal such as a string
                # passed this check but failed the whole-ROM checksum once integrated
                # (drawRaceSplitscreenSelectEntryFee, 2026-09-14): its TU placement differs.
                raise ValueError("address-taken candidate rodata needs its named symbol for integration")
            datum_start = counterpart[1] - bases[target_identity[1]]
            label = max((v for v, _ in target_labels[target_identity[1]] if v <= datum_start), default=None)
            if label is None:
                raise ValueError("target datum has no label")
            extent = target_extents[(target_identity[1], label)][datum_start - label:]
            added = _addend(rt, group)
            own = identity[2] + added
            candidate_labels.setdefault(identity[1], []).append((own, len(extent)))
            symbol = (destination - added) & 0xffffffff
            data_sites.append({"offsets": list(offsets), "target_address": destination,
                               "candidate_section": identity[1], "candidate_offset": own, "length": len(extent)})
        candidate_resolved.append((group, symbol))
    if _link(rt[:size], candidate_resolved, address) != rom_function:
        raise ValueError("relocated candidate does not reproduce ROM")

    for section in set(right) & DATA_SECTIONS:
        if section not in candidate_labels and any(_section_bytes(candidate_data, section)):
            raise ValueError("candidate rodata the function does not read")
    for section, sites in candidate_labels.items():
        section_bytes, _tables = _linked_data(candidate_data, right, section, address, size)
        covered = bytearray(len(section_bytes))
        for own, length in sites:
            covered[own:own + length] = b"\1" * len(section_bytes[own:own + length])
        if any(byte and not mark for byte, mark in zip(section_bytes, covered)):
            raise ValueError("candidate rodata the function does not read")
        starts = sorted({own for own, _ in sites}) + [len(section_bytes)]
        for site in data_sites:
            if site["candidate_section"] != section:
                continue
            own, length = site["candidate_offset"], site["length"]
            available = min(length, len(section_bytes) - own)
            if available <= 0:
                raise ValueError("candidate datum outside its section")
            rom_at = _data_rom_offset(mapping, site["target_address"], length, len(rom_data))
            expected = rom_data[rom_at:rom_at + length]
            if section_bytes[own:own + available] != expected[:available] or any(expected[available:]):
                raise ValueError("candidate datum differs from ROM")
            following = min(s for s in starts if s > own)
            if any(section_bytes[own + available:following]):
                raise ValueError("candidate datum carries nonzero bytes beyond the target extent")
    for (section, value), extent in target_extents.items():
        if section in bases and extent:
            rom_at = _data_rom_offset(mapping, bases[section] + value, len(extent), len(rom_data))
            if rom_data[rom_at:rom_at + len(extent)] != extent:
                raise ValueError("target rodata disagrees with ROM")

    if len(lt) != (size + left[".text"]["alignment"] - 1) // left[".text"]["alignment"] * left[".text"]["alignment"] \
            and any(lt[size:]):
        raise ValueError("unexplained target section extent")
    if any(rt[size:]):
        raise ValueError("nonzero candidate bytes after function")
    return {"schema_version": 3, "function_exact": True, "status": "function_exact_pending_integration",
            "scope": "annotated function bytes; rodata read by the function and absolute-address literals, "
                     "each resolved and compared against the ROM",
            "excluded": ["trailing text bytes", "TU/link layout", "rodata placement after integration", "whole ROM"],
            "rom_offset": offset, "function_bytes": size, "metadata_bytes": metadata_size, "data_sites": data_sites,
            "absolute_literals": absolute_literals}


def revalidate(receipt: dict) -> bool:
    """Replay from bound immutable artifacts before staging any replacement."""
    try:
        paths = {key: Path(value["path"]) for key, value in receipt["inputs"].items()}
        if any(cert.digest(path.read_bytes()) != receipt["inputs"][key]["sha256"]
               for key, path in paths.items()):
            return False
        fresh = certify(**paths, **{key: receipt[key] for key in ("function", "address", "size")})
        return fresh.get("function_exact") is True and fresh == receipt
    except (OSError, KeyError, TypeError, ValueError):
        return False
