"""Conservative ELF32/MIPS section certificate, independent of asm scoring.

Equality here means equal section bytes/layout and relocation expressions under
the same link environment. It is NOT a certificate for the final linked ROM.
We deliberately reject unfamiliar ELF features rather than normalize them away.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def independent_relocation_groups(rows: list) -> tuple | None:
    """Canonicalize only disjoint external scalar relocations/intact HI/LO pairs.

    The MIPS ABI couples HI16 with the immediately following LO16. Preserve
    that pairing; never sort individual entries, infer orphan LO16 partners,
    or normalize GNU multi-HI extensions. Raw equality still handles those.
    See https://sourceware.org/pipermail/binutils/2023-February/125959.html
    """
    groups, occupied, index = [], set(), 0
    while index < len(rows):
        at, kind, symbol = rows[index]
        if symbol[0] != "external" or kind not in {2, 4, 5}:
            return None
        group = [(at, kind, tuple(symbol))]
        if kind == 5:
            if index + 1 >= len(rows):
                return None
            lo_at, lo_kind, lo_symbol = rows[index + 1]
            if lo_kind != 6 or tuple(lo_symbol) != tuple(symbol):
                return None
            group.append((lo_at, lo_kind, tuple(lo_symbol)))
            index += 1
        for offset, _, _ in group:
            if offset % 4 or offset in occupied:
                return None
            occupied.add(offset)
        groups.append(tuple(group))
        index += 1
    return tuple(sorted(groups))


def sections_equivalent(left: dict, right: dict) -> bool:
    if set(left) != set(right):
        return False
    for name, section in left.items():
        other = right[name]
        if {k: v for k, v in section.items() if k != "relocations"} != {
                k: v for k, v in other.items() if k != "relocations"}:
            return False
        if section["relocations"] == other["relocations"]:
            continue
        first = independent_relocation_groups(section["relocations"])
        second = independent_relocation_groups(other["relocations"])
        if first is None or second is None or first != second:
            return False
    return True


def object_image(data: bytes) -> dict:
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x02\x01":
        raise ValueError("expected big-endian ELF32")
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    if header[0:2] != (1, 8):
        raise ValueError("expected relocatable MIPS object")
    shoff, shsize, count, names_index = header[5], header[10], header[11], header[12]
    if shsize != 40 or not 0 < names_index < count:
        raise ValueError("unsupported ELF section table")
    sections = [struct.unpack_from(">IIIIIIIIII", data, shoff + i * 40)
                for i in range(count)]

    def contents(row):
        offset, size = row[4:6]
        if offset + size > len(data):
            raise ValueError("truncated ELF section")
        return data[offset:offset + size]

    names = contents(sections[names_index])

    def string(table, offset):
        end = table.find(b"\0", offset)
        if end < offset:
            raise ValueError("invalid ELF string")
        return table[offset:end].decode("utf-8", errors="strict")

    section_names = [string(names, row[0]) for row in sections]
    images = {}
    for index, row in enumerate(sections):
        name = section_names[index]
        if not row[2] & 2 or row[5] == 0:
            continue
        # ABI metadata is not game text/data. State its exclusion in the receipt.
        if name in {".reginfo", ".MIPS.abiflags"}:
            continue
        if row[1] not in {1, 8}:
            raise ValueError(f"unsupported allocated section {name}")
        if name in images:
            raise ValueError("duplicate allocated section name")
        images[name] = {"size": row[5], "type": row[1], "flags": row[2],
                        "alignment": row[8], "sha256": (
                            digest(contents(row)) if row[1] != 8 else None),
                        "relocations": []}
    if ".text" not in images:
        raise ValueError("missing nonempty text section")
    for row in sections:
        if row[1] not in {4, 9}:
            continue
        dest = section_names[row[7]]
        if dest not in images:
            continue
        if row[1] != 9 or row[9] != 8 or row[5] % 8:
            raise ValueError("unsupported relocation format")
        symbols = sections[row[6]]
        if symbols[1] != 2 or symbols[9] != 16:
            raise ValueError("unsupported symbol table")
        symdata = contents(symbols)
        strings = contents(sections[symbols[6]])
        for offset, info in struct.iter_unpack(">II", contents(row)):
            symbol_index, kind = info >> 8, info & 255
            if kind not in {0, 2, 3, 4, 5, 6, 7}:
                raise ValueError(f"unsupported MIPS relocation {kind}")
            if offset + 4 > images[dest]["size"]:
                raise ValueError("relocation outside section")
            name, value, size, binding, other, section = struct.unpack_from(
                ">IIIBBH", symdata, symbol_index * 16)
            if section == 0:
                identity = ("external", string(strings, name), binding >> 4, other)
            elif section == 0xfff1:
                identity = ("absolute", value, binding >> 4, other)
            elif 0 < section < count:
                identity = ("section", section_names[section], value, binding >> 4, other)
            else:
                raise ValueError("unsupported common/extended symbol")
            # Keep relocation order: HI16/LO16 pairing is order-sensitive.
            images[dest]["relocations"].append((offset, kind, identity))
    return {"elf_flags": header[6], "sections": images}


def certify(target: Path, candidate: Path, *, source: str,
            build_inputs: dict[str, str] | None = None) -> dict:
    receipt = {"schema_version": 1, "kind": "mips_object_section_certificate",
               "scope": "allocated text/data/BSS and relocation expressions; same link environment",
               "excluded": ["debug and ABI metadata", "final link layout", "whole ROM"],
               "whole_rom_verified": False, "source_sha256": digest(source.encode()),
               "build_inputs": build_inputs or {}, "exact": False}
    try:
        target_data, candidate_data = target.read_bytes(), candidate.read_bytes()
        receipt.update(target_sha256=digest(target_data), candidate_sha256=digest(candidate_data))
        left, right = object_image(target_data), object_image(candidate_data)
        receipt.update(target_image=left, candidate_image=right)
        # Extracted GNU-as objects and IDO objects use different architecture
        # flags and symbol types. Those are not bytes or relocation expressions.
        equal = sections_equivalent(left["sections"], right["sections"])
        receipt["exact"] = equal
        receipt["relocation_order_equivalent"] = equal and left["sections"] != right["sections"]
        receipt["relocation_comparison"] = "raw order or disjoint external scalar/intact HI16-LO16 groups; pairing preserved"
        receipt["status"] = "object_sections_exact" if equal else "object_sections_differ"
    except (OSError, ValueError, struct.error, IndexError, UnicodeError) as exc:
        receipt.update(status="unverified", error=f"{type(exc).__name__}: {exc}")
    return receipt
