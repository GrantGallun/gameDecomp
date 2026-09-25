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


def same_addend_pairing_groups(rows: list, contents: bytes) -> tuple | None:
    """Like `independent_relocation_groups`, but HI16/LO16 pairs of one symbol whose pairs ALL carry the same
    instruction immediates (the REL addend halves) are canonicalized as a set of HI16 offsets and a set of LO16
    offsets: the linker then computes the same value whichever HI16 a LO16 is paired with.

    Found 2026-09-24 (eval/results/reloc-pairing-20260924): IDO's assembler lists such pairs in instruction order
    while the GNU-as-assembled target lists each HI16 before its partner, so the reference decomp's OWN source for
    osCreateMesgQueue, compiled in its own translation unit, failed strict pairing with identical bytes. Pairs of one
    symbol with DIFFERENT immediates (`sym+4` against `sym+8`) keep strict pairing: this returns None.
    """
    groups = independent_relocation_groups(rows)
    if groups is None:
        return None
    scalars, pairs = [], {}
    for group in groups:
        if len(group) == 1:
            scalars.append(group)
            continue
        (hi_at, _, symbol), (lo_at, _, _) = group
        if hi_at + 4 > len(contents) or lo_at + 4 > len(contents):
            return None
        pairs.setdefault(symbol, []).append((hi_at, lo_at, contents[hi_at + 2:hi_at + 4], contents[lo_at + 2:lo_at + 4]))
    canonical = []
    for symbol, members in pairs.items():
        immediates = {(hi, lo) for _, _, hi, lo in members}
        if len(immediates) != 1:
            return None
        canonical.append((symbol, tuple(sorted(m[0] for m in members)), tuple(sorted(m[1] for m in members)),
                          tuple(immediates)))
    return tuple(sorted(scalars)), tuple(sorted(canonical))


def section_contents(data: bytes) -> dict[str, bytes]:
    """Allocated PROGBITS contents by section name (relocation immediates for `same_addend_pairing_groups`)."""
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    shoff, count, names_index = header[5], header[11], header[12]
    rows = [struct.unpack_from(">IIIIIIIIII", data, shoff + i * 40) for i in range(count)]
    names = data[rows[names_index][4]:rows[names_index][4] + rows[names_index][5]]
    out = {}
    for row in rows:
        name = names[row[0]:names.find(b"\0", row[0])].decode("utf-8", errors="strict")
        if row[2] & 2 and row[1] == 1:
            out[name] = data[row[4]:row[4] + row[5]]
    return out


RODATA = {".rodata", ".late_rodata"}
_LOAD_WIDTH = {0x23: 4, 0x31: 4, 0x35: 8, 0x21: 2, 0x25: 2, 0x20: 1, 0x24: 1}   # lw lwc1 ldc1 lh lhu lb lbu


def progbits_contents(data: bytes) -> dict[str, bytes]:
    """Every PROGBITS section's contents by name, allocated or not (asm-processor's .late_rodata is not allocated)."""
    header = struct.unpack_from(">HHIIIIIHHHHHH", data, 16)
    shoff, count, names_index = header[5], header[11], header[12]
    rows = [struct.unpack_from(">IIIIIIIIII", data, shoff + i * 40) for i in range(count)]
    names = data[rows[names_index][4]:rows[names_index][4] + rows[names_index][5]]
    return {names[r[0]:names.find(b"\0", r[0])].decode("utf-8", errors="strict"): data[r[4]:r[4] + r[5]]
            for r in rows if r[1] == 1}


def rodata_value_relocations(rows: list, text: bytes, raw: dict[str, bytes]) -> list | None:
    """Relocations with each section-relative `.rodata`/`.late_rodata` HI16/LO16 pair's identity replaced by the
    BYTES the paired load reads (width from the load opcode, address = the pair's REL addend).

    Found 2026-09-25 (eval/results/operand-repair-20260925): the target keeps literal constants in asm-processor's
    `.late_rodata`, which `object_image` skips (not allocated), and the candidate in `.rodata`. Comparing section
    names both rejected equal constants and ACCEPTED a sign-flipped one (`-4/3` against the ROM's `+4/3`), because the
    value itself was never read. Returns None when a rodata reference cannot be resolved to bytes."""
    out, index = [], 0
    while index < len(rows):
        at, kind, identity = rows[index]
        ident = tuple(identity) if isinstance(identity, list) else identity
        if ident[0] == "section" and ident[1] in RODATA:
            if kind != 5 or index + 1 >= len(rows):
                return None
            lo_at, lo_kind, lo_identity = rows[index + 1]
            if lo_kind != 6 or tuple(lo_identity) != ident or lo_at + 4 > len(text) or at + 4 > len(text):
                return None
            hi = struct.unpack(">H", text[at + 2:at + 4])[0]
            lo = struct.unpack(">h", text[lo_at + 2:lo_at + 4])[0]
            width = _LOAD_WIDTH.get(text[lo_at] >> 2)
            section = raw.get(ident[1], b"")
            address = (hi << 16) + lo + ident[2]
            if width is None or address < 0 or address + width > len(section):
                return None
            value = ("rodata-bytes", section[address:address + width].hex(), 1, 0)
            out += [(at, 5, value), (lo_at, 6, value)]
            index += 2
            continue
        out.append((at, kind, ident))
        index += 1
    return out


