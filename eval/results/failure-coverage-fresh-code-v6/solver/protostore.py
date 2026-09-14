"""Verified callee signatures, harvested from byte-exact sources.

WHY A SIGNATURE IS WORTH PROPAGATING
    A callee's prototype changes the CALLER's codegen. A function returning s16
    makes the caller sign-extend where u16 does not; a parameter declared u8
    marshals differently from s32. So a caller that declares `extern void f();`
    against a callee that really returns `s16` is wrong before any of its own
    logic is considered, and no amount of struct repair fixes it.

    The project has never used this. It has 9,761 resolved call edges and 138
    byte-exact functions, and no caller has ever been given a callee's verified
    signature. That gap is why the work queue was ordered by byte score rather
    than by dependency, and why an 8-instruction leaf blocking 15 callers had
    never been attempted.

WHERE THE SIGNATURES COME FROM, AND THE HONESTY BOUNDARY
    Only from functions the ORACLE has verified byte-exact, so a signature is
    a fact about code that provably compiles to the ROM.

    But 55 of those 138 were RECOVERED from the target repository rather than
    solved, and a signature taken from one of those is reference source. The
    user's rule permits sibling source, and a callee is a sibling rather than
    the target, so this is inside the line -- but a measured gain would then be
    partly attributable to the reference decomp rather than to the system.
    Recovered sources are therefore EXCLUDED BY DEFAULT and must be asked for.

    Nothing is inferred here. A signature is copied verbatim from source that
    matched, or it is absent.
"""

from __future__ import annotations

import re

# `void *getRelocatableHeapBlockBase(s32 handle) {` and friends. Anchored at
# line start so a call inside a body is never mistaken for a definition.
# The separator between return type and name is REQUIRED -- either whitespace
# or a pointer star. Without it the engine happily split a single identifier,
# reading `f32 __MusIntPowerOf2(f32 arg0) {` as ret=`f32 __MusIntPowerO`,
# name=`f2`, so 82 of 83 verified sources yielded no signature and the store
# reported one entry as though there were simply nothing to harvest.
DEFINITION = re.compile(
    r"^(?P<ret>[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)(?P<ptr>\s*\**)\s*"
    r"(?<![\w])(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{)]*)\)\s*\{?\s*$",
    re.M)

RECOVERY_STRATEGIES = ("history-recovery", "historical-provenance",
                       "symbol-restoration")

_STORE: dict[str, dict] = {}


def clear() -> None:
    _STORE.clear()


def loaded() -> bool:
    return bool(_STORE)


def stats() -> dict:
    return {"signatures": len(_STORE)}


def signature(name: str) -> dict | None:
    return _STORE.get(name)


def parse_definition(code: str, name: str) -> dict | None:
    """The verified definition of `name` in `code`, as declared."""
    for m in DEFINITION.finditer(code.replace("\n{", " {")):
        if m.group("name") != name:
            continue
        params = " ".join(m.group("params").split())
        base = " ".join(m.group("ret").split())
        stars = m.group("ptr").strip()
        if not base or base in ("return", "else", "if", "while", "for"):
            continue
        # Spelled the way C sources actually write it: `void *name`, never
        # `void* name` or `void * name`. A spelling difference alone would make
        # an already-correct declaration compare unequal and be rewritten on
        # every iteration of the search.
        return {"name": name, "ret": (base + " " + stars).strip(),
                "params": params,
                "prototype": f"{base} {stars}{name}({params or 'void'});"}
    return None


def record(name: str, sig: dict) -> None:
    _STORE.setdefault(name, sig)


def load_from_db(conn, include_recovered: bool = False) -> dict:
    """Harvest signatures of every byte-exact function.

    `include_recovered` admits functions whose exact source came from the
    target repository. Off by default so a measured gain is attributable to
    what the system solved.
    """
    clear()
    rows = conn.execute(
        "select f.name, a.source_code, group_concat(a.strategy) from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.exact = 1 and a.source_code is not null"
        " group by f.name").fetchall()
    for name, src, strategies in rows:
        if not include_recovered and any(
                s in (strategies or "") for s in RECOVERY_STRATEGIES):
            continue
        sig = parse_definition(src, name)
        if sig:
            record(name, sig)
    return stats()
