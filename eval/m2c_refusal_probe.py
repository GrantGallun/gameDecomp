"""Why did m2c refuse these 79 functions, and is the refusal fixable?

`tools/claude` writes `// file is blank because m2c failed to decompile function` whenever m2c exits
non-zero, and then throws the diagnostic away (`echo "$M2C_OUTPUT"` to a scrollback nobody keeps). So
79 workspaces -- 3.8% of the tree, and 4 wasted compiles each in the admission run -- have no
hypothesis at all, and the reason is not recorded anywhere.

This re-runs the exact bootstrap invocation against each workspace's own `target.s` and keeps the
stderr. A refusal that is an m2c capability gap (unsupported opcode, unhandled jump table) is a
different problem from one that is an invocation bug (missing context, bad flags, a `.set` directive
m2c chokes on) -- and only the second is ours to fix.

    python3 -m eval.m2c_refusal_probe [--limit N] [--repo PATH]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = Path.home() / "decomp/sbk1"
CENSUS = ROOT / "eval/results/blank-draft-census-20260917.json"


def classify(stderr: str, stdout: str) -> str:
    blob = f"{stderr}\n{stdout}"
    for pattern, label in (
        (r"Traceback", "python-traceback"),
        (r"Syntax error|parse error|Could not parse", "parse-error"),
        (r"Unknown instruction|unsupported instruction|Invalid instruction|unrecognized",
         "unsupported-instruction"),
        (r"jump table|jtbl|switch", "jump-table"),
        (r"no such file|not found|command not found", "invocation"),
        (r"\.set|directive|macro", "directive"),
    ):
        if re.search(pattern, blob, re.I):
            return label
    return "other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--census", type=Path, default=CENSUS)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/m2c-refusal-20260917.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--m2c", default="", help="path to the m2c entry point; defaults to PATH lookup")
    ap.add_argument("--context", action="store_true",
                    help="pass --context ctx.c when the workspace has one (the bootstrap default)")
    args = ap.parse_args(argv)

    m2c = args.m2c or shutil.which("m2c")
    print(f"m2c: {m2c}")
    if not m2c:
        print("m2c not on PATH; run inside the WSL decomp environment")
        return 2
    names = json.loads(args.census.read_text())["no_draft"]
    if args.limit:
        names = names[:args.limit]

    rows = {}
    for name in names:
        ws = args.repo / "nonmatchings" / name
        asm = ws / "target.s"
        if not asm.is_file():
            rows[name] = {"status": "no-target-asm"}
            continue
        argv_m2c = [m2c, "--target", "mips-ido-c"]
        if args.context and (ws / "ctx.c").is_file():
            argv_m2c += ["--context", "ctx.c"]
        proc = subprocess.run(argv_m2c + [str(asm)], cwd=ws, capture_output=True, text=True,
                              timeout=180)
        err = (proc.stderr or "").strip()
        rows[name] = {"exit": proc.returncode,
                      "class": classify(err, proc.stdout or "") if proc.returncode else "ok",
                      "stderr_head": err.splitlines()[:4],
                      "asm_bytes": asm.stat().st_size}
        print(f"  {name:<30} exit={proc.returncode} {rows[name].get('class')} "
              f"{(err.splitlines() or [''])[0][:90]}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    print(collections.Counter(r.get("class") for r in rows.values()))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
