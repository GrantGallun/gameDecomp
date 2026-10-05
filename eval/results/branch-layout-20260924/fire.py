"""Fire tests on real residuals: compile source variants of an unsolved function's best node in a copied workspace.

    python3 fire.py FUNCTION VARIANTS.json   (VARIANTS: {name: [[old, new], ...]} applied to the best source in order)
Prints score, and the backward/unconditional branch census vs the target, per variant."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import census
import exit_tests

WORK = Path.home() / "decomp/experiments/branch-layout-20260924/ws"


def best_source(name):
    row = json.loads((census.E / "rows" / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    return next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"], row


def build(name, source):
    src_ws = census.E / "ws" / name / "routed"
    dst = WORK / name
    if not dst.exists():
        shutil.copytree(src_ws, dst, symlinks=True)
    ws = dst / "nonmatchings" / name
    (ws / f"{name}.c").write_text(source)
    # The function's recorded compiler recipe (e.g. -O1 for libultra io/*) lives in `.compiler-<hash>.sh`; plain
    # build.sh defaults to -O2, which silently mis-compiles -O1 functions (found: __osAiDeviceBusy base 14.6 vs 65.8).
    recipes = sorted(ws.glob(".compiler-*.sh"))
    script = recipes[0].name if len(recipes) == 1 else "build.sh"
    r = subprocess.run(["bash", "-c", f". {dst}/.venv/bin/activate 2>/dev/null; bash {script} {name}.c"], cwd=ws,
                       capture_output=True, text=True, timeout=300)
    m = re.search(r"Score: ([\d.]+)%", r.stdout + r.stderr)
    dump = ws / f"{name}_object_dump_normalized.s"
    ok = r.returncode == 0 and dump.exists()
    return (float(m.group(1)) if m else None), (dump.read_text().splitlines() if ok else None), r.stdout[-600:] + r.stderr[-600:]


def main(name, variants_path):
    base, _row = best_source(name)
    target = (census.E / "ws" / name / "routed" / "nonmatchings" / name / "target_object_dump_normalized.s").read_text().splitlines()
    t = census.parse(target)
    print("target  back", exit_tests.kinds(t), "blocks", census.skeleton(t)[0])
    variants = {"base": []} | json.loads(Path(variants_path).read_text())
    out = {}
    for vname, edits in variants.items():
        src = base
        for old, new in edits:
            if old not in src:
                print(f"  {vname}: edit not found: {old[:60]!r}")
            src = src.replace(old, new)
        score, dump, log = build(name, src)
        if dump is None:
            print(f"{vname:14s} build failed: {log[-300:]}")
            continue
        c = census.parse(dump)
        cls, info = census.classify(t, c)
        print(f"{vname:14s} score {score} class {cls} back {exit_tests.kinds(c)} cond {info['c_cond']}/{info['t_cond']} "
              f"unc {info['c_uncond']}/{info['t_uncond']} len {info['c_len']}/{info['t_len']}")
        out[vname] = {"score": score, "class": cls, "source": src}
    Path(variants_path).with_suffix(".out.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
