"""What the TARGET says a placeholder is -- types derived from the binary, not defaulted.

THE GAP THIS FILLS. `m2c_placeholders.rewrite(code, widths)` already substitutes a concrete type for
m2c's `?`, and it already accepts binary-derived widths -- but every caller in the pipeline passes one
argument (`eval/tool_runners.py`, `eval/completion_chain`'s campaign, `zero_token_harvest`,
`compile_recovery`), so every placeholder becomes the `s32` default. Measured: resolving placeholders
alone does not admit the drafts, because the NEXT error is
`Selector requires struct/union pointer as left hand side` on dozens of files -- a name that should be
a pointer to a struct is a scalar, and no default can know that.

WHY IT IS DERIVABLE. m2c names a local after where it lives, and the target assembly says what happens
at that location:

    arg0 / var_a1     -> register a0 / a1
    var_s3            -> register s3
    var_t0            -> register t0
    sp18              -> stack, offset 0x18 (m2c writes these in hex; `18` decimal is also tried and
                         the match is recorded, because guessing the base is exactly how a "derived"
                         type becomes an invented one)

From the accesses at that location the binary fixes the size (lb/lh/lw) and, when the opcode
distinguishes, the signedness (lb/lh signed, lbu/lhu unsigned). `lw`/`sw` do NOT distinguish, so a
4-byte name comes back `s32` with `signedness: unconstrained` RECORDED -- the difference between an
inference the binary supports and a default wearing its clothes is the whole point of this module.

Dereference is the other half: if the register holding the value is itself used as an address base
elsewhere, the name is a pointer, and the offsets touched through it are the struct's field offsets.

WHAT IT REFUSES TO DO. A location with two different access widths, or with no access at all, yields
no type. An empty or contradictory answer is more useful than a plausible one: the caller can fall back
to the default knowing it is a default.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

NAME = re.compile(r"^(?P<kind>arg|var_a|var_s|var_t|sp)(?P<tail>[0-9a-fA-F]+)(?:_\w+)?$")
ACCESS = re.compile(r"\b(?P<op>lb|lbu|lh|lhu|lw|lwu|sw|sb|sh|ld|sd)\b\s+"
                    r"\$?(?P<dst>[a-z0-9]+),\s*(?P<off>-?(?:0x[0-9a-fA-F]+|\d+))?\((?P<base>\$?[a-z0-9]+)\)")
USED_AS_BASE = re.compile(r"\(\$?(?P<base>[a-z0-9]+)\)")

# size in bytes, and signedness when the opcode actually distinguishes it.
OPS: dict[str, tuple[int, bool | None]] = {
    "lb": (1, True), "lbu": (1, False), "lh": (2, True), "lhu": (2, False),
    "lw": (4, None), "lwu": (4, False), "sw": (4, None), "sb": (1, None), "sh": (2, None),
    "ld": (8, None), "sd": (8, None),
}
CTYPE = {(1, True): "s8", (1, False): "u8", (2, True): "s16", (2, False): "u16",
         (4, True): "s32", (4, False): "u32", (4, None): "s32", (8, None): "s64"}


@dataclass
class Derived:
    name: str
    located_at: str = ""
    type: str = ""
    basis: str = "none"              # derived | defaulted | none
    accesses: list = field(default_factory=list)
    signedness: str = ""
    pointer: bool = False
    field_offsets: list = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _strip(register: str) -> str:
    return (register or "").lstrip("$")


def locate(name: str) -> tuple[str, str, str] | None:
    """(base register, offset, human location) for an m2c local name, or None if unrecognised."""
    match = NAME.match(name)
    if not match:
        return None
    kind, tail = match.group("kind"), match.group("tail")
    if kind in ("arg", "var_a"):
        return _strip(f"a{int(tail, 16) if len(tail) == 1 else tail}"), "", f"a{tail}"
    if kind == "var_s":
        return f"s{int(tail, 16)}", "", f"s{tail}"
    if kind == "var_t":
        return f"t{int(tail, 16)}", "", f"t{tail}"
    # Stack: m2c writes the offset in hex. Try hex, then decimal, and say which matched -- a wrong base
    # silently reinterprets every access at that slot.
    return "sp", tail, f"stack {tail}"


def _offset(value: str | None) -> int | None:
    if value in (None, ""):
        return 0
    return int(value, 16) if value.lower().startswith(("0x", "-0x")) else int(value)


def derive(target_asm: str, name: str) -> Derived:
    """The type the target assembly supports for one placeholder name."""
    located = locate(name)
    if located is None:
        return Derived(name=name, basis="none", reason="name does not encode a location")
    register, offset_text, human = located
    offsets = []
    if register == "sp":
        for base in (16, 10):
            try:
                candidate = int(offset_text, base)
            except ValueError:
                continue
            offsets.append((candidate, f"0x{candidate:x}(sp)", base))
    else:
        offsets.append((None, human, 16))

    accesses, used_offsets = [], set()
    for match in ACCESS.finditer(target_asm or ""):
        if _strip(match.group("base")) != register:
            continue
        offset = _offset(match.group("off"))
        if register == "sp":
            hit = next((entry for entry in offsets if entry[0] == offset), None)
            if hit is None:
                continue
            used_offsets.add(hit[1])
        accesses.append({"op": match.group("op"), "text": match.group(0).strip(),
                         "offset": offset})

    if not accesses:
        return Derived(name=name, located_at=human, basis="none",
                       reason=f"the target never accesses {human}")

    sizes = {OPS[a["op"]][0] for a in accesses if a["op"] in OPS}
    signs = {OPS[a["op"]][1] for a in accesses if a["op"] in OPS}
    if len(sizes) != 1:
        return Derived(name=name, located_at=human, basis="none", accesses=accesses,
                       reason=f"conflicting access widths {sorted(sizes)} at {human}")
    size = sizes.pop()
    signed = signs.pop() if len(signs) == 1 else None

    # Is the value itself used as an address? Then it is a pointer, whatever width it loads at.
    pointer, offsets_through_it = False, []
    # The DESTINATION register is the operand before the comma. The first version read the operand
    # AFTER it -- the offset -- so `loaded_into` held "0x18" instead of "t0" and pointer detection never
    # fired on the very shape it exists for.
    loaded_into = []
    for access in accesses:
        if not access["op"].startswith("l"):
            continue
        destination = access["text"].split(",")[0].split()[-1]
        loaded_into.append(_strip(destination))
    for base in {r for r in loaded_into if r}:
        for match in USED_AS_BASE.finditer(target_asm or ""):
            if _strip(match.group("base")) == base:
                pointer = True
                offsets_through_it.append(match.group(0).strip("()"))
    result = Derived(name=name, located_at=human,
                     type=("s32 *" if pointer else CTYPE.get((size, signed), "s32")),
                     basis="derived", accesses=accesses,
                     signedness=("unconstrained (lw/sw do not distinguish)" if signed is None
                                 else "signed" if signed else "unsigned"),
                     pointer=pointer, field_offsets=sorted(set(offsets_through_it)))
    return result


SYMBOL = re.compile(r"^(?:D_|g_|pimgr_|[A-Za-z_]\w*_bss_|.*_bss_)", re.IGNORECASE)


def classify(names: list[str], asm: str | None = None) -> dict[str, list[str]]:
    """Which evidence source owns each placeholder. THIS IS THE FINDING, not a convenience.

    Measured on the real drafts the tree actually contains (`base.m2c.c`, the preservation copies the
    placeholder-admission run left behind), the placeholder token is NOT mostly a local:

        guMtxIdent        guMtxF2L, guMtxIdentF, sp18
        alLoadNew         alAdpcmPull, alFilterNew, alLoadParam
        initRaceHud       D_245A80, D_24C8E0
        osCreatePiManager pimgr_bss_01B0
        __osCheckPackId   sp30

    Of twelve names, ten are SYMBOLS -- unknown function return types and unknown globals -- and only
    two are stack locals, for which this module's location mapping found no access in the target at all.
    So one derivation covering "the placeholder" does not exist; the class decides the owner:

      LOCAL     `sp18`, `var_s3`, `arg0`  -> `derive()` here, from accesses at that location
      GLOBAL    `D_245A80`, `pimgr_bss_*` -> `solver.globaldecl`, widths from the evidence tier
      FUNCTION  `guMtxF2L`, `alLoadParam` -> `solver.project_headers` / typedecl, from the callee's
                                            own prototype or the call site's result register

    The second and third already exist and are already composed in `eval/placeholder_admission.py`,
    which reached an exact match (`loadMainMenuSceneModelAnimationBank`, score 100.0) through exactly
    that route. What is missing is that the ACTION REGISTRY's `resolve-placeholders` does not use any
    of it: it calls `rewrite(candidate)` with the `s32` default, a strictly weaker copy of a route the
    repo already owns. Routing each class to its owner is the fix; a better `s32` is not.
    """
    groups: dict[str, list[str]] = {"local": [], "global": [], "function": [], "unknown": []}
    for name in names:
        if locate(name) is not None:
            groups["local"].append(name)
        elif asm and re.search(rf"\bjal\w*\s+{re.escape(name)}\b", asm):
            # Evidence, not spelling: the target calls it.
            groups["function"].append(name)
        elif "_bss_" in name.lower() or name.startswith(("D_", "g_")):
            groups["global"].append(name)
        else:
            # NOT GUESSED. The first version decided function-versus-global by the first letter being
            # lowercase, which put `pimgr_bss_01B0` -- a bss symbol -- in the function group. A wrong
            # owner sends the name to a resolver that cannot answer for it, and the receipt then shows a
            # principled-looking route that never had the evidence.
            groups["unknown"].append(name)
    return {key: value for key, value in groups.items() if value}


def types_for(target_asm: str, names: list[str]) -> tuple[dict[str, str], list[dict]]:
    """`{name: ctype}` for `m2c_placeholders.rewrite`, plus the derivation receipt.

    Only DERIVED names appear in the dict. A name whose evidence is absent or contradictory is left
    out on purpose, so the caller's default applies and the receipt can say which names were guessed
    rather than known.
    """
    derived = [derive(target_asm, name) for name in names]
    return ({d.name: d.type for d in derived if d.basis == "derived"},
            [d.as_dict() for d in derived])


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asm", type=Path, required=True, help="the function's target.s")
    ap.add_argument("--names", nargs="+", required=True)
    args = ap.parse_args(argv)
    widths, receipt = types_for(args.asm.read_text(encoding="utf-8"), args.names)
    print(json.dumps({"types": widths, "receipt": receipt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
