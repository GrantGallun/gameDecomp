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
    return result


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
