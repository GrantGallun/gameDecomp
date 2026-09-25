"""A per-function type context built from the binary alone (struct-identity-20260924 facts, variant D16).

Nothing here reads reference source or headers: struct layouts are observed offsets/widths/signedness merged across
the identity groups, pointer fields come from structural children, prototypes from binary arity (argument registers
read before written), globals from accesses inside each symbol's ELF extent. Names are placeholders (`T<n>`,
`unkXX`), as invariant 5 requires. Symbol names are the ones target.s already uses.

    decls(function) -> C declarations (after `#include "common.h"`) for m2c context and for the standalone compile
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "struct-identity-20260924"))
import score as ident  # noqa: E402

ELF = Path.home() / "decomp/sbk1/build/snowboardkids.elf"
PRIM = {(1, 1): "s8", (1, 0): "u8", (2, 1): "s16", (2, 0): "u16", (4, 1): "s32", (4, 0): "u32"}
FPU_ARGS = re.compile(r"\$?f1[24]\b")


@lru_cache(maxsize=1)
def model():
    rows = json.loads((ident.E / "facts.json").read_text())
    chosen = json.loads((Path(ident.__file__).parent / "score.json").read_text())["chosen_C"]   # selected on FIT
    uf = ident.solve(rows, chosen[0], int(chosen[1:]))
    obs = collections.defaultdict(list)                 # root -> [(off, width, signed, is_load, cls)]
    for r in rows:
        for node, off, width, signed, is_load, cls in r["accesses"]:
            if width:
                obs[uf.find(ident.tup(node))].append((off, width, signed, is_load, cls))
    by_name = {r["function"]: r for r in rows}
    name_of = {r["addr"]: r["function"] for r in rows}
    syms = {}
    out = subprocess.run(["mips-linux-gnu-nm", "-S", str(ELF)], capture_output=True, text=True).stdout
    sized = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4:
            sized[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
        elif len(parts) == 3:
            syms.setdefault(parts[2], (int(parts[0], 16), 0))
    # many symbols are listed without a size; the build's `<name>.NON_MATCHING` alias carries it
    for name, (addr, size) in sized.items():
        base = name[:-len(".NON_MATCHING")] if name.endswith(".NON_MATCHING") else name
        if syms.get(base, (addr, 0))[1] == 0:
            syms[base] = (addr, size)
    # data placed in .text (display lists, tables) is `T` like code; only real functions are excluded later
    return uf, obs, by_name, name_of, syms, stack_args()


def stack_args() -> dict[str, int]:
    """name -> number of stack-passed arguments the function reads (loads at sp >= frame + 0x10)."""
    sys.path.insert(0, str(HERE.parents[2]))
    from miner import evidence as ev
    out = {}
    for fn in ev.disassemble(ELF):
        frame, top = 0, -1
        for insn in fn.insns[:4]:
            d = insn.decoded
            if d.getOpcodeName() == "addiu" and ev._dest_register(d) == "sp" and d.getProcessedImmediate() < 0:
                frame = -d.getProcessedImmediate()
                break
        for insn in fn.insns:
            d = insn.decoded
            if d.doesDereference() and d.doesLoad() and ev._reg_name(d.rs) == "sp":
                off = d.getProcessedImmediate() - frame
                if off >= 0x10:
                    top = max(top, off)
        if top >= 0x10:
            out[fn.name] = (top - 0x10) // 4 + 1
    return out


class Emitter:
    def __init__(self):
        self.uf, self.obs, self.by_name, self.name_of, self.syms, self.stack = model()
        self.obs = dict(self.obs)
        self.strides: dict[str, int] = {}
        self.ids, self.structs, self.order = {}, {}, []

    def typed(self, root) -> bool:
        return bool(self.obs.get(root)) or any(k[0] == "D" for k in self.uf.children.get(root, {}))

    def tname(self, root, depth=0) -> str:
        if root not in self.ids:
            self.ids[root] = f"T{len(self.ids)}"
            self.structs[root] = None
            self.structs[root] = self.body(root, depth)
            self.order.append(root)
        return self.ids[root]

    def body(self, root, depth) -> str:
        """Fields at observed offsets: most common width (ties: widest), naturally aligned, non-overlapping."""
        at = collections.defaultdict(list)
        for off, width, signed, is_load, cls in self.obs.get(root, []):
            if off >= 0 and off % width == 0:
                at[off].append((width, signed, is_load, cls))
        children = {k[1]: v for k, v in self.uf.children.get(root, {}).items() if k[0] == "D"}
        fields, end = [], 0
        for off in sorted(at):
            if off < end:
                continue
            ws = collections.Counter(w for w, *_ in at[off])
            order = sorted(ws, key=lambda w: (-ws[w], -w))
            if off > end:
                fields.append(f"u8 pad{end:X}[0x{off - end:X}];")
            members = []
            for n, width in enumerate(order):
                rows = [x for x in at[off] if x[0] == width]
                cls = "float" if any(c == "float" for *_, c in rows) else "int"
                loads = [s for w, s, l, c in rows if l and s is not None]
                signed = 1 if not loads or any(loads) else 0
                name = f"unk{off:X}" if n == 0 else f"unk{off:X}_{width}"
                child = children.get(off)
                if width == 4 and child is not None and depth < 3 and self.typed(self.uf.find(child)):
                    members.append(f"struct {self.tname(self.uf.find(child), depth + 1)} *{name};")
                elif cls == "float":
                    members.append(f"{'f32' if width == 4 else 'f64'} {name};")
                else:
                    members.append(f"{PRIM.get((width, signed), 'u8')} {name};" if width in (1, 2, 4)
                                   else f"u8 {name}[{width}];")
            # several widths at one offset (unions, or a word and its halves): one union, all members at `off`
            fields.append(members[0] if len(members) == 1 else "union { " + " ".join(members) + " };")
            end = off + max(order)
        return " ".join(fields) or "u8 pad0[4];"

    def ptr_type(self, node) -> str:
        root = self.uf.find(node)
        return f"struct {self.tname(root)} *" if self.typed(root) else "s32 "

    def prototype(self, fn: str) -> str | None:
        r = self.by_name.get(fn)
        if r is None:
            return None
        reads = r.get("arity_reads") or []
        n = max(reads) + 1 if reads else 0
        ret_root = self.uf.find(("R", r["addr"]))
        ret = f"struct {self.tname(ret_root)} *" if self.typed(ret_root) else "s32 "
        extra = self.stack.get(fn, 0) if n == 4 else 0
        params = ", ".join([f"{self.ptr_type(('P', fn, k))}arg{k}" for k in range(n)] +
                           [f"s32 arg{4 + j}" for j in range(extra)]) or "void"
        ret_root = self.uf.find(("R", r["addr"]))
        ret = f"struct {self.tname(ret_root)} *" if self.typed(ret_root) else "s32 "
        return f"{ret}{fn}({params});"

    def global_decl(self, sym: str) -> str | None:
        if sym not in self.syms:
            return None
        addr, size = self.syms[sym]
        inside, elements = {}, {}
        for a in range(addr, addr + max(size, 1)):
            if ("G", a) in self.uf.parent:
                inside[a - addr] = self.uf.find(("G", a))
            if ("A", ("G", a)) in self.uf.parent:          # table elements reached as table + index (A4)
                elements[a - addr] = self.uf.find(("A", ("G", a)))
        stride = self.strides.get(sym)
        if elements and stride:
            # an array of an element struct: direct and indexed observations folded modulo the stride
            fake = ("GSYM", sym, stride)
            at = []
            for off, root in list(inside.items()) + list(elements.items()):
                for o, w, s, l, c in self.obs.get(root, []):
                    at.append(((off + o) % stride, w, s, l, c))
            self.obs[fake] = at
            count = size // stride if size and size % stride == 0 else 0
            return f"extern struct {self.tname(fake)} {sym}[{count or ''}];"
        if not inside:
            return f"extern u8 {sym}[{size}];" if size else f"extern u8 {sym}[];"
        if set(inside) == {0} and size <= 4:
            root = inside[0]
            kids = {k[1]: v for k, v in self.uf.children.get(root, {}).items() if k[0] == "D"}
            if size == 4 and 0 in kids and self.typed(self.uf.find(kids[0])):
                return f"extern struct {self.tname(self.uf.find(kids[0]))} *{sym};"
            ws = collections.Counter(w for _o, w, *_ in self.obs.get(root, []))
            loads = [s for _o, w, s, l, c in self.obs.get(root, []) if l and s is not None]
            width = max(ws, key=lambda w: (ws[w], w)) if ws else size
            return f"extern {PRIM.get((width, 1 if not loads or any(loads) else 0), 's32')} {sym};"
        # aggregate: synthesize one struct over the symbol's observed offsets; with a stride (from m2c's own
        # pass-one indexing `(&sym + (i * N))`), an array of an element struct folded modulo N
        fake = ("GSYM", sym, stride)
        at = []
        for off, root in inside.items():
            for o, w, s, l, c in self.obs.get(root, []):
                at.append(((off + o) % stride if stride else off + o, w, s, l, c))
        self.obs[fake] = at
        if stride and size and size % stride == 0:
            return f"extern struct {self.tname(fake)} {sym}[{size // stride}];"
        return f"extern struct {self.tname(fake)} {sym};"

    def render(self, head: list[str]) -> str:
        fwd = [f"typedef struct {self.ids[r]} {self.ids[r]};" for r in self.order]
        defs = [f"struct {self.ids[r]} {{ {self.structs[r]} }};" for r in self.order]
        return "\n".join(["/* Binary-derived types (struct-identity-20260924, FIT-chosen variant): offsets/widths are binary facts, names",
                          "   are placeholders. */", *fwd, *defs, *head]) + "\n"


def decls(function: str, target_s: str, strides: dict[str, int] | None = None) -> str:
    """Declarations for `function`: its prototype, callees' and address-taken functions' prototypes, globals."""
    em = Emitter()
    em.strides = dict(strides or {})
    symbols = set(re.findall(r"%(?:hi|lo)\(([A-Za-z_]\w*)", target_s)) | set(re.findall(r"\bjal\s+([A-Za-z_]\w*)", target_s))
    head, seen = [], set()
    fns = [function] + sorted(s for s in symbols if s in em.by_name and s != function)
    for fn in fns:
        if fn in seen:
            continue
        seen.add(fn)
        p = em.prototype(fn)
        if p:
            head.append(p)
    for s in sorted(symbols - set(em.by_name)):
        g = em.global_decl(s)
        if g:
            head.append(g)
    return em.render(head)


if __name__ == "__main__":
    fn = sys.argv[1]
    ts = (Path.home() / "decomp/sbk1/nonmatchings" / fn / "target.s").read_text()
    print(decls(fn, ts))
