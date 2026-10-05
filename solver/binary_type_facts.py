"""Binary-only observations for cross-function type inference.

The walk records accesses, store unifications, calls, returns, and arity.
All inferred layout choices belong to ``binary_type_identity`` instead.
"""
from __future__ import annotations

import collections
from pathlib import Path

from miner import evidence as ev

ARGS = ("a0", "a1", "a2", "a3")


def _control(d) -> bool:
    try:
        return bool(d.isBranch() or d.isJump())
    except Exception:
        return False


def _stable_sets(func, targets):
    """Registers and stack slots defined once, inside the entry block, never redefined: they survive merges."""
    entry_end = len(func.insns)
    for i, insn in enumerate(func.insns):
        if i and insn.addr in targets:
            entry_end = i
            break
        if _control(insn.decoded):
            entry_end = i + 2
            break
    reg_defs, reg_first = collections.Counter(), {}
    slot_defs, slot_first = collections.Counter(), {}
    for i, insn in enumerate(func.insns):
        d = insn.decoded
        r = ev._dest_register(d)
        if r:
            reg_defs[r] += 1
            reg_first.setdefault(r, i)
        if d.isFunctionCall():
            for c in ev.CALLER_SAVED:
                reg_defs[c] += 1
                reg_first.setdefault(c, i + 1)
        if d.doesDereference() and d.doesStore() and ev._reg_name(d.rs) == "sp":
            o = d.getProcessedImmediate()
            slot_defs[o] += 1
            slot_first.setdefault(o, i)
    regs = {r for r in reg_defs if reg_defs[r] == 1 and reg_first[r] < entry_end}
    regs |= {a for a in ARGS if reg_defs[a] == 0}
    slots = {o for o in slot_defs if slot_defs[o] == 1 and slot_first[o] < entry_end}
    return regs, slots


def _blocks(func, targets):
    """Basic blocks as (start, end) instruction-index ranges (end exclusive) and successor lists."""
    n = len(func.insns)
    idx = {insn.addr: i for i, insn in enumerate(func.insns)}
    leaders = {0}
    for i, insn in enumerate(func.insns):
        if insn.addr in targets:
            leaders.add(i)
        d = insn.decoded
        if _control(d) and not d.isFunctionCall() and i + 2 <= n:
            leaders.add(i + 2)          # after the delay slot
    starts = sorted(l for l in leaders if l < n)
    blocks = [(s, starts[k + 1] if k + 1 < len(starts) else n) for k, s in enumerate(starts)]
    at = {s: k for k, (s, _e) in enumerate(blocks)}
    succ = []
    for s, e in blocks:
        out, last = [], None
        for i in range(s, e):
            d = func.insns[i].decoded
            if _control(d) and not d.isFunctionCall():
                last = i
        if last is None:
            out.append(e)
        else:
            d = func.insns[last].decoded
            op = d.getOpcodeName()
            try:
                t = d.getBranchVramGeneric() if d.isBranch() else None
            except Exception:
                t = None
            if t is not None and t in idx:
                out.append(idx[t])
            try:
                same = ev._reg_name(d.rs) == ev._reg_name(d.rt)
            except RuntimeError:
                same = False
            is_uncond = op in ("b", "j", "jr") or (op == "beq" and same)
            if not is_uncond:
                out.append(e)
        succ.append([at[x] for x in out if x in at])
    return blocks, succ


def _meet(states):
    states = [s for s in states if s is not None]
    if not states:
        return None
    regs = {k: v for k, v in states[0][0].items() if all(st[0].get(k) == v for st in states[1:])}
    slots = {k: v for k, v in states[0][1].items() if all(st[1].get(k) == v for st in states[1:])}
    return regs, slots


