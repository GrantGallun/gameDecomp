"""Scratch: identify which C statement emits each instruction around the residual.

    python3 eval/results/rename-wall-20260917/identify.py <function> [attempt_id]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"

EDITS = {
    "orig": [],
    "tileIndex=0x11": [("        tileIndex = 0;", "        tileIndex = 0x11;")],
    "offset=0x22": [("    offset = 0;", "    offset = 0x22;")],
    "i=0x81": [("        i = 0x80;", "        i = 0x81;")],
}


def main() -> int:
    name = sys.argv[1]
    conn = sqlite3.connect(DB)
    addr = conn.execute("select addr from functions where name = ?", (name,)).fetchone()[0]
    attempt = int(sys.argv[2]) if len(sys.argv) > 2 else conn.execute(
        "select id from attempts where func_addr = ? and compiled = 1 order by score desc, id limit 1",
        (addr,)).fetchone()[0]
    source = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    ws = workspace.bootstrap(REPO, name)
    for label, edits in EDITS.items():
        candidate = source
        for old, new in edits:
            if old not in candidate:
                raise SystemExit(f"edit {old!r} not found")
            candidate = candidate.replace(old, new)
        att = workspace.score(ws, REPO, name, candidate, conn=None)
        if not att.compiled:
            print(f"{label:<16} NOT COMPILED")
            continue
        lines = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace").splitlines()
        window = lines[49:64]
        print(f"{label:<16} exact={att.exact} score={att.score:.3f}")
        for line in window:
            print("    ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
