"""H10: `p->m` vs `*(T *)((u8 *)p + OFF)`, 30 paired compiles through the game recipe."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402
from solver import byte_certificate  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
TYPES = {"s32": 0x24, "s16": 0x1A, "u16": 0x2E, "s8": 0x13, "u8": 0x31}


def struct(t):
    # pad to OFF, then the member; typedefs only (no project headers)
    return (f"typedef signed int s32; typedef signed short s16; typedef unsigned short u16;\n"
            f"typedef signed char s8; typedef unsigned char u8;\n"
            f"typedef struct {{ u8 pad[{TYPES[t]}]; {t} m; u8 tail[8]; }} Rec;\n"
            f"extern Rec syn_recs[];\nextern int syn_sink;\n")


def bodies(t, access, base):
    off = f"0x{TYPES[t]:X}"
    ptr = {"param": "p", "local": "q", "array": "syn_recs[n]"}[base]
    member = {"param": "p->m", "local": "q->m", "array": "syn_recs[n].m"}[base]
    explicit_base = {"param": "p", "local": "q", "array": "&syn_recs[n]"}[base]
    explicit = f"*({t} *)((u8 *){explicit_base} + {off})"
    pre = "    Rec *q = &syn_recs[n];\n" if base == "local" else ""
    stmt = (lambda e: f"    syn_sink = {e};\n") if access == "load" else (lambda e: f"    {e} = (int)n;\n")
    head = "void syn_f(Rec *p, int n) {\n"
    return (head + pre + stmt(member) + "}\n", head + pre + stmt(explicit) + "}\n")


def compile_image(tree, cmd, source):
    probe = tree / "build" / "syn_probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "h.c").write_text(source)
    r = subprocess.run([str(Path.home() / "decomp/tools-src/ido-trace/cc"), *ai.cc_flags(cmd), "-o",
                        str(probe / "h.o"), str(probe / "h.c")], cwd=tree, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    image = byte_certificate.object_image((probe / "h.o").read_bytes())
    return hashlib.sha256(json.dumps(image, sort_keys=True, default=str).encode()).hexdigest()


def main():
    cmd = ai.recipe(TREE)
    rows = []
    for t in TYPES:
        for access in ("load", "store"):
            for base in ("param", "local", "array"):
                a, b = bodies(t, access, base)
                row = {"type": t, "access": access, "base": base}
                try:
                    row["identical"] = compile_image(TREE, cmd, struct(t) + a) == compile_image(TREE, cmd, struct(t) + b)
                except RuntimeError as exc:
                    row["identical"], row["error"] = None, str(exc)
                rows.append(row)
    held = [r for r in rows if r["identical"] is True]
    failed = [r for r in rows if r["identical"] is not True]
    verdict = "confirmed" if len(held) == len(rows) else "untestable" if not held else "partial"
    out = {"pairs": len(rows), "identical": len(held), "verdict": verdict, "not_identical": failed}
    (HERE / "h10.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
