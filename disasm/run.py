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

from disasm import accounting, code_extent, functions, overlays, rom


def overlay_stage(data: bytes, ov: overlays.Overlay, boot_calls: set[int]) -> dict:
    """Stages 2-3 on a placed overlay, seeded the way the boot segment is.

    Seeds are evidence, not the sequential split: the overlay's first
    function and every call from the boot image that lands on one of its
    split starts. Stage 2's reachability and data bound decide the rest. The
    sequential split alone over-runs into data that happens to decode
    (F-Zero X 0xFB430: its own data loads land inside the "text").
    """
    seg = ov.segment(f"overlay_{ov.rom_start:X}")
    code_vram = seg.vram + (ov.code_start - ov.rom_start)
    starts = {code_vram + s for s in ov.starts}
    entries = [code_vram] + sorted(t for t in boot_calls if t in starts and t != code_vram)
    ext = code_extent.find(data, seg, entries)
    funcs = functions.find(data, ext)
    return {"overlay": ov, "segment": seg, "extent": ext, "functions": funcs,
            "code_vram": code_vram, "entries": len(entries)}


def front_end(rom_path: Path) -> dict:
    data, ri = rom.read(rom_path)
    seg = rom.boot_segment(ri)
    entries = [seg.vram] + ([ri.main_address] if ri.main_address else [])
    ext = code_extent.find(data, seg, entries)
    funcs = functions.find(data, ext)
    found = overlays.find(data, seg, ext)
    dec = code_extent.Decoder(data, seg)
    boot_calls = {t for f in ext.functions for t in dec.walk(f).calls}
    placed, failed = [], []
    for o in found:
        if o.vram is None:
            continue
        try:
            placed.append(overlay_stage(data, o, boot_calls))
        except ValueError as e:                    # a refusal is recorded, not fatal
            failed.append({"overlay": o, "error": str(e)})
    return {"data": data, "info": ri, "segment": seg, "extent": ext,
            "functions": funcs, "overlays": placed, "failed_overlays": failed,
            "unplaced_overlays": [o for o in found if o.vram is None]}


def _segment_ledger(led: accounting.Ledger, seg, ext, funcs, code_vram: int) -> None:
    code_lo = seg.rom_start + (code_vram - seg.vram)
    text_end = ext.rom(ext.text_end)
    bound = ext.rom(ext.data_bound) if ext.data_bound else seg.rom_end
    led.add(seg.rom_start, code_lo, "segment_lead")
    led.add(code_lo, text_end, "cpu_text")
    led.add(text_end, bound, "non_cpu_or_data")
    led.add(bound, seg.rom_end, "data")
    led.text_gaps += accounting.text_gaps(seg.rom_start, seg.vram, code_vram,
                                          ext.text_end, funcs)


def ledger(fe: dict) -> accounting.Ledger:
    led = accounting.Ledger(len(fe["data"]))
    led.add(0, 0x40, "header")
    led.add(0x40, 0x1000, "ipl3")
    seg = fe["segment"]
    _segment_ledger(led, seg, fe["extent"], fe["functions"], seg.vram)
    for o in fe["overlays"]:
        _segment_ledger(led, o["segment"], o["extent"], o["functions"], o["code_vram"])
    for o in fe["unplaced_overlays"]:
        led.add(o.rom_start, o.rom_end, "overlay_unplaced")
    for f in fe["failed_overlays"]:
        led.add(f["overlay"].rom_start, f["overlay"].rom_end, "overlay_failed")
    return led


def stage_digest() -> str:
    h = hashlib.sha256()
    for name in ("rom.py", "code_extent.py", "functions.py", "accounting.py",
                 "overlays.py"):
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()


def receipt(fe: dict, grade_repo: Path | None) -> dict:
    seg, ext, funcs = fe["segment"], fe["extent"], fe["functions"]
    out = {
        "schema": "disasm-frontend-receipt-v2",
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
        "overlays": [{
            "rom_start": f"{o['overlay'].rom_start:#x}",
            "rom_end": f"{o['overlay'].rom_end:#x}",
            "vram": f"{o['segment'].vram:#010x}",
            "sources": o["overlay"].sources,
            "votes": o["overlay"].votes, "runner_up": o["overlay"].runner_up,
            "vote_detail": o["overlay"].vote_detail,
            "text_end_rom": f"{o['extent'].rom(o['extent'].text_end):#x}",
            "functions": len(o["functions"]),
            "entries": o["entries"]} for o in fe["overlays"]],
        "failed_overlays": [{
            "rom_start": f"{f['overlay'].rom_start:#x}", "vram": f"{f['overlay'].vram:#010x}",
            "error": f["error"]} for f in fe["failed_overlays"]],
        "unplaced_overlays": [{
            "rom_start": f"{o.rom_start:#x}", "rom_end": f"{o.rom_end:#x}",
            "votes": o.votes, "runner_up": o.runner_up,
            "vote_detail": o.vote_detail} for o in fe["unplaced_overlays"]],
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
            "overlays": grade.grade_overlays(grade_repo, fe, known),
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
    if args.out:
        args.out.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("code_extent", "ledger", "functions")}, indent=1))
    print(f"overlays placed {len(rec['overlays'])}, unplaced {len(rec['unplaced_overlays'])}")
    if "grade" in rec:
        print(json.dumps(rec["grade"]["functions"], indent=1))
        print(json.dumps(rec["grade"]["overlays"]["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
