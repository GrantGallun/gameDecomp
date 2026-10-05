"""Compare the `.text` tails of a function that certified and one that did not.

`function_boundary.certify` requires `size % 4 == 0`, `size <= len(text)` and every byte after `size` to be
zero. rmonPrintf's candidate `.text` is 32 bytes for a 28-byte function, so the answer depends on those
four trailing bytes -- and the same table is printed for `osSyncPrintf`, which DID certify, so the
difference is visible rather than inferred.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def text_of(path: Path) -> bytes:
    data = path.read_bytes()
    shoff, = struct.unpack_from(">I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from(">HHH", data, 0x2E)
    rows = [struct.unpack_from(">IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    strtab = rows[shstrndx]
    table = data[strtab[4]:strtab[4] + strtab[5]]

    def name(offset: int) -> str:
        return table[offset:table.find(b"\0", offset)].decode()

    for row in rows:
        if name(row[0]) == ".text":
            return data[row[4]:row[4] + row[5]]
    raise SystemExit(f"{path}: no .text")


CASES = ((Path("/home/grant/decomp/sbk1/nonmatchings/rmonPrintf"), 28, "rmonPrintf (did NOT certify)"),
         (Path("/home/grant/decomp/sbk1/nonmatchings/osSyncPrintf"), 32, "osSyncPrintf (DID certify)"))

for ws, size, label in CASES:
    print(f"=== {label}: function size {size} ===")
    for which in ("candidate", "target"):
        path = ws / ("target.o" if which == "target" else f"{ws.name}.o")
        if not path.is_file():
            print(f"  {which:9} {path.name}: missing")
            continue
        text = text_of(path)
        trailing = text[size:]
        print(f"  {which:9} {path.name:20} .text={len(text):4} trailing={len(trailing):3} "
              f"all_zero={not any(trailing)} bytes={trailing.hex()}")
        print(f"            first 16 of the function: {text[:16].hex()}")
    print()
