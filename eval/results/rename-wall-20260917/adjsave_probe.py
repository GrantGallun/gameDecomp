"""Which uopt quantity numbers two saved ranges the way the target needs?

The measured coupling (see RESULT.md) is that the two `move R,zero` instructions are always
emitted s3-then-s2, and whichever value is emitted first holds the higher saved register. The
target needs the first-emitted value in the LOWER one, which no mutation reaches.

`solver.uopt_trace` reports each live range's `adjsave` weight -- the quantity uopt's save pass
orders by (the 2026-09-14 census: constrained ranges follow non-increasing adjsave in 1,907 of
1,976 procedures). This prints every saved-band range of a source with its adjsave, so two
assignments can be compared directly instead of guessed at.

    python3 eval/results/rename-wall-20260917/adjsave_probe.py <function> <attempt_id>[,<attempt_id>...]
    python3 eval/results/rename-wall-20260917/adjsave_probe.py <function> <attempt_id> --source file.c
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import uopt_diagnosis, uopt_trace, workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"
TRACE_CC = Path.home() / "decomp" / "tools-src" / "ido-trace" / "cc"
SAVED_BAND = range(14, 23)


def show(label: str, name: str, source: str) -> None:
    ws = workspace.bootstrap(REPO, name)
    texts = uopt_diagnosis.traced_compile(ws, REPO, source, TRACE_CC, name)
    if not texts:
        print(f"=== {label}: traced compile FAILED")
        return
    proc = uopt_trace.join(texts["level5"], texts["level6"]).get(name)
    if proc is None:
        print(f"=== {label}: no colouring trace for {name}")
        return
    print(f"=== {label} — saved-band ranges, by colour ===")
    rows = [(record.color, lr, record) for lr, record in proc.ranges.items()
            if record.color in SAVED_BAND]
    for colour, lr, record in sorted(rows, key=lambda r: r[0]):
        print(f"  colour={colour:<4} lr={lr:<5} kind={str(record.kind):<6} node={record.node} "
              f"offset={record.offset} adjsave={record.adjsave} constant={record.constant}")
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    adjsaves = [round(proc.ranges[lr].adjsave, 3) for lr in order if lr in proc.ranges]
    print(f"  decision order: {order}")
    print(f"  adjsave in that order: {adjsaves}"
          f"   descending: {all(a >= b for a, b in zip(adjsaves, adjsaves[1:]))}")


def main() -> int:
    name = sys.argv[1]
    conn = sqlite3.connect(DB)
    if "--source" in sys.argv:
        source = Path(sys.argv[sys.argv.index("--source") + 1]).read_text()
        show(f"{name} {sys.argv[2]}", name, source)
        return 0
    for attempt in sys.argv[2].split(","):
        source = conn.execute("select source_code from attempts where id = ?",
                              (int(attempt),)).fetchone()[0]
        show(f"{name} attempt {attempt}", name, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
