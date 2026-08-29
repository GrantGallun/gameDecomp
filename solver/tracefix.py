"""Repair a candidate's call arguments from the symbolic trace.

Not a prompt hint. The trace does not SUGGEST the call, it STATES it:

    CALL getRaceCourseSurfaceType(param0->f502, param0->f1c, param0->f24)

so the argument expressions can be written into the candidate directly, with no
model involved and the oracle verifying the result. That is the same shape as
repad, which fixed a struct problem that every model-based attempt failed on.

Measuring this as "does the prompt help the model" would have been the eighth
prompt-content experiment after seven nulls, and it would have tested the
weakest use of the trace.

Conservative by construction: a call is only rewritten when the candidate has
the same callee with the same argument count, and every traced argument maps to
an expression the candidate can name. Anything else is left alone.
"""

from __future__ import annotations

import re

from solver import symtrace

CALL_EV = re.compile(r"^CALL (\w+)\((.*?)\)")
TYPEWORD = re.compile(r"(s8|u8|s16|u16|s32|u32|f32|f64|int|char|void|long|short|struct|unsigned|extern)")


def traced_calls(asm: str) -> dict[str, list[str]]:
    """{callee: [arg expressions]} from the symbolic trace."""
    out: dict[str, list[str]] = {}
    for ev in symtrace.trace(asm, max_events=400):
        m = CALL_EV.match(ev)
        if not m:
            continue
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
        out.setdefault(m.group(1), args)
    return out


def _to_c(expr: str, ptr: str) -> str | None:
    """Turn a trace expression into C, or None if it cannot be named.

    param0->f1c  ->  ptr->field1C          (field naming is caller-supplied)
    &gSymbol     ->  gSymbol / &gSymbol
    stack[0x18]  ->  None -- a spill slot has no name in the source
    """
    m = re.match(r"param(\d+)->f([0-9a-f]+)$", expr)
    if m:
        return f"{ptr}->@{int(m.group(2), 16):x}"
    if expr.startswith("&"):
        return expr
    if re.match(r"param\d+$", expr):
        return ptr
    return None


def fix_calls(code: str, asm: str, ptr_name: str = "") -> tuple[str, list[str]]:
    """Rewrite call arguments in `code` to match the trace. Returns (code, log).

    Field references are resolved against the struct the candidate already
    declares, by matching each offset to a field whose NAME encodes it -- the
    same convention repad relies on. A field that cannot be resolved aborts
    that call, rather than being invented.
    """
    log: list[str] = []
    if not ptr_name:
        m = re.search(r"\(\s*\w+\s*\*\s*(\w+)\s*\)", code)
        ptr_name = m.group(1) if m else "param0"

    # offset -> field name, from the candidate's own struct
    fields: dict[int, str] = {}
    for m in re.finditer(r"\b(?:s8|u8|s16|u16|s32|u32|f32)\s+(\w+)\s*;", code):
        name = m.group(1)
        if name.lstrip("_").lower().startswith("pad"):
            continue
        tail = ""
        for ch in reversed(name):
            if ch in "0123456789abcdefABCDEF":
                tail = ch + tail
            else:
                break
        # Register EVERY trailing-hex reading, not just the longest. "field502"
        # yields tail "d502" because the d of "field" is itself a hex digit, so
        # taking the first parse records offset 0xd502 and never tries 0x502 --
        # the lookup then failed and the call was left alone. repad solves this
        # by disambiguating against observed offsets; there is no such set here,
        # so all readings are kept and the caller's offset selects one.
        for i in range(len(tail)):
            try:
                fields.setdefault(int(tail[i:], 16), name)
            except ValueError:
                continue

    for callee, targs in traced_calls(asm).items():
        # Skip the PROTOTYPE. Matching the first occurrence rewrote
        # `extern s32 f(s32 a0, s32 a1, s32 a2);` into
        # `extern s32 f(player->field502, ...)`, which is not C. The same bug
        # was fixed in argperm an hour earlier and not carried across; these
        # two call-site finders should be one shared helper.
        call = None
        for m in re.finditer(rf"\b{re.escape(callee)}\s*\(([^();]*)\)", code):
            if TYPEWORD.search(m.group(1)):
                continue
            call = m
            break
        if not call:
            continue
        have = [a.strip() for a in call.group(1).split(",") if a.strip()]
        # `f(void)` takes ZERO arguments. Treating "void" as one argument made
        # this rewrite initGameSystems(void) into initGameSystems(arg0),
        # inventing a parameter the call does not have.
        if have == ["void"]:
            have = []
        # The trace may carry one more argument than the call really has: a
        # scratch copy like `move a3,a0` is indistinguishable from a fourth
        # argument from inside the function. Trust the CANDIDATE's arity and
        # fix only the expressions and their order.
        while len(targs) > len(have) and targs and re.match(r"param\d+$",
                                                            targs[-1]):
            targs = targs[:-1]
        if len(have) != len(targs):
            log.append(f"{callee}: {len(have)} args in code vs {len(targs)} "
                       f"traced -- left alone")
            continue
        if not have:
            continue

        newargs, ok = [], True
        for t in targs:
            c = _to_c(t, ptr_name)
            if c is None:
                ok = False
                break
            m2 = re.match(rf"{re.escape(ptr_name)}->@([0-9a-f]+)$", c or "")
            if m2:
                off = int(m2.group(1), 16)
                if off not in fields:
                    ok = False
                    break
                c = f"{ptr_name}->{fields[off]}"
            newargs.append(c)
        if not ok:
            log.append(f"{callee}: an argument could not be named -- left alone")
            continue

        if newargs != have:
            code = code.replace(call.group(0),
                                f"{callee}({', '.join(newargs)})", 1)
            log.append(f"{callee}: ({', '.join(have)}) -> "
                       f"({', '.join(newargs)})")
    return code, log
