"""H15b: test the frame layout rule on constructs it was not fitted on (PROTOCOL-layout.md)."""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
HDR = ("extern int syn_g(int);\nextern void syn_p(void *);\n"
       "extern void syn_h6(int, int, int, int, int, int);\n")
CALLS = "    x = syn_g(n);\n    syn_g(x);\n"
# id -> (body, predicted frame, {register-or-name: predicted offset}); names are checked via `addiu aK,sp,OFF`
CASES = {
    "P1_decl_swap": ("    int y;\n    int x;\n" + CALLS + "    syn_p(&y);\n", 32, {"ra": 20, "addr": [28]}),
    "P2_unused_between": ("    int x;\n    int u;\n    int y;\n" + CALLS + "    syn_p(&y);\n", 40, {"ra": 20, "addr": [28]}),
    "P3_short_addr": ("    int x;\n    short s;\n" + CALLS + "    syn_p(&s);\n", 32, {"ra": 20, "addr": [24]}),
    "P4_double_addr": ("    int x;\n    double d;\n" + CALLS + "    syn_p(&d);\n", 40, {"ra": 20, "addr": [24]}),
    "P5_sixth_arg": ("    int x;\n" + CALLS + "    syn_h6(1, 2, 3, 4, x, x);\n", 40, {"ra": 28, "stores": [16, 20, 36]}),
    "P6_callee_saved": ("    int i;\n    for (i = 0; i < n; i++) {\n        syn_g(i);\n    }\n", 32,
                        {"ra": 28, "s1": 24, "s0": 20}),
    "P7_two_arrays": ("    int x;\n    char a[5];\n    char b[3];\n" + CALLS + "    syn_p(a);\n    syn_p(b);\n", 40,
                      {"ra": 20, "addr": [28, 24]}),
}


def compile_(src):
    probe = TREE / "build" / "syn_probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "fl.c").write_text(src)
    r = subprocess.run([str(Path.home() / "decomp/tools-src/ido-trace/cc"), *ai.cc_flags(ai.recipe(TREE)), "-o",
                        str(probe / "fl.o"), str(probe / "fl.c")], cwd=TREE, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    d = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(probe / "fl.o")],
                       capture_output=True, text=True).stdout
    return [l.split(":", 1)[1].strip().replace("\t", " ") for l in d.splitlines() if re.match(r"^\s+[0-9a-f]+:", l)]


def main():
    rows, verdicts = {}, []
    for cid, (body, frame, slots) in CASES.items():
        insns = compile_(HDR + "void syn_f(int n) {\n" + body + "}\n")
        m = next((re.search(r"addiu sp,sp,-(\d+)", i) for i in insns if re.search(r"addiu sp,sp,-\d+", i)), None)
        got = int(m.group(1)) if m else 0
        saves = {mm.group(1): int(mm.group(2)) for i in insns if (mm := re.match(r"sw (ra|s\d),(\d+)\(sp\)", i))}
        addrs = [int(mm.group(1)) for i in insns if (mm := re.match(r"addiu a\d,sp,(\d+)", i))]
        stores = sorted({int(mm.group(1)) for i in insns if (mm := re.match(r"sw [atv]\d,(\d+)\(sp\)", i))})
        checks = {"frame": got == frame}
        for reg in ("ra", "s0", "s1"):
            if reg in slots:
                checks[reg] = "untestable" if reg not in saves else saves[reg] == slots[reg]
        if "addr" in slots:
            checks["addr"] = addrs == slots["addr"]
        if "stores" in slots:
            checks["stores"] = stores == slots["stores"]
        if cid == "P6_callee_saved" and not {"s0", "s1"} <= set(saves):
            checks = {"frame": "untestable (no callee-saved registers used)", **checks}
        rows[cid] = {"frame": got, "predicted_frame": frame, "saves": saves, "addr_args": addrs, "stack_stores": stores,
                     "predicted": slots, "checks": checks,
                     "sp_insns": [i for i in insns if "sp" in i]}
        verdicts.extend(v for v in checks.values())
        print(cid, json.dumps(rows[cid]["checks"]), "|", " | ".join(rows[cid]["sp_insns"]))
    testable = [v for v in verdicts if isinstance(v, bool)]
    verdict = ("confirmed" if testable and all(testable) else "not confirmed") + \
              f" ({sum(testable)}/{len(testable)} testable checks; {len(verdicts) - len(testable)} untestable)"
    print(verdict)
    (HERE / "layout.json").write_text(json.dumps({"verdict": verdict, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