def walk(func) -> dict:
    """Forward dataflow over basic blocks (A3): meet = values equal in every predecessor, iterated to a fixed point;
    facts are recorded in one final pass over the converged entry states."""
    f = func.name
    targets = ev.branch_targets(func)
    blocks, succ = _blocks(func, targets)
    preds = collections.defaultdict(list)
    for b, ss in enumerate(succ):
        for t in ss:
            preds[t].append(b)
    entry = ({a: ("tv", ("P", f, k), 0) for k, a in enumerate(ARGS)}, {})
    out_state = [None] * len(blocks)
    facts = {"accesses": [], "unify": [], "calls": [], "returns": []}

    def run(b, state, record):
        regs, slots = dict(state[0]), dict(state[1])
        call_target, call_after = None, None
        s, e = blocks[b]
        for i in range(s, e):
            d = func.insns[i].decoded
            op = d.getOpcodeName()
            if d.isFunctionCall():
                try:
                    call_target = d.getInstrIndexAsVram()
                except (RuntimeError, ValueError):
                    call_target = None
                call_after = i + 1
            if record and op == "jr" and ev._reg_name(d.rs) == "ra":
                v = regs.get("v0")
                if v and v[0] == "tv" and v[2] == 0:
                    facts["returns"].append(v[1])
            dest = ev._dest_register(d)
            new, handled = None, False
            if d.doesDereference():
                base = ev._reg_name(d.rs)
                try:
                    rt = ev._reg_name(d.rt)
                except RuntimeError:        # FPU loads/stores (lwc1/swc1) have no integer rt
                    rt = None
                imm = d.getProcessedImmediate()
                is_load = bool(d.doesLoad())
                if base == "sp":
                    if is_load and op == "lw":
                        new, handled = slots.get(imm), True
                    elif not is_load and op == "sw":
                        v = regs.get(rt)
                        if v is not None:
                            slots[imm] = v
                        else:
                            slots.pop(imm, None)
                else:
                    held = regs.get(base)
                    obj = None
                    if held and held[0] == "tv":
                        obj, off = held[1], held[2] + imm
                    elif held and held[0] in ("hi", "abs"):
                        obj, off = ("G", held[1] + imm), 0
                    elif held and held[0] == "arr":        # element of a table at a global address (A4)
                        obj, off = ("A", ("G", held[1])), held[2] + imm
                    if obj is not None:
                        if record:
                            access = str(d.getAccessType()).split(":")[-1].split("(")[0].strip(" >")
                            facts["accesses"].append([obj, off, ev.ACCESS_WIDTH.get(access),
                                                      (0 if d.doesUnsignedMemoryAccess() else 1) if is_load else None,
                                                      int(is_load), "float" if d.isFloat() else "int"])
                        if is_load and op == "lw":
                            new, handled = ("tv", ("D", obj, off), 0), True
                        elif not is_load and op == "sw" and record:
                            src = regs.get(rt)
                            if src and src[0] == "tv" and src[2] == 0:
                                facts["unify"].append([["D", obj, off], src[1]])
            if dest and not handled:
                src = ev._source_register(d)
                sv = regs.get(src) if src else None
                if op == "lui":
                    new = ("hi", (d.getProcessedImmediate() & 0xFFFF) << 16)
                elif op in ("addiu", "addi") and sv is not None:
                    imm = d.getProcessedImmediate()
                    new = (("abs", sv[1] + imm) if sv[0] == "hi" else
                           ("tv", sv[1], sv[2] + imm) if sv[0] == "tv" else
                           ("arr", sv[1], sv[2] + imm) if sv[0] == "arr" else None)
                elif op == "ori" and sv is not None and sv[0] == "hi":
                    new = ("abs", sv[1] | (d.getProcessedImmediate() & 0xFFFF))
                elif op in ("move", "addu", "or") and d.maybeIsMove() and sv is not None:
                    new = sv
                elif op == "addu":
                    # base + unknown index: an element pointer into a table at a global address (A4)
                    try:
                        other = regs.get(ev._reg_name(d.rt)) if src != ev._reg_name(d.rt) else None
                    except RuntimeError:
                        other = None
                    vals = [v for v in (sv, other) if v is not None]
                    if len(vals) == 1 and vals[0][0] == "abs":
                        new = ("arr", vals[0][1], 0)
            if dest:
                if new is None:
                    regs.pop(dest, None)
                else:
                    regs[dest] = new
            if call_after == i:
                if record and call_target is not None:
                    for k, a in enumerate(ARGS):
                        v = regs.get(a)
                        if v is None:
                            continue
                        if v[0] == "tv":
                            facts["calls"].append([call_target, k, v[1] if v[2] == 0 else ["E", v[1], v[2]]])
                        elif v[0] == "abs":
                            facts["calls"].append([call_target, k, ["G", v[1]]])
                for c in ev.CALLER_SAVED:
                    regs.pop(c, None)
                if call_target is not None:
                    regs["v0"] = ("tv", ("R", call_target), 0)
                call_target, call_after = None, None
        return regs, slots

    # Fixed point: under the meet, entry states only shrink, so this terminates; the round cap is a safety net.
    work, rounds = list(range(len(blocks))), 0
    while work and rounds < 50 * max(1, len(blocks)):
        rounds += 1
        b = work.pop(0)
        if b == 0:
            inp = entry
        elif not preds[b]:
            inp = ({}, {})
        else:
            inp = _meet([out_state[p] for p in preds[b]])
            if inp is None:
                continue
        res = run(b, inp, False)
        if res != out_state[b]:
            out_state[b] = res
            work += [t for t in succ[b] if t not in work]
    for b in range(len(blocks)):
        inp = entry if b == 0 else (_meet([out_state[p] for p in preds[b]]) or ({}, {}))
        run(b, inp, True)
    return {"function": f, "addr": func.addr, **facts, "arity_reads": _arg_reads(func)}


def _arg_reads(func) -> list[int]:
    """Argument registers the function reads before writing them (linear order): its binary arity evidence."""
    seen_write, reads = set(), set()
    for insn in func.insns:
        d = insn.decoded
        srcs = []
        for attr, test in (("rs", "readsRs"), ("rt", "readsRt")):
            try:
                if getattr(d, test)():
                    srcs.append(ev._reg_name(getattr(d, attr)))
            except (RuntimeError, AttributeError):
                pass
        for r in srcs:
            if r in ARGS and r not in seen_write:
                reads.add(ARGS.index(r))
        dest = ev._dest_register(d)
        if dest in ARGS:
            seen_write.add(dest)
    return sorted(reads)


def facts(elf: Path) -> list[dict]:
    """Extract per-function observations from the caller's explicit target ELF."""
    return [walk(fn) for fn in ev.disassemble(elf)]
