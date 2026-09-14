"""Narrow ROM-backed function extent certificate, separate from object exactness.

Only a single text function, with zero-only trailing bytes and external R_MIPS_26
calls, is supported initially. No padding is deleted or added to either object.
The annotated extent is checked against both ELF symbols, DB metadata, segment
mapping and ROM bytes. Whole-TU padding remains an integration obligation.
"""
from __future__ import annotations

from pathlib import Path
import re
import struct

from solver import byte_certificate as cert
from solver.sdk_intake import rom_offset


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
        for key in ("type", "flags", "alignment", "relocations"):
            if left[".text"][key] != right[".text"][key]:
                raise ValueError("text attributes or relocation expressions differ")
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
        linked = bytearray(lt[:size])
        seen = set()
        for at, kind, identity in left[".text"]["relocations"]:
            if at in seen or at % 4 or at + 4 > size or kind != 4 or identity[0] != "external":
                raise ValueError("unsupported relocation or relocation outside function")
            seen.add(at)
            destination = addresses.get(identity[1])
            word = int.from_bytes(linked[at:at + 4], "big")
            if destination is None or destination % 4 or word >> 26 != 3 or word & 0x03ffffff:
                raise ValueError("requires resolved external jal with zero addend")
            if (destination ^ (address + at + 4)) & 0xf0000000:
                raise ValueError("call crosses MIPS jump region")
            linked[at:at + 4] = (word | ((destination >> 2) & 0x03ffffff)).to_bytes(4, "big")
        if bytes(linked) != rom_data[offset:offset + size]:
            raise ValueError("relocated function object does not reproduce ROM")
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
