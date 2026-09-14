"""Search catalogued allocation nudges, ranked on REGISTER movement.

Every repair built so far moves a field or retypes one. None can move a value
between registers, and register choice is the largest untouched fault class:
renderRaceUiSingleTrailEffect carries 45 residual faults of which 36 are
register choice and ZERO are structural.

RANKING IS THE POINT, not the enumeration. The imported IDO notes warn that
dist.py's reordering penalty hides the statement-order lever entirely, so the
byte score cannot steer this search -- a nudge that fixes a register can score
lower than one that does nothing. So rank on the count of REGISTER-CHOICE
faults in the residual (solver/signals.py), and keep the score only as a
tie-break and a safety check.

Each nudge is semantics-preserving and comes from patterns/imported_ido.py,
which is registered as hypothesis-only. This run is how one of them earns
promotion, or does not.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from solver import asmlayer, signals, workspace


def build_nudges(src: str) -> list[tuple[str, str, str]]:
    """(label, catalog id, mutated source) for every nudge that applies."""
    out: list[tuple[str, str, str]] = []

    def add(label: str, pid: str, new: str) -> None:
        if new != src:
            out.append((label, pid, new))

    # control: layout is inert without -g, so this must change nothing
    add("CONTROL blank line", "-", src.replace("{\n", "{\n\n", 1))

    # ido-do-not-cache-what-the-target-reloads
    add("uncache alloc ptr", "ido-do-not-cache-what-the-target-reloads",
        src.replace("gRegionAllocPtr = (u8 *)blk + 8;",
                    "gRegionAllocPtr = (u8 *)gRegionAllocPtr + 8;"))

    # ido-compound-assignment-steers-result-register
    add("compound bump", "ido-compound-assignment-steers-result-register",
        src.replace("gRegionAllocPtr = (u8 *)blk + 8;",
                    "gRegionAllocPtr += 8;"))

    # ido-web-ties-break-on-construction-order: separate carriers change the
    # order webs are constructed in, which breaks ties on web number
    n = 0
    sep = src
    while "RegionBlock *blk = (RegionBlock *)gRegionAllocPtr;" in sep and n < 1:
        sep = sep.replace("RegionBlock *blk = (RegionBlock *)gRegionAllocPtr;",
                          "RegionBlock *blkA = (RegionBlock *)gRegionAllocPtr;",
                          1)
        n += 1
    if n:
        sep = re.sub(r"\bblk->", "blkA->", sep, count=2)
        sep = sep.replace("(u8 *)blk + 8", "(u8 *)blkA + 8", 1)
        add("separate first carrier",
            "ido-web-ties-break-on-construction-order", sep)

    # ido-redundant-mask-advances-the-temp-fifo, one and two masks
    for k in (1, 2):
        masked = src.replace(
            "gAssetHandles[0xA]",
            "gAssetHandles[" + "(" * k + "0xA" + " & 0xFFFF)" * k + "]", 1)
        add(f"redundant mask x{k}",
            "ido-redundant-mask-advances-the-temp-fifo", masked)

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--func", default="renderRaceUiSingleTrailEffect")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    row = conn.execute(
        "select a.score, a.source_code from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where f.name = ? and a.compiled = 1 and a.source_code is not null"
        " order by a.score desc limit 1", (args.func,)).fetchone()
    if not row:
        print("no compiling candidate")
        return 1
    _score, src = row

    ws = workspace.bootstrap(repo, args.func)
    base = workspace.score(ws, repo, args.func, src)
    bs = signals.analyse(base.diff, base.score, base.exact, True)
    base_s, _e = asmlayer.compile_s(repo, src)
    print(f"{args.func}  baseline {base.score:.3f}   "
          f"regalloc {bs.regalloc}  offset {bs.offset}  "
          f"structural {bs.structural}\n")

    print(f"{'nudge':28} {'score':>8} {'regalloc':>9} {'struct':>7} {'dRegs':>6}")
    print("-" * 64)
    rows = []
    for label, pid, cand in build_nudges(src):
        att = workspace.score(ws, repo, args.func, cand)
        if not att.compiled:
            print(f"{label:28} {'did not build':>26}")
            continue
        s = signals.analyse(att.diff, att.score, att.exact, True)
        cs, _e2 = asmlayer.compile_s(repo, cand)
        dregs = len(asmlayer.register_changes(base_s, cs)) if cs else -1
        flag = ""
        if s.regalloc < bs.regalloc:
            flag = "  <-- fewer register faults"
        if att.exact:
            flag = "  <-- EXACT"
        print(f"{label:28} {att.score:8.3f} {s.regalloc:9} {s.structural:7} "
              f"{dregs:6}{flag}")
        rows.append({"nudge": label, "pattern": pid, "score": att.score,
                     "regalloc": s.regalloc, "structural": s.structural,
                     "exact": att.exact, "reg_changes_vs_base": dregs,
                     "control": label.startswith("CONTROL")})

    ctrl = [r for r in rows if r["control"]]
    bad = [r for r in ctrl if r["reg_changes_vs_base"] or
           r["regalloc"] != bs.regalloc]
    print(f"\ncontrols that moved (must be none): {len(bad)}")
    better = [r for r in rows
              if not r["control"] and r["regalloc"] < bs.regalloc]
    print(f"nudges reducing register faults: {len(better)} "
          f"(baseline {bs.regalloc})")
    for r in better:
        print(f"   {r['nudge']}: {bs.regalloc} -> {r['regalloc']}  "
              f"[{r['pattern']}]")
    if not better:
        print("   none -- no catalogued nudge moved this residual")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"baseline": {"score": base.score, "regalloc": bs.regalloc,
                          "structural": bs.structural},
             "nudges": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
