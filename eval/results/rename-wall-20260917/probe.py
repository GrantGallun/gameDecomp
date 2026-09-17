"""Scratch probe: recompile a stored attempt for one function and show its residual.

Run in WSL from the repo root:
    python3 eval/results/rename-wall-20260917/probe.py drawRaceSplitscreenSelectOption2Frame 31662 [file.c]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import regalloc_signature, signals, workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"


def stored_source(conn, attempt_id: int) -> tuple[str, str]:
    row = conn.execute("select f.name, a.source_code from attempts a join functions f on f.addr = a.func_addr "
                       "where a.id = ?", (attempt_id,)).fetchone()
    return row[0], row[1]


def main() -> int:
    name = sys.argv[1]
    source = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 and sys.argv[2].endswith(".c") else None
    conn = sqlite3.connect(DB)
    if source is None:
        attempt = int(sys.argv[2]) if len(sys.argv) > 2 else None
        if attempt is None:
            addr = conn.execute("select addr from functions where name = ?", (name,)).fetchone()[0]
            attempt = conn.execute("select id from attempts where func_addr = ? and compiled = 1 "
                                   "order by score desc, id limit 1", (addr,)).fetchone()[0]
        name, source = stored_source(conn, attempt)
        print(f"attempt {attempt}")
    ws = workspace.bootstrap(REPO, name)
    att = workspace.score(ws, REPO, name, source, conn=None)
    print(f"compiled={att.compiled} exact={att.exact} score={att.score}")
    print("--- diff ---")
    print(att.diff or "(clean)")
    target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")
    dump = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace")
    report = regalloc_signature.compare(target, dump)
    print("--- regalloc_signature ---")
    print(report.to_dict(limit=12))
    print("--- signals ---")
    verdict = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), bool(att.compiled))
    print({a: getattr(verdict, a) for a in
           ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
