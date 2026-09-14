"""Distinguish literal rodata from a named const object; check ISA scope."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import compiler_recipe

repo = Path.home() / "decomp/sbk1"
out = Path(__file__).parent / "supplemental-v1"
out.mkdir(exist_ok=False)
recipe = compiler_recipe.resolve(repo, "build/src/menu/main_menu/main_menu_scene_model.o")
flags = shlex.split(recipe["settings"]["CFLAGS"]) + shlex.split(recipe["settings"]["C_OPT"])
probes = [
    ("extern-const", "extern const float k; extern void sink(float); void probe(int n) { while(n-->0) sink(k); }\n", flags),
    ("literal-rodata", "extern void sink(float); void probe(int n) { while(n-->0) sink(1.25f); }\n", flags),
    ("conditional-store-mips1", "void probe(int c,int *p) { if(c) *p=1; }\n", flags),
    ("conditional-store-mips2", "void probe(int c,int *p) { if(c) *p=1; }\n", ["-mips2" if f == "-mips1" else f for f in flags]),
]
report = {"scope": "synthetic mechanism probes; mips2 is a diagnostic arm, not a changed game recipe",
          "model_calls": 0, "recipe": recipe, "rows": []}
with tempfile.TemporaryDirectory(prefix="decompedia-supplemental-") as temp:
    temp = Path(temp)
    for label, source, cflags in probes:
        src, obj = temp / "probe.c", temp / "probe.o"
        src.write_text(source)
        (out / f"{label}.c").write_text(source)
        p = subprocess.run([str(repo / "tools/ido-recomp/linux/cc"), *cflags, str(src), "-o", str(obj)],
            cwd=repo, text=True, capture_output=True, timeout=60)
        row = {"label": label, "flags": cflags, "returncode": p.returncode, "diagnostics": p.stdout+p.stderr,
               "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        if p.returncode == 0:
            asm = subprocess.run(["mips-linux-gnu-objdump", "-dr", "-M", "regnames=32", str(obj)],
                text=True, capture_output=True, check=True).stdout
            (out / f"{label}.s").write_text(asm)
            (out / f"{label}.o").write_bytes(obj.read_bytes())
            print(label, asm, flush=True)
        report["rows"].append(row)
(out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
