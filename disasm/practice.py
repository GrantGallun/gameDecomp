"""Run the front end on every ROM in a directory and tabulate what happens.

Practice games are hole-finders, not targets: no reference is read here.
Each ROM gets the stages, emission and the round-trip oracle; a crash, a
refusal, a text gap or a round-trip failure is a hole to explain.

    python -m disasm.practice --roms /mnt/c/Code/gameDecomp/roms --out practice.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import traceback
from pathlib import Path


def one(rom_path: Path) -> dict:
    from disasm import emit, roundtrip, run
    t0 = time.time()
    row: dict = {"rom": rom_path.name}
    try:
        fe = run.front_end(rom_path)
    except Exception as e:                       # a refusal is a result
        row.update(stage="front_end", error=f"{type(e).__name__}: {e}",
                   trace=traceback.format_exc()[-1500:])
        return row
    rec = run.receipt(fe, None)
    row.update({
        "title": fe["info"].title, "cic": fe["info"].cic,
        "boot": rec["segments"][0], "code_extent": rec["code_extent"],
        "functions": rec["functions"]["count"],
        "overlays_placed": len(rec["overlays"]),
        "overlays_unplaced": len(rec["unplaced_overlays"]),
        "overlays_failed": rec["failed_overlays"],
        "ledger": {k: rec["ledger"][k] for k in ("unclassified_fraction",
                                                 "text_gap_bytes", "overlapping_regions")},
    })
    try:
        with tempfile.TemporaryDirectory() as td:
            emit.emit(fe, td)
            rt = roundtrip.check(td, fe["data"])
        row["roundtrip"] = {"segments": rt["segments"], "ok": rt["segments_ok"],
                            "failures": [{k: r.get(k) for k in ("segment", "stage", "error",
                                                                 "mismatched_words",
                                                                 "first_mismatches")}
                                         for r in rt["results"] if not r["ok"]][:5]}
    except Exception as e:
        row["roundtrip"] = {"error": f"{type(e).__name__}: {e}"}
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roms", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rows = []
    for p in sorted(a.roms.expanduser().iterdir()):
        if p.suffix.lower() in (".z64", ".n64", ".v64"):
            r = one(p)
            rows.append(r)
            short = {k: r.get(k) for k in ("rom", "error", "functions", "overlays_placed",
                                           "overlays_unplaced", "overlays_failed", "seconds")}
            print(json.dumps(short), flush=True)
    if a.out:
        a.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
