"""H15: frame bytes per C construct, paired synthetic compiles."""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
HDR = ("extern int syn_g(int);\nextern void syn_p(void *);\nextern void syn_h5(int, int, int, int, int);\n"
       "typedef struct { int a, b, c; } Trip;\n")
BASE_LOCALS = "    int x;\n"
BASE_BODY = "    x = syn_g(n);\n    syn_g(x);\n"
VARIANTS = {
    "base": ("", "", 0),
    "addr_int": ("    int y;\n", "    syn_p(&y);\n", 4),
    "two_addr_ints": ("    int y;\n    int z;\n", "    syn_p(&y);\n    syn_p(&z);\n", 8),
    "char_array_8": ("    char buf[8];\n", "    syn_p(buf);\n", 8),
    "char_array_6": ("    char buf[6];\n", "    syn_p(buf);\n", 8),
    "struct_12": ("    Trip t;\n", "    syn_p(&t);\n", 12),
    "fifth_arg": ("", "    syn_h5(1, 2, 3, 4, x);\n", 4),
}


def frame(src):
    probe = TREE / "build" / "syn_probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "fr.c").write_text(src)
    r = subprocess.run([str(Path.home() / "decomp/tools-src/ido-trace/cc"), *ai.cc_flags(ai.recipe(TREE)), "-o",
                        str(probe / "fr.o"), str(probe / "fr.c")], cwd=TREE, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-200:])
    d = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(probe / "fr.o")],
                       capture_output=True, text=True).stdout
    m = re.search(r"addiu\s+sp,sp,-(\d+)", d)
    slots = sorted({int(x) for x in re.findall(r"[\s,](-?\d+)\(sp\)", d)} | {int(x) for x in re.findall(r"addiu\s+\w+,sp,(\d+)", d)})
    frame.slots = slots
    return int(m.group(1)) if m else 0


def main():
    rows = {}
    for name, (locals_, body, added) in VARIANTS.items():
        src = HDR + "void syn_f(int n) {\n" + BASE_LOCALS + locals_ + BASE_BODY + body + "}\n"
        rows[name] = {"frame": frame(src), "predicted_added": added}
        rows[name]["sp_offsets_used"] = frame.slots
    base = rows["base"]["frame"]
    # base raw = outgoing 16 + saved words; the rounded frame hides up to 4 bytes of slack, so test after rounding
    for name, r in rows.items():
        r["delta"] = r["frame"] - base
    out = {"rows": rows}
    print(json.dumps(out, indent=1))
    (HERE / "constructs.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
