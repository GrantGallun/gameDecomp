"""H15c: scalar natural alignment vs aggregate 4-alignment, on fresh constructs (PROTOCOL-layout.md)."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402

HERE = Path(__file__).resolve().parent
HDR = layout.HDR + "typedef struct { short a, b, c; } Sh3;\n"
CALLS = "    x = syn_g(n);\n    syn_g(x);\n"
# id -> (declarations, addressed expressions in call order, frame, addiu offsets)
CASES = {
    "Q1": ("int x; char c;", ["&c"], 32, [27]),
    "Q2": ("int x; short a; short b;", ["&a", "&b"], 32, [26, 24]),
    "Q3": ("int x; char c; int y;", ["&c", "&y"], 40, [35, 28]),
    "Q4": ("int x; short arr[3];", ["arr"], 40, [28]),
    "Q5": ("int x; Sh3 t;", ["&t"], 40, [28]),
    "Q6": ("int x; char c; double d;", ["&c", "&d"], 40, [35, 24]),
}


def main():
    rows, ok = {}, []
    for cid, (decls, addressed, frame, offs) in CASES.items():
        body = "".join(f"    {d.strip()};\n" for d in decls.split(";") if d.strip()) + CALLS + \
            "".join(f"    syn_p({a});\n" for a in addressed)
        insns = layout.compile_(HDR + "void syn_f(int n) {\n" + body + "}\n")
        m = next((re.search(r"addiu sp,sp,-(\d+)", i) for i in insns if re.search(r"addiu sp,sp,-\d+", i)), None)
        got = int(m.group(1)) if m else 0
        addrs = [int(mm.group(1)) for i in insns if (mm := re.match(r"addiu a\d,sp,(\d+)", i))]
        checks = {"frame": got == frame, "slots": addrs == offs}
        rows[cid] = {"frame": got, "predicted_frame": frame, "slots": addrs, "predicted_slots": offs, "checks": checks,
                     "sp_insns": [i for i in insns if "sp" in i]}
        ok.extend(checks.values())
        print(cid, json.dumps(checks), "|", " | ".join(rows[cid]["sp_insns"]))
    verdict = ("confirmed" if all(ok) else "not confirmed") + f" ({sum(ok)}/{len(ok)} checks)"
    print(verdict)
    (HERE / "layout2.json").write_text(json.dumps({"verdict": verdict, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
