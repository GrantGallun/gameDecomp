"""Conservative layout clusters for scheduling, not proven translation units.

Padding between recorded function ranges often coincides with an object-file
boundary. It can also reflect function alignment or missing inventory, while
adjacent objects can have no padding at all. Cluster membership is therefore a
retractable locality heuristic; it must not establish source-file ownership.

No reference linker-map assignment is consulted. The current extractor obtains
function ranges from ELF symbols and verifies their words against the ROM, so
these ranges should not be described as recovered from an unannotated ROM.
"""
from __future__ import annotations

from collections import defaultdict


def _address(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _layout(rows):
    """Yield usable address groups and retain unknown extents as barriers.

    A conflicting name has no unambiguous placement. Decline all its records,
    retaining any known addresses as uncertainty barriers. Identical duplicate
    records, in contrast, carry no additional information.
    """
    named = defaultdict(list)
    barriers = set()
    for addr, size, name in rows:
        if not isinstance(name, str) or not name:
            if _address(addr):
                barriers.add(addr)
            continue
        named[name].append((addr, size))
    groups = defaultdict(list)
    for name, extents in named.items():
        addr, size = extents[0]
        valid = all(_address(a) and isinstance(s, int)
                    and not isinstance(s, bool) and s > 0 for a, s in extents)
        if not valid or any(extent != (addr, size) for extent in extents):
            barriers.update(a for a, _ in extents if _address(a))
            continue
        groups[addr].append((size, name))
    for addr in sorted(groups.keys() | barriers):
        yield addr, sorted(groups.get(addr, ())), addr in barriers


def _scan(rows):
    index, cuts = {}, set()
    label = previous = end = None
    for addr, entries, uncertain in _layout(rows):
        if uncertain:
            # Do not infer adjacency or padding across an unknown extent.
            label = previous = end = None
        if entries:
            if end is not None and addr > end:
                cuts.add(previous)
                label = None
            if label is None:
                label = f'unit@{addr:#010x}'
            for size, name in entries:
                index[name] = label
                # Nested intervals and aliases must not manufacture a gap.
                end = max(end or 0, addr + size)
                previous = name
        if uncertain:
            label = previous = end = None
    return index, cuts


def boundaries(rows: list[tuple[int, int, str]]) -> set[str]:
    """Last known function names before observed gaps in the range union.

    Unknown extents split clusters conservatively but are not padding witnesses.
    Overlapping or nested ranges use their maximum end, not the last row's end.
    """
    return _scan(rows)[1]


def clusters(rows: list[tuple[int, int, str]]) -> dict[str, str]:
    """Map unambiguous, positive-sized functions to stable locality labels.

    Unknown addresses cannot be placed. Unknown/invalid sizes at known addresses
    remain barriers, so discarding a row cannot manufacture adjacency between
    the remaining functions. Labels retain the historical ``unit@`` spelling
    for checkpoint compatibility; they represent heuristic layout clusters.
    """
    return _scan(rows)[0]
