"""Candidate declarations inferred from binary observations, never reference C.

The D32 identity policy and emitter were measured in the September 24 experiment.
D32 was selected on historical FIT labels; production never loads those labels.
Layouts/signatures remain compiler-checked hypotheses, not new KB evidence.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


POLICY = "D32"
PRIM = {(1, 1): "s8", (1, 0): "u8", (2, 1): "s16", (2, 0): "u16", (4, 1): "s32", (4, 0): "u32"}


class Unavailable(ValueError):
    """The binary cannot support a candidate context on this route."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def algorithm_digest() -> str:
    digest = hashlib.sha256()
    for name in ("binary_type_context.py", "binary_type_facts.py", "binary_type_identity.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    digest.update((Path(__file__).resolve().parent.parent / "miner" / "evidence.py").read_bytes())
    return digest.hexdigest()


def find_elf(repo: Path) -> Path:
    candidates = sorted((repo / "build").glob("*.elf"))
    if len(candidates) != 1:
        raise Unavailable(f"requires one built target ELF; found {len(candidates)}")
    return candidates[0]


def _extract(elf: Path) -> dict:
    from miner import evidence as ev
    from solver import binary_type_facts
    functions = ev.disassemble(elf)
    rows = [binary_type_facts.walk(fn) for fn in functions]
    stack = {}
    for fn in functions:
        frame, top = 0, -1
        for insn in fn.insns[:4]:
            d = insn.decoded
            if d.getOpcodeName() == "addiu" and ev._dest_register(d) == "sp" and d.getProcessedImmediate() < 0:
                frame = -d.getProcessedImmediate()
                break
        for insn in fn.insns:
            d = insn.decoded
            if d.doesDereference() and d.doesLoad() and ev._reg_name(d.rs) == "sp":
                offset = d.getProcessedImmediate() - frame
                if offset >= 0x10:
                    top = max(top, offset)
        if top >= 0x10:
            stack[fn.name] = (top - 0x10) // 4 + 1
    proc = subprocess.run(["mips-linux-gnu-nm", "-S", str(elf)], capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise Unavailable("binary symbol extraction failed: " + proc.stderr[-500:])
    symbols, sized = {}, {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        try:
            if len(parts) == 4:
                sized[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
            elif len(parts) == 3:
                symbols.setdefault(parts[2], (int(parts[0], 16), 0))
        except ValueError:
            continue
    for name, (address, size) in sized.items():
        base = name.removesuffix(".NON_MATCHING")
        if symbols.get(base, (address, 0))[1] == 0:
            symbols[base] = (address, size)
    return {"rows": rows, "symbols": symbols, "stack_args": stack}


def load(elf: Path, cache_root: Path | None = None) -> "ContextModel":
    elf = Path(elf)
    if not elf.is_file():
        raise Unavailable("target ELF unavailable")
    with elf.open("rb") as stream:
        binary_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    algorithm = algorithm_digest()
    root = cache_root or elf.parent / ".gamedecomp-binary-types"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{binary_sha}-{algorithm}.json"
    key = {"elf_sha256": binary_sha, "algorithm_sha256": algorithm, "policy": POLICY}
    bundle = None
    status = "miss"
    if path.exists():
        status = "rebuilt"
        try:
            cached = json.loads(path.read_text())
            candidate = cached["bundle"]
            if cached["key"] == key and cached["bundle_sha256"] == _digest(candidate):
                bundle, status = candidate, "hit"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if bundle is None:
        bundle = _extract(elf)
        payload = {"key": key, "bundle": bundle, "bundle_sha256": _digest(bundle)}
        # Independent workers may derive the same cache entry. Atomic replacement
        # makes each complete equivalent observation bundle visible as a unit.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(payload, stream, separators=(",", ":"))
        try:
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    return ContextModel(bundle, {**key, "elf": str(elf), "facts_sha256": _digest(bundle),
                                 "cache_status": status})


class ContextModel:
    def __init__(self, bundle: dict, evidence: dict):
        from solver import binary_type_identity as identity
        rows = bundle["rows"]
        self.uf = identity.solve(rows)
        self.obs = collections.defaultdict(list)
        for row in rows:
            for node, offset, width, signed, is_load, cls in row["accesses"]:
                if width:
                    self.obs[self.uf.find(identity.tup(node))].append((offset, width, signed, is_load, cls))
        self.by_name = {row["function"]: row for row in rows}
        self.symbols = bundle["symbols"]
        self.stack = bundle["stack_args"]
        self.evidence = dict(evidence)

    def context(self, function: str, target_assembly: str, strides: dict | None = None) -> dict:
        if function not in self.by_name:
            raise Unavailable(f"function {function} missing binary observations")
        if not re.fullmatch(r"[A-Za-z_]\w*", function):
            raise Unavailable("unsupported binary function identifier")
        em = _Emitter(self)
        em.strides = dict(strides or {})
        if any(not re.fullmatch(r"[A-Za-z_]\w*", name) or type(size) is not int or not 0 < size <= 0x10000
               for name, size in em.strides.items()):
            raise Unavailable("invalid generated array stride")
        symbols = set(re.findall(r"%(?:hi|lo)\(([A-Za-z_]\w*)", target_assembly))
        symbols |= set(re.findall(r"\bjal\s+([A-Za-z_]\w*)", target_assembly))
        head = []
        for fn in [function] + sorted((symbols & set(self.by_name)) - {function}):
            prototype = em.prototype(fn)
            if prototype:
                head.append(prototype)
        own = head[0]
        for name in sorted(symbols - set(self.by_name)):
            declaration = em.global_decl(name)
            if declaration:
                head.append(declaration)
        declarations = em.render(head)
        evidence = {**self.evidence, "policy": POLICY, "function": function,
                    "function_address": self.by_name[function]["addr"],
                    "target_assembly_sha256": hashlib.sha256(target_assembly.encode()).hexdigest(),
                    "declarations_sha256": hashlib.sha256(declarations.encode()).hexdigest(),
                    "authority": "binary observations; identity/layout/signatures are compiler-checked hypotheses",
                    "reference_source_used": False, "strides": em.strides}
        return {"declarations": declarations, "own_prototype": own, "evidence": evidence}


class _Emitter:
    def __init__(self, model):
        self.uf, self.obs, self.by_name = model.uf, dict(model.obs), model.by_name
        self.syms, self.stack = model.symbols, model.stack
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

