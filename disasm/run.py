"""Run the front end on a ROM; optionally grade it against a finished decomp.

    python -m disasm.run --rom ~/decomp/sbk1/snowboardkids.z64 \\
        --grade-repo ~/decomp/sbk1 --out receipt.json

The stages see only --rom. --grade-repo is read after they have finished, by
`disasm.grade`, and its output goes to the receipt and nowhere else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from disasm import accounting, code_extent, functions, rom


def front_end(rom_path: Path) -> dict:
    data, ri = rom.read(rom_path)
    seg = rom.boot_segment(ri)
    entries = [seg.vram] + ([ri.main_address] if ri.main_address else [])
    ext = code_extent.find(data, seg, entries)
    funcs = functions.find(data, ext)
    return {"data": data, "info": ri, "segment": seg, "extent": ext, "functions": funcs}


def ledger(fe: dict) -> accounting.Ledger:
    seg, ext, funcs = fe["segment"], fe["extent"], fe["functions"]
    led = accounting.Ledger(len(fe["data"]))
    led.add(0, 0x40, "header")
    led.add(0x40, 0x1000, "ipl3")
    text_end = ext.rom(ext.text_end)
    bound = ext.rom(ext.data_bound) if ext.data_bound else seg.rom_end
    led.add(seg.rom_start, text_end, "cpu_text")
    led.add(text_end, bound, "non_cpu_or_data")
    led.add(bound, seg.rom_end, "data")
    led.text_gaps = accounting.text_gaps(seg.rom_start, seg.vram, seg.vram,
                                         ext.text_end, funcs)
    return led


def stage_digest() -> str:
    h = hashlib.sha256()
    for name in ("rom.py", "code_extent.py", "functions.py", "accounting.py"):
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()


def receipt(fe: dict, grade_repo: Path | None) -> dict:
    seg, ext, funcs = fe["segment"], fe["extent"], fe["functions"]
    out = {
        "schema": "disasm-frontend-receipt-v1",
        "stage_sha256": stage_digest(),
        "rom": fe["info"].to_json(),
        "segments": [{"name": seg.name, "rom_start": f"{seg.rom_start:#x}",
                      "rom_end": f"{seg.rom_end:#x}", "vram": f"{seg.vram:#010x}",
                      "derived_from": seg.derived_from}],
        "code_extent": ext.summary(),
        "ledger": ledger(fe).summary(),
        "functions": {"count": len(funcs),
                      "seeded": sum(f.seeded for f in funcs),
                      "with_padding_split": sum(1 for f in funcs if f.padding)},
    }
    if grade_repo is not None:
        from disasm import grade
        ref = grade.reference(grade_repo)
        dec = code_extent.Decoder(fe["data"], seg)
        ours = {f.vram: f.size for f in funcs}
        known = grade.load_known(fe["info"].sha1)
        cmp_ = grade.compare(ours, ref, seg.vram, ext.text_end, dec.word, known)
        out["grade"] = {
            "reference": str(grade_repo),
            "functions": cmp_["summary"],
            "disagreements": {k: [_hexify(x) for x in v]
                              for k, v in cmp_["buckets"].items() if k != "exact"},
            "reference_uncheckable_statics": ref.uncheckable_statics,
            "reference_unexplained_intervals": [(o, f"{a:#x}", f"{b:#x}")
                                                for o, a, b in ref.unexplained_intervals],
            "reference_object_order": ref.object_order,
        }
    return out


def _hexify(x):
    if isinstance(x, int):
        return f"{x:#010x}"
    if isinstance(x, dict):
        return {k: (f"{v:#010x}" if k == "addr" else v) for k, v in x.items()}
    return x


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--grade-repo", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    fe = front_end(args.rom.expanduser())
    rec = receipt(fe, args.grade_repo.expanduser() if args.grade_repo else None)
    text = json.dumps(rec, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("code_extent", "ledger", "functions")}, indent=1))
    if "grade" in rec:
        print(json.dumps(rec["grade"]["functions"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
