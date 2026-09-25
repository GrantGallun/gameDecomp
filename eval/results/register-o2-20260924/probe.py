"""H6: paired synthetic compiles at -O1 (libultra recipe of osJamMesg), plain vs `register`. See PROTOCOL.md.

    python3 probe.py   -> results.json
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import pairs  # noqa: E402

E = Path.home() / "decomp/experiments/register-o2-20260924"
PRELUDE = "typedef unsigned int u32;\nextern u32 g(void);\nextern void h(void);\nextern void k(u32);\n"
CASES = {
    "P6a": ("u32 f(void) { {R}u32 m; m = g(); h(); return m; }"),
    "P6b": ("void f({R}u32 a) { h(); k(a); }"),
    "P6c": ("void f(u32 x) { {R}u32 m = x * 3; k(m); }"),
}


def ws_o1() -> Path:
    mirror = pairs.mirror_repo()
    src = pairs.workspace(mirror, "osJamMesg")
    dst = mirror / "nonmatchings" / "zz_register_probe_o1"
    if not dst.exists():
        shutil.copytree(src, dst, symlinks=True)
    return dst


def compile_(ws: Path, stem: str, code: str) -> list[str]:
    (ws / f"{stem}.c").write_text(PRELUDE + code + "\n")
    recipe = next(p.name for p in ws.glob(".compiler-*.sh"))
    r = subprocess.run(["bash", recipe, f"{stem}.c"], cwd=ws, capture_output=True, text=True, timeout=300)
    dump = ws / f"{stem}_object_dump_normalized.s"
    if r.returncode != 0 or not dump.exists():
        raise RuntimeError((r.stdout + r.stderr)[-1500:])
    return [l.strip() for l in dump.read_text().splitlines() if l.strip()]


def facts(lines: list[str]) -> dict:
    ops = [l.split()[0] for l in lines]
    frame = next((int(m.group(1), 0) for l in lines[:3] if (m := re.match(r"addiu\s+sp,sp,-(\S+)", l))), 0)
    return {"frame": frame, "sw_v0": sum(bool(re.match(r"sw\s+v0,", l)) for l in lines),
            "sw_a0": sum(bool(re.match(r"sw\s+a0,", l)) for l in lines),
            "move_s": sum(bool(re.match(r"move\s+s\d,", l)) for l in lines),
            "sw_nonra": sum(op == "sw" and not re.match(r"sw\s+(ra|s\d),", l) for op, l in zip(ops, lines)),
            "lw_sp_nonra": sum(op == "lw" and "(sp)" in l and not re.match(r"lw\s+(ra|s\d),", l)
                               for op, l in zip(ops, lines)),
            "asm": lines}


def main():
    ws = ws_o1()
    out = {}
    for name, template in CASES.items():
        plain = facts(compile_(ws, f"{name}_plain", template.replace("{R}", "")))
        reg = facts(compile_(ws, f"{name}_reg", template.replace("{R}", "register ")))
        out[name] = {"plain": plain, "register": reg}
    p = {k: (v["plain"], v["register"]) for k, v in out.items()}
    verdict = {
        "P6a": p["P6a"][0]["sw_v0"] >= 1 and p["P6a"][0]["lw_sp_nonra"] >= 1 and p["P6a"][1]["sw_v0"] == 0
        and p["P6a"][1]["lw_sp_nonra"] == 0 and p["P6a"][1]["move_s"] >= 1,
        "P6b": p["P6b"][0]["sw_a0"] >= 1 and p["P6b"][1]["sw_a0"] == 0,
        "P6c": p["P6c"][0]["sw_nonra"] >= 1 and p["P6c"][1]["sw_nonra"] == 0,
        "P6d": all(v[1]["frame"] <= v[0]["frame"] for v in p.values()),
    }
    out["verdict"] = verdict | {"H6": "confirmed" if all(verdict.values()) else "refuted as registered"}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    for name in CASES:
        for arm in ("plain", "register"):
            f = out[name][arm]
            print(name, arm, {k: v for k, v in f.items() if k != "asm"})
            print("    ", " | ".join(f["asm"]))
    print(json.dumps(out["verdict"]))


if __name__ == "__main__":
    main()
