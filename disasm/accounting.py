"""Byte ledger: every ROM byte is classified, and unknown is counted.

The dangerous front-end failure is silent: code that nothing reaches is not
disassembled, so it never becomes a function, never fails to match, and never
shows up anywhere. The ledger makes that loss a number. Two invariants:

* Inside CPU .text every byte belongs to a function or to its recorded
  padding. A gap is an error (`text_gaps`), never quietly dropped.
* Outside the classified regions, bytes are `unclassified` and reported --
  assets, compressed data, or code we failed to find. The front end does
  not get to call them data because it did not look.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ledger:
    rom_size: int
    regions: list[tuple[int, int, str]] = field(default_factory=list)  # rom [a,b) kind
    text_gaps: list[tuple[int, int]] = field(default_factory=list)      # rom [a,b)

    def add(self, a: int, b: int, kind: str) -> None:
        if b > a:
            self.regions.append((a, b, kind))

    def overlaps(self) -> list[tuple[tuple, tuple]]:
        r = sorted(self.regions)
        return [(x, y) for x, y in zip(r, r[1:]) if y[0] < x[1]]

    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for a, b, k in self.regions:
            by_kind[k] = by_kind.get(k, 0) + (b - a)
        classified = sum(by_kind.values())
        return {
            "rom_size": self.rom_size,
            "bytes_by_kind": dict(sorted(by_kind.items())),
            "unclassified_bytes": self.rom_size - classified,
            "unclassified_fraction": round(1 - classified / self.rom_size, 4),
            "text_gap_bytes": sum(b - a for a, b in self.text_gaps),
            "text_gaps": [(f"{a:#x}", f"{b:#x}") for a, b in self.text_gaps[:50]],
            "overlapping_regions": len(self.overlaps()),
        }


def text_gaps(seg_rom_start: int, seg_vram: int, text_lo: int, text_hi: int,
              functions) -> list[tuple[int, int]]:
    """ROM ranges of [text_lo, text_hi) (vram) that no function or padding covers."""
    gaps, cursor = [], text_lo
    for f in sorted(functions, key=lambda f: f.vram):
        if f.vram > cursor:
            gaps.append((cursor, f.vram))
        cursor = max(cursor, f.vram + f.size + f.padding)
    if cursor < text_hi:
        gaps.append((cursor, text_hi))
    return [(seg_rom_start + a - seg_vram, seg_rom_start + b - seg_vram) for a, b in gaps]
