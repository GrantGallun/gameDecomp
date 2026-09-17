"""Scratch: bounded structural-variant runner for the `move s2,zero` / `move s3,zero` pair.

    python3 eval/results/rename-wall-20260917/variants.py <function> <attempt_id> <edits.json> [--base other.c]

`edits.json` is a list of {"label": str, "edits": [[old, new], ...]}. Every variant that compiles is
reported with its score, gradient and the four dump lines around the residual, so a variant that
moves the pair is visible without re-reading the diff.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import regalloc_signature, signals, workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def main() -> int:
    name = sys.argv[1]
    attempt = int(sys.argv[2])
    spec = json.loads(Path(sys.argv[3]).read_text())
    conn = sqlite3.connect(DB)
    source = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    ws = workspace.bootstrap(REPO, name)
    target_lines = (ws / "target_object_dump_normalized.s").read_text(errors="replace").splitlines()
    print("target window:")
    for line in target_lines[53:60]:
        print("   |", line)
    for item in spec:
        candidate = source
        missing = [old for old, _new in item["edits"] if old not in candidate]
        if missing:
            print(f"{item['label']:<28} EDIT MISSING {missing[0]!r}")
            continue
        for old, new in item["edits"]:
            candidate = candidate.replace(old, new)
        att = workspace.score(ws, REPO, name, candidate, conn=None)
        if not att.compiled:
            print(f"{item['label']:<28} NOT COMPILED")
            continue
        dump = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace")
        report = regalloc_signature.compare((ws / "target_object_dump_normalized.s").read_text(errors="replace"), dump)
        profile = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), True)
        axes = {a: getattr(profile, a) for a in AXES}
        window = dump.splitlines()[56:58]
        print(f"{item['label']:<28} exact={att.exact} score={att.score:.3f} grad={list(report.gradient)} "
              f"axes={axes} pair={window}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