def rodata_equivalent(left: dict, right: dict, left_raw: dict, right_raw: dict) -> bool:
    """Sections equal except that section-relative rodata references compare by the bytes they read.

    An allocated `.rodata` present on only one side (IDO's literal pool in the candidate) is accepted only when its
    contents equal, byte for byte, the other side's `.late_rodata` (asm-processor's non-allocated copy in the target).
    """
    only = set(left) ^ set(right)
    if only - {".rodata"}:
        return False
    if only:
        mine, theirs = (left_raw, right_raw) if ".rodata" in left else (right_raw, left_raw)
        if mine.get(".rodata") is None or mine.get(".rodata") != theirs.get(".late_rodata"):
            return False
        if (left.get(".rodata") or right.get(".rodata"))["relocations"]:
            return False
    for name, section in left.items():
        if name not in right:
            continue
        other = right[name]
        if {k: v for k, v in section.items() if k != "relocations"} != {
                k: v for k, v in other.items() if k != "relocations"}:
            return False
        a = rodata_value_relocations(section["relocations"], left_raw.get(name, b""), left_raw)
        b = rodata_value_relocations(other["relocations"], right_raw.get(name, b""), right_raw)
        if a is None or b is None:
            return False
        if a == b:
            continue
        ga, gb = _value_groups(a, left_raw.get(name, b"")), _value_groups(b, right_raw.get(name, b""))
        if ga is None or gb is None or ga != gb:
            return False
    return True


def _value_groups(rows, text):
    """Pairing-normalized groups where rodata values stand in for symbols (externals and values both allowed)."""
    rows = [(at, kind, ("external", *ident[1:]) if ident[0] == "rodata-bytes" else ident) for at, kind, ident in rows]
    return same_addend_pairing_groups(rows, text)


def pairing_equivalent(left: dict, right: dict, left_bytes: dict, right_bytes: dict) -> bool:
    """Sections equal in everything but relocation order, where the only difference is same-addend HI16/LO16 pairing."""
    if set(left) != set(right):
        return False
    for name, section in left.items():
        other = right[name]
        if {k: v for k, v in section.items() if k != "relocations"} != {
                k: v for k, v in other.items() if k != "relocations"}:
            return False
        if section["relocations"] == other["relocations"]:
            continue
        first = same_addend_pairing_groups(section["relocations"], left_bytes.get(name, b""))
        second = same_addend_pairing_groups(other["relocations"], right_bytes.get(name, b""))
        if first is None or second is None or first != second:
            return False
    return True


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
        # Second stage, recorded explicitly: identical bytes whose relocation tables differ ONLY in the pairing of
        # same-symbol, same-immediate HI16/LO16 records link identically (see same_addend_pairing_groups).
        receipt["relocation_pairing_normalized"] = False
        if not equal and pairing_equivalent(left["sections"], right["sections"],
                                            section_contents(target_data), section_contents(candidate_data)):
            equal = True
            receipt["relocation_pairing_normalized"] = True
        # Third stage, recorded explicitly: section-relative .rodata/.late_rodata references compared by the bytes
        # they read, not by section name (see rodata_value_relocations).
        receipt["rodata_values_compared"] = False
        if not equal:
            left_raw, right_raw = progbits_contents(target_data), progbits_contents(candidate_data)
            if rodata_equivalent(left["sections"], right["sections"], left_raw, right_raw):
                equal = True
                receipt["rodata_values_compared"] = True
        receipt["exact"] = equal
        receipt["relocation_order_equivalent"] = equal and left["sections"] != right["sections"]
        receipt["relocation_comparison"] = "raw order or disjoint external scalar/intact HI16-LO16 groups; pairing preserved"
        receipt["status"] = "object_sections_exact" if equal else "object_sections_differ"
    except (OSError, ValueError, struct.error, IndexError, UnicodeError) as exc:
        receipt.update(status="unverified", error=f"{type(exc).__name__}: {exc}")
    return receipt
