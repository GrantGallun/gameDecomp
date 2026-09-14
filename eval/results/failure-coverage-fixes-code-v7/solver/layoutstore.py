"""Struct layout evidence pooled ACROSS functions, from logged residuals.

Layout repair has always been per-function: a residual pins a field, the
struct is padded, and the knowledge dies when the run ends. The same struct is
touched by many functions, so a field pinned by one is pinned for all of them
before they are ever attempted.

MEASURED FIRST, BUILT SECOND. eval/layout_overlap.py harvested 10,941 stored
residuals and attributed 1,028 constraint sets to named structs:

    RacePlayer                        13 functions, 15 offsets
    gRacePlayers                       3 functions,  4 offsets
    RaceUiTimeTrialRecordDeltaPopup    3 functions,  3 offsets
    ...
    7 structs pinned by more than one function, 24 by exactly one
    ZERO width conflicts across functions

Zero conflicts is the part that licenses this. Had different functions
disagreed about a field, pooling would inject errors into every one of them.

WHY THIS IS EVIDENCE AND NOT INFERENCE
    A PRODUCED offset describes whatever struct one attempt happened to
    declare and means nothing elsewhere. An EXPECTED offset is read from the
    target binary, so "this struct has a field at 0x1c" is a fact about the
    ROM. Only expected offsets and widths are pooled, and an offset with
    conflicting widths is dropped rather than resolved -- unknown is the
    default.

    The store is EMPTY unless a caller loads it. Nothing changes behaviour by
    merely importing this, which keeps the generator inert until an experiment
    deliberately switches it on.
"""

from __future__ import annotations

from collections import defaultdict

# {struct name -> {expected offset -> width or None}}
_STORE: dict[str, dict[int, int | None]] = {}
_SOURCES: dict[str, set[str]] = defaultdict(set)
# Which functions pinned each individual offset, so one target's own history
# can be excluded without rescanning the whole attempts table per function.
# Reloading per function was the obvious implementation and would have cost a
# full 10,941-row scan for each of 41 targets.
_OFFSET_SOURCES: dict[tuple, set[str]] = defaultdict(set)
_EXCLUDE = ""


def loaded() -> bool:
    return bool(_STORE)


def clear() -> None:
    global _EXCLUDE
    _STORE.clear()
    _SOURCES.clear()
    _OFFSET_SOURCES.clear()
    _EXCLUDE = ""


def set_exclusion(function: str) -> None:
    """Ignore evidence contributed by this function when reading the store."""
    global _EXCLUDE
    _EXCLUDE = function or ""


def stats() -> dict:
    return {
        "structs": len(_STORE),
        "offsets": sum(len(v) for v in _STORE.values()),
        "multi_function": sum(1 for k in _STORE if len(_SOURCES[k]) > 1),
    }


def record(struct_name: str, offset: int, width: int | None,
           function: str = "") -> None:
    """Add one observed expected offset for a struct."""
    slot = _STORE.setdefault(struct_name, {})
    if offset in slot and slot[offset] is not None and width is not None \
            and slot[offset] != width:
        slot[offset] = None            # conflicting widths: width unknown
    elif offset not in slot or slot[offset] is None:
        slot[offset] = width
    if function:
        _SOURCES[struct_name].add(function)
        _OFFSET_SOURCES[(struct_name, offset)].add(function)


def offsets_for(struct_name: str) -> list[int]:
    """Offsets pinned by at least one function other than the excluded one."""
    slot = _STORE.get(struct_name, {})
    if not _EXCLUDE:
        return sorted(slot)
    keep = []
    for off in slot:
        srcs = _OFFSET_SOURCES.get((struct_name, off), set())
        if srcs - {_EXCLUDE}:
            keep.append(off)
    return sorted(keep)


def functions_for(struct_name: str) -> set[str]:
    return set(_SOURCES.get(struct_name, ())) - ({_EXCLUDE} if _EXCLUDE else set())


def load_from_db(conn, exclude_function: str = "", limit: int = 0) -> dict:
    """Populate the store from the attempts table's stored residuals.

    `exclude_function` drops a target's OWN history, so a function is never
    repaired using constraints derived from its own earlier attempts. That is
    not contamination in the ground-truth sense -- those constraints came from
    the binary either way -- but it would make any measurement of cross
    function transfer circular, which is the thing being tested.
    """
    from solver import diffrepair          # local: avoids an import cycle

    clear()
    sql = ("select f.name, a.source_code, a.diff_summary from attempts a"
           " join functions f on f.addr = a.func_addr"
           " where a.diff_summary is not null and length(a.diff_summary) > 50"
           "   and a.source_code is not null and a.compiled = 1")
    if limit:
        sql += f" limit {limit}"

    for name, src, diff in conn.execute(sql):
        if exclude_function and name == exclude_function:
            continue
        try:
            regions = diffrepair._struct_regions(src)
            if not regions:
                continue
            sizes = diffrepair.type_sizes(src)
            named = []
            for r in regions:
                m = diffrepair.STRUCT_NAME.match(src, r[1])
                if m:
                    named.append((m.group("name"), r))
            if not named:
                continue
            sets = diffrepair.constraint_sets(diff)
            widths = diffrepair.width_constraints(diff)
        except Exception:                          # noqa: BLE001
            continue

        for _base, mapping in sets:
            for struct_name, region in named:
                try:
                    offs = {o for _m, o, _s in
                            diffrepair.region_fields(src, region, sizes)}
                except Exception:                  # noqa: BLE001
                    continue
                if not offs or not set(mapping).issubset(offs):
                    continue
                for produced, expected in mapping.items():
                    w = widths.get(produced, (None, None))[0]
                    record(struct_name, expected, w, name)
                break
    return stats()
