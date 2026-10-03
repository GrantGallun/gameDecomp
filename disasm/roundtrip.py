"""Round-trip oracle for the front end: reassemble what we emitted, compare to ROM.

The lock-and-key test one layer down (handoff idea 7). For each segment, the
emitted functions are concatenated in address order with their recorded
padding as zero words, assembled, and linked at the segment's load address.
Every symbol the code references is defined at the address its generated
name encodes (a trailing _<VRAM>), so relocations resolve exactly as in
the original link. The linked bytes must equal the ROM's CPU text.

What this catches: wrong instruction text, wrong %hi/%lo symbolisation,
lost or doubled words at a function seam, padding emitted inside a function
or dropped from the stream. What it does NOT catch: a boundary in the wrong
place -- splitting the same bytes differently reassembles identically.
Boundaries are graded against references and traces, not here.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

AS = "mips-linux-gnu-as"
LD = "mips-linux-gnu-ld"
OBJCOPY = "mips-linux-gnu-objcopy"
NM = "mips-linux-gnu-nm"
# Every generated name ends in the address it stands for: func_, D_, D_DBL_,
# D_FLT_, STR_, jtbl_ ... <8 hex digits>.
ADDR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*_([0-9A-F]{8})$")


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def segment_source(out: Path, entries: list[dict]) -> str:
    parts = ['.include "macro.inc"', ".set noat", ".set noreorder", ".set gp=64",
             ".section .text", ""]
    for e in entries:
        body = (out / e["file"]).read_text(encoding="utf-8")
        body = body.replace('.include "macro.inc"', "").replace(".section .text", "")
        parts.append(body)
        if e["padding"]:
            parts.append(f".fill {e['padding'] // 4}, 4, 0")
    return "\n".join(parts) + "\n"


def check_segment(out: Path, seg: str, entries: list[dict], rom: bytes) -> dict:
    entries = sorted(entries, key=lambda e: int(e["vram"], 16))
    vram = int(entries[0]["vram"], 16)
    rom_lo = int(entries[0]["rom"], 16)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "macro.inc").write_text((out / "macro.inc").read_text())
        (td / "seg.s").write_text(segment_source(out, entries))
        r = _run([AS, "-march=vr4300", "-mabi=32", "-mno-shared", "-G0", "-I", str(td),
                  "seg.s", "-o", "seg.o"], td)
        if r.returncode:
            return {"segment": seg, "ok": False, "stage": "as", "error": r.stderr[-2000:]}
        nm = _run([NM, "-u", "seg.o"], td).stdout.split()
        undefined = [w for w in nm if w != "U"]
        defs, unresolved = [], []
        for name in undefined:
            m = ADDR_NAME.match(name)
            if m:
                defs += ["--defsym", f"{name}=0x{m.group(1)}"]
            else:
                unresolved.append(name)
        if unresolved:
            return {"segment": seg, "ok": False, "stage": "symbols",
                    "error": f"names that do not encode an address: {unresolved[:20]}"}
        r = _run([LD, "-EB", "-Ttext", f"0x{vram:08X}", "--no-check-sections",
                  "-e", "0", *defs, "seg.o", "-o", "seg.elf"], td)
        if r.returncode:
            return {"segment": seg, "ok": False, "stage": "ld", "error": r.stderr[-2000:]}
        r = _run([OBJCOPY, "-O", "binary", "--only-section=.text", "seg.elf", "seg.bin"], td)
        if r.returncode:
            return {"segment": seg, "ok": False, "stage": "objcopy", "error": r.stderr[-2000:]}
        got = (td / "seg.bin").read_bytes()
    want = rom[rom_lo:rom_lo + len(got)]
    last = entries[-1]
    expected_len = (int(last["rom"], 16) + last["size"] + last["padding"]) - rom_lo
    mismatches = [i for i in range(0, min(len(got), len(want)), 4)
                  if got[i:i + 4] != want[i:i + 4]]
    culprits = []
    for i in mismatches[:20]:
        v = vram + i
        f = max((e for e in entries if int(e["vram"], 16) <= v),
                key=lambda e: int(e["vram"], 16))
        culprits.append({"vram": f"{v:#010x}", "function": f["name"],
                         "got": got[i:i + 4].hex(), "want": want[i:i + 4].hex()})
    return {"segment": seg, "ok": not mismatches and len(got) == expected_len,
            "bytes": len(got), "expected_bytes": expected_len,
            "mismatched_words": len(mismatches), "first_mismatches": culprits}


def check(out: Path, rom: bytes) -> dict:
    out = Path(out)
    manifest = json.loads((out / "manifest.json").read_text())
    by_seg: dict[str, list] = {}
    for e in manifest:
        by_seg.setdefault(e["segment"], []).append(e)
    results = [check_segment(out, s, es, rom) for s, es in by_seg.items()]
    return {"segments": len(results),
            "segments_ok": sum(r["ok"] for r in results),
            "results": results}
