"""H13: `u8`/`u16` locals produce the surplus `andi`; `s32` does not. Paired compiles, game recipe."""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
HDR = ("typedef signed int s32; typedef unsigned int u32; typedef unsigned short u16; typedef signed short s16;\n"
       "typedef unsigned char u8; typedef signed char s8;\nextern int syn_a[]; extern int syn_g(int);\n"
       "extern struct { int wide; } syn_s;\n")
SHAPES = {
    "sum": "void syn_f(int a, int b) {\n    T x;\n    x = a + b;\n    syn_a[x] = a;\n}\n",
    "call": "void syn_f(int a) {\n    T x;\n    x = syn_g(a);\n    syn_a[x] = a;\n}\n",
    "wide_load": "void syn_f(int a) {\n    T x;\n    x = syn_s.wide;\n    syn_a[x] = a;\n}\n",
    "loop": "void syn_f(int n) {\n    T x;\n    for (x = 0; x < n; x++) {\n        syn_a[x] = n;\n    }\n}\n",
}


def insns(src):
    probe = TREE / "build" / "syn_probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "m.c").write_text(src)
    r = subprocess.run([str(Path.home() / "decomp/tools-src/ido-trace/cc"), *ai.cc_flags(ai.recipe(TREE)), "-o",
                        str(probe / "m.o"), str(probe / "m.c")], cwd=TREE, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-200:])
    d = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(probe / "m.o")],
                       capture_output=True, text=True).stdout
    return [l.split(":", 1)[1].strip().split()[0] for l in d.splitlines() if re.match(r"^\s+[0-9a-f]+:", l)]


rows = []
for shape, body in SHAPES.items():
    counts = {}
    for t in ("s32", "u8", "u16", "s8", "s16"):
        ops = insns(HDR + body.replace("T x;", f"{t} x;"))
        counts[t] = {"n": len(ops), "andi": ops.count("andi"), "sra": ops.count("sra")}
    ok = all(counts[t]["andi"] == counts["s32"]["andi"] + 1 and counts[t]["n"] == counts["s32"]["n"] + 1
             for t in ("u8", "u16"))
    rows.append({"shape": shape, "counts": counts, "unsigned_narrow_adds_exactly_one_andi": ok})
    print(shape, json.dumps(counts), ok)
verdict = "confirmed" if all(r["unsigned_narrow_adds_exactly_one_andi"] for r in rows) else "partial"
(HERE / "h13.json").write_text(json.dumps({"rows": rows, "verdict": verdict}, indent=1))
print("H13:", verdict)
