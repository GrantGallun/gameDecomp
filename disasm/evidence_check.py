"""Does ROM-only evidence equal reference-ELF evidence? (grading side)

The miner's evidence tier used to need the reference build's ELF for function
extents. `miner.evidence.funcs_from_frontend` derives them from the ROM. On a
finished decomp both paths can run, and the evidence rows they produce for the
same function must be identical -- same instructions, same base resolution,
same widths. Any difference is either a boundary disagreement the grader
already explains, or a bug.

    python -m disasm.evidence_check --rom ~/decomp/sbk1/snowboardkids.z64 \\
        --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def rows_by_function(funcs) -> dict[int, list[tuple]]:
    from miner.evidence import evidence_rows, resolve_bases
    out = {}
    for f in funcs:
        out[f.addr] = [tuple(sorted(r.items())) for r in evidence_rows(f, resolve_bases(f))]
    return out


def compare(rom_path: Path, repo: Path) -> dict:
    from disasm import run
    from miner.evidence import disassemble, funcs_from_frontend
    fe = run.front_end(rom_path)
    ours = rows_by_function([f for seg, f in funcs_from_frontend(fe) if seg == "boot"])
    elf = next(Path(repo).glob("build/*.elf"))
    lo, hi = fe["segment"].vram, fe["segment"].vram_end
    theirs = rows_by_function([f for f in disassemble(elf) if lo <= f.addr < hi])
    both = set(ours) & set(theirs)
    same = sorted(a for a in both if ours[a] == theirs[a])
    differ = sorted(a for a in both if ours[a] != theirs[a])
    ext = fe["extent"]
    bound = ext.data_bound or fe["segment"].vram_end
    from disasm import grade
    known = grade.load_known(fe["info"].sha1)
    reference = grade.reference(repo)
    statics = {a for a, f in reference.funcs.items() if f.kind == "static"}
    statics |= {a for a in ours for _, ivs, _ in reference.shared_static_intervals
                if any(x < a < y for x, y in ivs)}     # uncheckable, shared intervals
    rom_only = sorted(set(ours) - set(theirs))
    elf_only = sorted(set(theirs) - set(ours))
    h = lambda xs: [f"{a:#010x}" for a in xs]
    return {
        "functions_rom": len(ours), "functions_elf": len(theirs),
        "identical": len(same),
        "differ_whitelisted": h(a for a in differ if a in known),
        "differ_unexplained": h(a for a in differ if a not in known),
        # Functions the ELF path never mined: IDO statics have no ELF symbol.
        "rom_only_static": h(a for a in rom_only if a in statics),
        "rom_only_other": h(a for a in rom_only if a not in statics),
        # The reference types its RSP microcode as STT_FUNC in .main, so the
        # ELF path decodes RSP code as CPU instructions. Not CPU code.
        "elf_only_non_cpu": h(a for a in elf_only if ext.text_end <= a < bound),
        "elf_only_whitelisted": h(a for a in elf_only
                                  if a in known and not ext.text_end <= a < bound),
        "elf_only_unexplained": h(a for a in elf_only
                                  if a not in known and not ext.text_end <= a < bound),
        "evidence_rows_rom": sum(len(v) for v in ours.values()),
        "evidence_rows_elf": sum(len(v) for v in theirs.values()),
        "evidence_rows_elf_non_cpu": sum(len(theirs[a]) for a in elf_only
                                         if ext.text_end <= a < bound),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args()
    print(json.dumps(compare(a.rom.expanduser(), a.repo.expanduser()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
