"""Does the ordering generator (`rewrites.statement_order_rewrites`) move the `move` pair?

The 99.936 siblings are booked `ordering=2` by `signals.analyse`, and `eval/repair.py` gained an
`order:*` rung for exactly that class. This enumerates that pass's own variants for the recorded
best source, compiles each, and reports the two-instruction pair, the register gradient and the axes.
It is the FIRES half of the ordering rung, on the residual it was wired for.

    python3 eval/results/rename-wall-20260917/ordering_probe.py <function> <attempt_id>
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import regalloc_signature, rewrites, signals, workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def main() -> int:
    name, attempt = sys.argv[1], int(sys.argv[2])
    conn = sqlite3.connect(DB)
    source = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    diff = conn.execute("select diff_summary from attempts where id = ?", (attempt,)).fetchone()[0] or ""
    ws = workspace.bootstrap(REPO, name)
    target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")

    for gate in (False, True):
        found = list(rewrites.statement_order_rewrites(source, diff, gate=gate))
        print(f"gate={gate}: {len(found)} variants")
        for rewrite in found:
            print(f"   {rewrite.label}")
    variants = list(rewrites.statement_order_rewrites(source, diff, gate=False))
    for rewrite in variants:
        candidate = rewrite(source)
        att = workspace.score(ws, REPO, name, candidate, conn=None)
        if not att.compiled:
            print(f"{rewrite.label:<50} NOT COMPILED")
            continue
        dump = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace")
        report = regalloc_signature.compare(target, dump)
        profile = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), True)
        axes = {a: getattr(profile, a) for a in AXES}
        lines = dump.splitlines()
        pair = next((lines[i:i + 2] for i in range(len(lines) - 1)
                     if lines[i].split() in (["move", "s2,zero"], ["move", "s3,zero"])
                     and lines[i + 1].split() in (["move", "s2,zero"], ["move", "s3,zero"])), None)
        print(f"{rewrite.label:<50} exact={att.exact} score={att.score:.3f} grad={list(report.gradient)} "
              f"reordered={report.reordered} renames={report.renames} axes={axes} pair={pair}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
