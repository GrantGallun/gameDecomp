"""Which IDO stage makes two spellings identical? Probe with line-normalized ucode.

ucode (IDO's front-end output, `cc -j`) embeds line numbers, so raw comparison calls every edit
different. Compiling each variant laid out IDENTICALLY -- comments stripped, the whole file on one
line, one filename -- removes line information from the comparison: equal ucode then means the
front end produced the same program. Pre-as1 assembly (`-S`) is compared for the optimizer/codegen
stage, and the object for the final one.

    python3 ido_stage_probe.py        (WSL)
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CC = Path("/home/grant/decomp/sbk1/tools/ido-recomp/linux/cc")
FLAGS = ["-O2", "-mips2", "-G", "0"]


def one_line(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return " ".join(l.strip() for l in src.splitlines() if l.strip()) + "\n"


def stages(src: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "cand.c").write_text(one_line(src))
        out = {}
        r = subprocess.run([str(CC), "-c", *FLAGS, "-j", "cand.c"], cwd=d, capture_output=True, text=True)
        out["ucode"] = (d / "cand.u").read_bytes() if (d / "cand.u").exists() else None
        r = subprocess.run([str(CC), *FLAGS, "-S", "cand.c"], cwd=d, capture_output=True, text=True)
        s = (d / "cand.s").read_text() if (d / "cand.s").exists() else None
        out["pre_as1"] = "\n".join(l for l in s.splitlines()
                                   if not re.match(r"\s*(#|\.loc|\.file|\.ident)", l)) if s else None
        r = subprocess.run([str(CC), "-c", *FLAGS, "cand.c"], cwd=d, capture_output=True, text=True)
        if (d / "cand.o").exists():
            dump = subprocess.run(["mips-linux-gnu-objdump", "-d", "cand.o"], cwd=d,
                                  capture_output=True, text=True).stdout
            out["object"] = "\n".join(l.split("\t", 2)[-1] for l in dump.splitlines() if "\t" in l)
        else:
            out["object"] = None
            out["error"] = r.stderr[:200]
    return out


BASE = "int a[4];\nint f(int i) {\n    return a[i];\n}\n"
VARIANTS = {
    "blank lines only": "int a[4];\n\nint f(int i) {\n\n    return a[i];\n}\n",
    "pointer arithmetic": "int a[4];\nint f(int i) {\n    return *(a + i);\n}\n",
    "redundant cast": "int a[4];\nint f(int i) {\n    return (int)a[i];\n}\n",
    "commuted index": "int a[4];\nint f(int i) {\n    return *(i + a);\n}\n",
    "temporary": "int a[4];\nint f(int i) {\n    int t = a[i];\n    return t;\n}\n",
    "different program": "int a[4];\nint f(int i) {\n    return a[i] + 1;\n}\n",
}
LOOP_BASE = ("int a[8];\nvoid g(void) {\n    int i;\n    for (i = 0; i < 8; i++) {\n"
             "        a[i] = 0;\n    }\n}\n")
LOOPS = {
    "while loop": "int a[8];\nvoid g(void) {\n    int i = 0;\n    while (i < 8) {\n        a[i] = 0;\n"
                  "        i++;\n    }\n}\n",
    "do-while loop": "int a[8];\nvoid g(void) {\n    int i = 0;\n    do {\n        a[i] = 0;\n"
                     "        i++;\n    } while (i < 8);\n}\n",
    "pointer loop": "int a[8];\nvoid g(void) {\n    int *p;\n    for (p = a; p < a + 8; p++) {\n"
                    "        *p = 0;\n    }\n}\n",
}
for base, variants in ((BASE, VARIANTS), (LOOP_BASE, LOOPS)):
    ref = stages(base)
    for name, src in variants.items():
        got = stages(src)
        flags = {k: ("same" if got.get(k) is not None and got.get(k) == ref.get(k) else "DIFFERENT")
                 for k in ("ucode", "pre_as1", "object")}
        first = next((k for k in ("ucode", "pre_as1", "object") if flags[k] == "same"), None)
        print(f"{name:20s} ucode {flags['ucode']:9s} pre-as1 {flags['pre_as1']:9s} "
              f"object {flags['object']:9s} -> {'flattened by ' + first if first else 'real difference'}"
              + (f"  [{got.get('error')}]" if got.get("error") else ""))
