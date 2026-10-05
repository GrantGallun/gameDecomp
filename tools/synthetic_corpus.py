"""Generated C functions compiled by the game's own IDO recipe: verified pairs with known source.

Why this exists. Model training needs (assembly, C) pairs, and the obvious sources are all bad:
public decomps require ROMs to produce assembly, and the finished Snowboard Kids decomps on
decomp.dev ARE the held-out answers. A generator sidesteps both. Every pair is correct by
construction -- the C is the source, IDO produced the bytes -- no ROM is involved, and nothing
generated can contain an SBK1 body.

It also aims at a capability instead of sampling blindly. Each family targets one fault class from
`solver/signals.py` (a dense switch is a jump table; values live across a call need saved registers)
so a curriculum can spend compute where the model is weak.

Expectations are PROBES, not rules. Each family declares what it expects IDO to emit and the run
MEASURES how often that happens. A family that does not fire is a finding about the compiler or the
generator, reported in the receipt -- never silently accepted, per the fifth rule.

The generator is deterministic and LLM-free. Generated names are `syn_*` so a pair can never be
mistaken for, or collide with, a game symbol.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path.home() / "decomp/sbk1"
# Every game-code TU resolves to one recipe (-O2 -mips1, 1,846 functions, measured 2026-09-16).
# The largest TU is the default; the flags still come from the Makefile via compiler_recipe.
DEFAULT_TARGET = "build/src/race/ui/race_ui_effects.o"
OBJDUMP = "mips-linux-gnu-objdump"
SCHEMA_VERSION = 1
NAME_PREFIX = "syn_"

SAVED = {f"s{i}" for i in range(8)} | {"ra"}
BRANCH_OPS = {"b", "beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz",
              "beql", "bnel", "beqzl", "bnezl", "bgezl", "blezl", "bgtzl", "bltzl",
              "bc1t", "bc1f", "j"}


# --- generator ----------------------------------------------------------------

def _rng(family: str, seed: int) -> random.Random:
    return random.Random(f"{family}:{seed}")


def _const(r: random.Random) -> int:
    return r.choice([r.randint(1, 15), r.randint(16, 255), r.randint(256, 4095)])


def _expr(r: random.Random, names: list[str], depth: int = 0) -> str:
    """A small side-effect-free integer expression over `names`."""
    if depth > 1 or r.random() < 0.35:
        return r.choice(names) if r.random() < 0.7 else str(_const(r))
    op = r.choice(["+", "-", "*", "&", "|", "^", "<<", ">>"])
    left = _expr(r, names, depth + 1)
    if op in ("<<", ">>"):
        right = str(r.randint(1, 7))
    else:
        # `x - x`, `x & x`, `x ^ x` fold away. A source term with no counterpart in the assembly
        # teaches a model to hallucinate code, so operands of one operator are kept distinct.
        right = left
        for _ in range(8):
            if right != left:
                break
            right = _expr(r, names, depth + 1)
        if right == left:
            right = str(_const(r))
    return f"({left} {op} {right})"


def _params(r: random.Random, lo: int = 1, hi: int = 4) -> list[str]:
    return [f"arg{i}" for i in range(r.randint(lo, hi))]


def _signature(name: str, params: list[str]) -> str:
    return f"s32 {name}({', '.join('s32 ' + p for p in params) or 'void'})"


def gen_switch_dense(r: random.Random, name: str) -> str:
    params = _params(r, 1, 3)
    base = r.randint(0, 3)
    count = r.randint(6, 12)
    body = [f"    switch ({params[0]}) {{"]
    for value in range(base, base + count):
        body.append(f"    case {value}:")
        body.append(f"        return {_expr(r, params)};")
    if r.random() < 0.5:
        body.append("    default:")
        body.append(f"        return {_const(r)};")
    body.append("    }")
    body.append("    return -1;")
    return "\n".join([_signature(name, params) + " {", *body, "}"])


def gen_switch_sparse(r: random.Random, name: str) -> str:
    params = _params(r, 1, 3)
    count = r.randint(3, 6)
    values = sorted(r.sample(range(0, 4096, 97), count))
    body = [f"    switch ({params[0]}) {{"]
    for value in values:
        body.append(f"    case {value}:")
        body.append(f"        return {_expr(r, params)};")
    body.append("    }")
    body.append("    return 0;")
    return "\n".join([_signature(name, params) + " {", *body, "}"])


def gen_loop_for(r: random.Random, name: str) -> str:
    params = ["arg0", "arg1"]
    step = r.choice(["i++", "i += 2", "i--"])
    start, cond = ("0", "i < arg1") if step != "i--" else ("arg1", "i > 0")
    acc_op = r.choice(["+=", "^=", "|=", "-="])
    lines = [_signature(name, params) + " {", "    s32 i;", "    s32 acc;", "", "    acc = 0;",
             f"    for (i = {start}; {cond}; {step}) {{"]
    if r.random() < 0.5:
        lines.append(f"        if (((u8 *)arg0)[i] == {r.randint(0, 255)}) {{")
        lines.append("            break;")
        lines.append("        }")
    lines.append(f"        acc {acc_op} ((u8 *)arg0)[i] * {r.randint(1, 9)};")
    lines += ["    }", "    return acc;", "}"]
    return "\n".join(lines)


def gen_loop_while(r: random.Random, name: str) -> str:
    sentinel = r.choice(["0", str(r.randint(1, 255))])
    lines = [_signature(name, ["arg0"]) + " {", "    u8 *p;", "    s32 n;", "",
             "    p = (u8 *)arg0;", "    n = 0;",
             f"    while (*p != {sentinel}) {{",
             f"        n += *p & {r.randint(1, 255)};", "        p++;", "    }",
             "    return n;", "}"]
    return "\n".join(lines)


def gen_if_chain(r: random.Random, name: str) -> str:
    params = _params(r, 2, 4)
    lines = [_signature(name, params) + " {"]
    for _ in range(r.randint(2, 5)):
        a, b = r.sample(params, 2)
        op = r.choice(["<", ">", "==", "!=", "<=", ">="])
        lines.append(f"    if ({a} {op} {b}) {{")
        if r.random() < 0.5:
            lines.append(f"        return {_expr(r, params)};")
        else:
            value = _expr(r, params)
            if value == a:                   # `arg0 = arg0;` is a no-op IDO deletes with its branch
                value = f"({a} + {_const(r)})"
            lines.append(f"        {a} = {value};")
        lines.append("    }")
    # Read every parameter on the way out: an assignment nothing reads is dead, IDO deletes it
    # with its branch, and 15 of the first 150 samples lost a branch exactly that way.
    lines.append(f"    return {' + '.join(params)};")
    lines.append("}")
    return "\n".join(lines)


def _live_across_calls(r: random.Random, name: str, calls: int) -> str:
    """Two live values carried across `calls` sequential calls, each call reading both."""
    params = _params(r, 2, 3)
    lines = [f"extern s32 {NAME_PREFIX}ext(s32);", "", _signature(name, params) + " {",
             "    s32 var0;", "    s32 var1;", "",
             f"    var0 = {params[0]} * {r.randint(2, 9)};",
             f"    var1 = {params[1]} + {_const(r)};"]
    for _ in range(calls):
        target = r.choice(["var0", "var1"])
        lines.append(f"    {target} += {NAME_PREFIX}ext(var0 {r.choice(['+', '^', '-'])} var1);")
    lines += ["    return var0 + var1;", "}"]
    return "\n".join(lines)


# PROBED, not assumed (2026-09-16, IDO -O2 -mips1): two values live across 1 or 2 calls are
# spilled to stack slots and reloaded; at 3 calls one s-register is saved, at 4 two. The
# register choice is cost-based on calls crossed. The first version of this generator used 1-3
# calls and assumed s-registers; its fire test measured 0 of 5.
def gen_saved_regs(r: random.Random, name: str) -> str:
    return _live_across_calls(r, name, r.randint(3, 6))


def gen_stack_spill(r: random.Random, name: str) -> str:
    return _live_across_calls(r, name, 1)


WIDTHS = [("s8", 1), ("u8", 1), ("s16", 2), ("u16", 2), ("s32", 4), ("u32", 4)]


def gen_struct_offsets(r: random.Random, name: str) -> str:
    """Known field widths and offsets: the generated source IS the layout ground truth."""
    fields = [(f"field{i}", *r.choice(WIDTHS)) for i in range(r.randint(3, 8))]
    tag = f"{NAME_PREFIX}Struct_{name[len(NAME_PREFIX):]}"
    lines = ["typedef struct {"]
    lines += [f"    {t} {f};" for f, t, _ in fields]
    lines += [f"}} {tag};", "", f"s32 {name}({tag} *arg0, s32 arg1) {{"]
    for f, _t, _w in r.sample(fields, min(len(fields), r.randint(2, 4))):
        if r.random() < 0.5:
            lines.append(f"    arg0->{f} = {_expr(r, ['arg1'])};")
        else:
            lines.append(f"    arg1 += arg0->{f};")
    lines += ["    return arg1;", "}"]
    return "\n".join(lines)


def _decimal(r: random.Random, single: bool) -> str:
    # Not an exact binary fraction: IDO loads 1.25 through `mtc1` with a zero low word, while
    # game code loads its constants from .rodata with `ldc1`/`lwc1` (probed 2026-09-16).
    whole, frac = r.randint(0, 99), r.choice([3, 7, 13, 17, 33, 37, 61, 99])
    return f"{whole}.{frac:02d}" + ("f" if single else "")


def gen_float_math(r: random.Random, name: str) -> str:
    """The largest shaped gap in real residuals: FPU loads, double arithmetic, float compares."""
    single = r.random() < 0.5
    ftype = "f32" if single else "f64"
    params = [f"arg{i}" for i in range(r.randint(2, 3))]
    lines = [f"{ftype} {name}({', '.join(f'{ftype} {p}' for p in params)}) {{", f"    {ftype} var0;", ""]
    lines.append(f"    var0 = ({params[0]} * {_decimal(r, single)}) + {params[1]};")
    for _ in range(r.randint(1, 3)):
        op = r.choice(["<", ">", "<=", ">="])
        lines.append(f"    if (var0 {op} {_decimal(r, single)}) {{")
        lines.append(f"        var0 = var0 {r.choice(['-', '+', '*'])} {r.choice(params)};")
        lines.append("    }")
    lines += ["    return var0;", "}"]
    return "\n".join(lines)


def gen_narrow_locals(r: random.Random, name: str) -> str:
    """Second-largest gap: u8/s16 locals read back from stack slots (`lbu R,#(sp)`)."""
    narrow = [r.choice(["u8", "s8", "u16", "s16"]) for _ in range(r.randint(2, 3))]
    lines = [f"extern s32 {NAME_PREFIX}ext(s32);", "", _signature(name, ["arg0", "arg1"]) + " {"]
    lines += [f"    {t} var{i};" for i, t in enumerate(narrow)] + [""]
    for i in range(len(narrow)):
        lines.append(f"    var{i} = {r.choice(['arg0', 'arg1'])}{r.choice(['', ' >> 2', ' + 3'])};")
    for i in range(len(narrow)):
        lines.append(f"    {NAME_PREFIX}ext(var{i});")
    lines += [f"    return {' + '.join(f'var{i}' for i in range(len(narrow)))};", "}"]
    return "\n".join(lines)


def gen_color_pack(r: random.Random, name: str) -> str:
    """RGBA5551 packing (gbi.h GPACK_RGBA5551) from 16-bit values, some passed on the stack.

    Written from residual_gate's gap report, the way the loop's model is meant to: the top shaped
    gap in real game residuals was `sll; andi; sll; andi; or` over `lh R,#(sp)` loads with
    0x7c0/0x3e masks and an alpha `ori`, in drawMenuSolidRect and drawMenuPanelBackdrop.
    """
    stack_count = r.randint(1, 3)
    params = ["arg0", "arg1", "arg2", "arg3"] + [f"arg{4 + i}" for i in range(stack_count)]
    narrow = params[4:] + r.sample(params[:4], r.randint(0, 2))
    decls = ", ".join(("s16 " if p in narrow else "s32 ") + p for p in params)
    red, green, blue, alpha = (r.sample(params[1:], 3) + [r.choice(params)])
    packed = (f"(({red} << 8) & 0xF800) | (({green} << 3) & 0x7C0) | "
              f"(({blue} >> 2) & 0x3E) | ({alpha} & 1)")
    lines = [f"extern void {NAME_PREFIX}sink(s32, s32);", "", f"void {name}({decls}) {{",
             f"    {NAME_PREFIX}sink({params[0]}, {packed});", "}"]
    return "\n".join(lines)


@dataclass(frozen=True)
class Family:
    name: str
    capability: str                       # the solver/signals.py fault class it exercises
    generate: Callable[[random.Random, str], str]
    expect: Callable[[dict], bool]        # a probe, measured per row -- never assumed
    expectation: str


FAMILIES: dict[str, Family] = {f.name: f for f in (
    Family("switch_dense", "structural", gen_switch_dense,
           lambda feat: feat["jump_table"], "IDO emits a .rodata jump table"),
    Family("switch_sparse", "structural", gen_switch_sparse,
           lambda feat: not feat["jump_table"] and feat["branches"] >= 2,
           "IDO emits a compare chain, no jump table"),
    Family("loop_for", "structural", gen_loop_for,
           lambda feat: feat["backward_branches"] >= 1, "a backward branch exists"),
    Family("loop_while", "structural", gen_loop_while,
           lambda feat: feat["backward_branches"] >= 1, "a backward branch exists"),
    Family("if_chain", "structural", gen_if_chain,
           lambda feat: feat["branches"] >= 2, "at least two conditional branches"),
    Family("saved_regs", "regalloc", gen_saved_regs,
           lambda feat: len(set(feat["saved_registers"]) - {"ra"}) >= 1,
           "live across >=3 calls: at least one callee-saved s-register is used"),
    Family("stack_spill", "regalloc", gen_stack_spill,
           lambda feat: not (set(feat["saved_registers"]) - {"ra"}) and feat["stack_spills"] >= 1,
           "live across 1 call: spilled to a stack slot, no s-register"),
    Family("struct_offsets", "offset", gen_struct_offsets,
           lambda feat: len(feat["memory_offsets"]) >= 1, "field accesses at nonzero-width offsets"),
    # The two families below were not chosen: residual_gate's coverage report named them as the
    # most common shaped faults in real residuals that the first eight families never produced.
    Family("float_math", "structural", gen_float_math,
           lambda feat: feat["fpu_ops"] >= 2 and feat["fpu_constant_loads"] >= 1,
           "FPU arithmetic with a .rodata float constant load"),
    Family("narrow_locals", "width", gen_narrow_locals,
           lambda feat: feat["narrow_stack_loads"] >= 1, "a byte/short load from a stack slot"),
    Family("color_pack", "structural", gen_color_pack,
           lambda feat: feat["or_ops"] >= 2 and feat["andi_ops"] >= 2 and feat["narrow_stack_loads"] >= 1,
           "masked shift-or packing over a 16-bit stack load"),
)}


def generate(family: str, seed: int) -> tuple[str, str]:
    """(function name, full translation unit) -- deterministic in (family, seed)."""
    name = f"{NAME_PREFIX}{family}_{seed}"
    body = FAMILIES[family].generate(_rng(family, seed), name)
    return name, f'#include "common.h"\n\n{body}\n'


# --- compile and observe -----------------------------------------------------

def recipe(repo: Path, target: str = DEFAULT_TARGET) -> dict:
    from solver import compiler_recipe
    return compiler_recipe.resolve(repo, target)


INSN = re.compile(r"^\s*([0-9a-f]+):\s+([a-z][a-z0-9.]*)\s*(.*)$")
RELOC = re.compile(r"^\s*[0-9a-f]+:\s+(R_MIPS_\w+)\s+(\S+)")
MEM = re.compile(r"(-?(?:0x[0-9a-f]+|\d+))\((\w+)\)")


def function_listing(objdump_text: str, name: str) -> list[str]:
    """The lines belonging to `name` in an `objdump -dr` listing."""
    out, inside = [], False
    for line in objdump_text.splitlines():
        header = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line)
        if header:
            inside = header.group(1) == name
            continue
        if inside and line.strip():
            out.append(line.rstrip())
    return out


def features(listing: list[str]) -> dict:
    """Mechanical observations of one function's code. No interpretation beyond counting."""
    insns, relocs = [], []
    for line in listing:
        m = INSN.match(line)
        if m:
            insns.append((int(m.group(1), 16), m.group(2), m.group(3)))
            continue
        rm = RELOC.match(line)
        if rm:
            relocs.append((rm.group(1), rm.group(2)))
    branches = backward = spills = 0
    saved: list[str] = []
    offsets: set[tuple[str, int]] = set()
    indirect_jump = False
    for addr, op, operands in insns:
        if op in BRANCH_OPS:
            branches += 1
            target = re.search(r"\b([0-9a-f]+) <", operands)
            if target and int(target.group(1), 16) <= addr:
                backward += 1
        if op == "jr" and operands.strip() != "ra":
            indirect_jump = True
        if op == "sw":
            reg = operands.split(",")[0].strip()
            if reg in SAVED and "(sp)" in operands:
                saved.append(reg)
            elif "(sp)" in operands:
                spills += 1             # argument home slot or temporary spilled across a call
        for off, base in MEM.findall(operands):
            if base not in ("sp", "at", "gp"):
                offsets.add((base, int(off, 0)))
    fpu = [(op, operands) for _, op, operands in insns
           if re.search(r"\.(s|d|w)$", op) or op in ("ldc1", "lwc1", "sdc1", "swc1", "mtc1", "mfc1")
           or op.startswith("bc1")]
    return {
        "or_ops": sum(1 for _, op, _ in insns if op in ("or", "ori")),
        "andi_ops": sum(1 for _, op, _ in insns if op == "andi"),
        "fpu_ops": len(fpu),
        "fpu_constant_loads": sum(1 for op, _ in fpu if op in ("ldc1", "lwc1")),
        "narrow_stack_loads": sum(1 for _, op, operands in insns
                                 if op in ("lb", "lbu", "lh", "lhu") and "(sp)" in operands),
        "instructions": len(insns),
        "branches": branches,
        "backward_branches": backward,
        "calls": sum(1 for _, op, _ in insns if op == "jal"),
        "saved_registers": sorted(set(saved)),
        "stack_spills": spills,
        "jump_table": indirect_jump and any(sym == ".rodata" for _, sym in relocs),
        "memory_offsets": sorted({off for _, off in offsets}),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def compile_one(repo: Path, resolved: dict, family: str, seed: int, workdir: Path) -> dict:
    name, source = generate(family, seed)
    unit = workdir / f"{name}.c"
    obj = workdir / f"{name}.o"
    unit.write_text(source)
    started = time.monotonic()
    proc = subprocess.run([*resolved["command"], "-o", str(obj), str(unit)],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    row = {
        "schema_version": SCHEMA_VERSION, "family": family, "seed": seed, "name": name,
        "capability": FAMILIES[family].capability, "source": source, "source_sha256": _sha(source),
        "compiled": proc.returncode == 0 and obj.exists(),
        "stderr": (proc.stderr or "")[-2000:], "compile_ms": int((time.monotonic() - started) * 1000),
    }
    if row["compiled"]:
        dump = subprocess.run([OBJDUMP, "-dr", "--no-show-raw-insn", str(obj)],
                              capture_output=True, text=True, check=True).stdout
        listing = function_listing(dump, name)
        row["asm"] = "\n".join(listing)
        row["asm_sha256"] = _sha(row["asm"])
        row["features"] = features(listing)
        row["expected"] = bool(FAMILIES[family].expect(row["features"]))
    return row


def run(repo: Path, out: Path, per_family: int, families: list[str], jobs: int,
        seed_base: int = 0, target: str = DEFAULT_TARGET) -> dict:
    resolved = recipe(repo, target)
    provenance = {k: resolved.get(k) for k in ("target", "makefile_sha256", "projection_sha256")}
    provenance["command_sha256"] = _sha(json.dumps(resolved["command"]))
    work = Path(tempfile.mkdtemp(prefix="synthetic-corpus-"))
    tasks = [(f, seed_base + i) for f in families for i in range(per_family)]
    try:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(lambda t: compile_one(repo, resolved, t[0], t[1], work), tasks))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as handle:
        for row in rows:
            handle.write(json.dumps({**row, "recipe": provenance}) + "\n")
    return summarize(rows, provenance)


def summarize(rows: list[dict], provenance: dict) -> dict:
    summary: dict = {"recipe": provenance, "rows": len(rows), "families": {}}
    for family in sorted({r["family"] for r in rows}):
        mine = [r for r in rows if r["family"] == family]
        compiled = [r for r in mine if r["compiled"]]
        fired = [r for r in compiled if r["expected"]]
        distinct_asm = len({r["asm_sha256"] for r in compiled})
        summary["families"][family] = {
            "capability": FAMILIES[family].capability,
            "expectation": FAMILIES[family].expectation,
            "generated": len(mine), "compiled": len(compiled),
            "expectation_fired": len(fired),
            "fire_rate": round(len(fired) / len(compiled), 3) if compiled else None,
            "distinct_assembly": distinct_asm,
            "median_instructions": (sorted(r["features"]["instructions"] for r in compiled)
                                    [len(compiled) // 2] if compiled else None),
            "first_failure_stderr": next((r["stderr"][:300] for r in mine if not r["compiled"]), None),
            "non_firing_seeds": [r["seed"] for r in compiled if not r["expected"]][:10],
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, required=True, help="JSONL of pairs; receipt written beside it")
    ap.add_argument("--per-family", type=int, default=50)
    ap.add_argument("--families", nargs="*", default=sorted(FAMILIES))
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    summary = run(args.repo, args.out, args.per_family, args.families, args.jobs, args.seed_base, args.target)
    receipt = args.out.with_suffix(".receipt.json")
    receipt.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
