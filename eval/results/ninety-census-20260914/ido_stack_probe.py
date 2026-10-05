"""Probe how IDO -O2 (the project recipe) assigns stack slots to address-taken locals. No campaign access.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/ido_stack_probe.py

Compiles small C files with the resolved project compiler command for a game TU and prints,
per variant, the frame size and the sp offset each local's address is passed at.
"""
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import compiler_recipe  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite"
with sqlite3.connect(f"file:{KB}?mode=ro", uri=True) as conn:
    target = conn.execute("SELECT t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name='makeFixedRotationXZ'").fetchone()[0]
recipe = compiler_recipe.resolve(REPO, target)
PRELUDE = "typedef short s16; typedef int s32; typedef unsigned char u8; typedef float f32;\n" \
          "void use(void *, ...); void use2(void *, void *);\n"

VARIANTS = {
    "a_then_b_s16x9": "void f(void){ s16 a[9]; s16 b[9]; use(&a); use(&b); use2(&a,&b); }",
    "b_then_a_s16x9": "void f(void){ s16 b[9]; s16 a[9]; use(&a); use(&b); use2(&a,&b); }",
    "use_order_swapped": "void f(void){ s16 a[9]; s16 b[9]; use(&b); use(&a); use2(&a,&b); }",
    "three_mixed": "void f(void){ s32 x; s16 y[3]; u8 z[5]; use(&x); use(&y); use(&z); }",
    "three_mixed_rev": "void f(void){ u8 z[5]; s16 y[3]; s32 x; use(&x); use(&y); use(&z); }",
    "scalar_s16": "void f(void){ s16 a; s16 b; use(&a); use(&b); }",
    "char_and_int": "void f(void){ u8 c; s32 i; use(&c); use(&i); }",
    "int_and_char": "void f(void){ s32 i; u8 c; use(&c); use(&i); }",
    "unused_pad_decl": "void f(void){ s32 pad; s16 a[9]; use(&a); }",
    "unused_pad_after": "void f(void){ s16 a[9]; s32 pad; use(&a); }",
    "struct_arg_slots": "void f(s32 p){ s16 a[9]; use(&a); use(&p); }",
    "unused_char_array": "void f(void){ char pad[3]; s16 a[9]; use(&a); }",
    "unused_volatile_array": "void f(void){ volatile unsigned char pad[8]; s16 a[9]; use(&a); }",
    "unused_after_array": "void f(void){ s16 a[9]; char pad[2]; use(&a); }",
    "register_saved_no_pad": "unsigned int g(void); void h(unsigned int); void f(void){ register unsigned int m; m = g(); use(0); h(m); }",
    "register_saved_pad4": "unsigned int g(void); void h(unsigned int); void f(void){ char pad[4]; register unsigned int m; m = g(); use(0); h(m); }",
    "register_saved_pad8": "unsigned int g(void); void h(unsigned int); void f(void){ char pad[8]; register unsigned int m; m = g(); use(0); h(m); }",
    "register_saved_s32pad": "unsigned int g(void); void h(unsigned int); void f(void){ s32 pad; register unsigned int m; m = g(); use(0); h(m); }",
    "register_local_mix":"void f(s32 p){ s32 t; s16 a[9]; t = p * 3; use(&a, t); use(&a, t); }",
}

with tempfile.TemporaryDirectory(prefix="ido-stack-") as tmp:
    for name, body in VARIANTS.items():
        c = Path(tmp) / f"{name}.c"
        o = Path(tmp) / f"{name}.o"
        c.write_text(PRELUDE + body + "\n")
        run = subprocess.run(recipe["command"] + ["-o", str(o), str(c)], cwd=REPO, capture_output=True, text=True)
        if run.returncode:
            print(name, "COMPILE FAILED", run.stderr[-300:])
            continue
        dump = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(o)], capture_output=True, text=True).stdout
        frame = re.search(r"addiu\s+sp,sp,-(\d+)", dump)
        addrs = re.findall(r"addiu\s+(a\d),sp,(\d+)", dump)
        slots = re.findall(r"sw\s+(\w+),(\d+)\(sp\)", dump)
        print(f"{name:20} frame={frame.group(1) if frame else '?':4} address-args={addrs} stores={slots}  | {body}")
