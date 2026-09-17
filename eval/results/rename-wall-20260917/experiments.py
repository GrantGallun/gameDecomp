"""Scratch: try source edits that could flip the emission order of the two `move R,zero`.

    python3 eval/results/rename-wall-20260917/experiments.py <function> [attempt_id]
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

ORIGINAL = """    if ((1)) {
        tileIndex = 0;
        i = 0x80;
    }
    offset = 0;
"""

VARIANTS = {
    "orig": ORIGINAL,
    "offset_first_block": """    if ((1)) {
        offset = 0;
        tileIndex = 0;
        i = 0x80;
    }
""",
    "offset_then_tile_then_i": """    tileIndex = 0;
    offset = 0;
    i = 0x80;
""",
    "offset_first_flat": """    offset = 0;
    tileIndex = 0;
    i = 0x80;
""",
    "offset_before_block": """    offset = 0;
    if ((1)) {
        tileIndex = 0;
        i = 0x80;
    }
""",
    "i_first": """    i = 0x80;
    tileIndex = 0;
    offset = 0;
""",
    "offset_after_i_in_block": """    if ((1)) {
        tileIndex = 0;
        i = 0x80;
        offset = 0;
    }
""",
    "no_offset_init": """    if ((1)) {
        tileIndex = 0;
        i = 0x80;
    }
    offset = 0xFFFFFFFF;
""",
}


def main() -> int:
    name = sys.argv[1]
    conn = sqlite3.connect(DB)
    addr = conn.execute("select addr from functions where name = ?", (name,)).fetchone()[0]
    attempt = int(sys.argv[2]) if len(sys.argv) > 2 else conn.execute(
        "select id from attempts where func_addr = ? and compiled = 1 order by score desc, id limit 1",
        (addr,)).fetchone()[0]
    source = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    if ORIGINAL not in source:
        print("ORIGINAL block not found in source; dumping it")
        print(source)
        return 1
    ws = workspace.bootstrap(REPO, name)
    target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")
    print(f"{name} attempt {attempt}")
    for label, block in VARIANTS.items():
        candidate = source.replace(ORIGINAL, block)
        att = workspace.score(ws, REPO, name, candidate, conn=None)
        if not att.compiled:
            print(f"{label:<24} NOT COMPILED")
            continue
        dump = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace")
        report = regalloc_signature.compare(target, dump)
        profile = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), True)
        axes = {a: getattr(profile, a) for a in ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")}
        print(f"{label:<24} exact={att.exact} score={att.score:.3f} grad={list(report.gradient)} "
              f"sig={dict(report.signatures)} axes={axes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
