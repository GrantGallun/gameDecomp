"""Stage 1: ROM identity, byte order, header and the boot segment.

The boot segment is the one thing every N64 game has: IPL3 copies 1 MiB from
ROM 0x1000 to the header's entry point (adjusted by the CIC's offset) and jumps
there. The entry code then clears .bss and jumps to the game's first function.
So the entry code alone gives the boot segment's extent: its ROM image runs
from 0x1000 until the address where .bss begins.

Header and entrypoint parsing is splat's (`splat.util.n64.rominfo`), which is
binary-only. This module records what it derived and refuses to guess when the
entry code is not the traditional bss-clearing shape.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

BOOT_ROM_START = 0x1000
IPL3_COPY_BYTES = 0x100000


def normalize(raw: bytes) -> tuple[bytes, str]:
    """Return big-endian (.z64) bytes and the order the dump was stored in."""
    magic = raw[:4]
    if magic == b"\x80\x37\x12\x40":
        return raw, "z64"
    if magic == b"\x37\x80\x40\x12":          # 16-bit byte swapped
        out = bytearray(raw)
        out[0::2], out[1::2] = raw[1::2], raw[0::2]
        return bytes(out), "v64"
    if magic == b"\x40\x12\x37\x80":          # 32-bit little endian
        out = bytearray(len(raw))
        for i in range(4):
            out[i::4] = raw[3 - i::4]
        return bytes(out), "n64"
    raise ValueError(f"not an N64 ROM: magic {magic.hex()}")


@dataclass(frozen=True)
class Segment:
    name: str
    rom_start: int
    rom_end: int          # exclusive; end of the ROM image (code+data, no bss)
    vram: int
    bss_start: int | None = None
    bss_size: int | None = None
    derived_from: str = ""

    @property
    def vram_end(self) -> int:
        return self.vram + (self.rom_end - self.rom_start)

    def vram_to_rom(self, vram: int) -> int:
        if not self.vram <= vram < self.vram_end:
            raise ValueError(f"{vram:#x} outside segment {self.name}")
        return self.rom_start + (vram - self.vram)


@dataclass(frozen=True)
class RomInfo:
    sha1: str
    byte_order: str
    size: int
    title: str
    game_code: str
    libultra_version: str
    cic: str
    header_entry: int
    load_vram: int        # where IPL3 actually puts ROM 0x1000
    main_address: int | None
    stack_top: int | None
    bss_start: int | None
    bss_size: int | None
    traditional_entrypoint: bool

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("header_entry", "load_vram", "main_address", "stack_top",
                  "bss_start", "bss_size"):
            if d[k] is not None:
                d[k] = f"{d[k]:#010x}"
        return d


def read(path: Path) -> tuple[bytes, RomInfo]:
    rom, order = normalize(Path(path).read_bytes())
    return rom, info(rom, order)


def info(rom: bytes, byte_order: str = "z64") -> RomInfo:
    from splat.util.n64 import rominfo

    encoding = rominfo.guess_header_encoding(rom)
    r = rominfo.get_info_bytes(rom, encoding)
    ep = r.entrypoint_info

    def val(x):
        return None if x is None else x.value

    return RomInfo(
        sha1=hashlib.sha1(rom).hexdigest(),
        byte_order=byte_order,
        size=len(rom),
        title=r.name.strip(),
        game_code=rom[0x3B:0x3F].decode("ascii", "replace"),
        libultra_version=r.libultra_version,
        cic=r.cic.ntsc_name,
        header_entry=int.from_bytes(rom[8:12], "big"),
        load_vram=r.entry_point,
        main_address=val(ep.main_address),
        stack_top=val(ep.stack_top),
        bss_start=val(ep.bss_start_address),
        bss_size=ep.get_bss_size(),
        traditional_entrypoint=ep.traditional_entrypoint,
    )


def boot_segment(ri: RomInfo) -> Segment:
    """ROM 0x1000 loaded at the entry point, ending where .bss begins.

    Refuses rather than guesses: without a bss start from the entry code there
    is no binary statement of where the segment's image ends.
    """
    if ri.bss_start is None:
        raise ValueError("entry code does not state a .bss start; boot segment "
                         "extent unknown (non-traditional entrypoint)")
    if ri.bss_start <= ri.load_vram:
        raise ValueError(f"bss start {ri.bss_start:#x} precedes load address "
                         f"{ri.load_vram:#x}")
    size = ri.bss_start - ri.load_vram
    return Segment(
        name="boot",
        rom_start=BOOT_ROM_START,
        rom_end=BOOT_ROM_START + size,
        vram=ri.load_vram,
        bss_start=ri.bss_start,
        bss_size=ri.bss_size,
        derived_from="entry code: bss clear start - IPL3 load address",
    )
