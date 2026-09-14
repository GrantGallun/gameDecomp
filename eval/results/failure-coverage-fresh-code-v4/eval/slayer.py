"""Does diffing at IDO's pre-as1 layer isolate a nudge better than the object?

The claim under test, from the reference project's learnings:

    Diffing two candidates' -S output isolates ONE changed decision where
    diffing their objects shows dozens of shifted rows.

If true, this is the feedback signal we lack for register allocation -- our
largest untouched fault class (36 of the 45 residual faults on
renderRaceUiSingleTrailEffect are register choice, and ZERO are structural).

MEASUREMENT
    For each source nudge, both of:
      dS  lines differing between baseline and nudge at the -S layer
      dO  residual lines that MOVED, i.e. the symmetric difference between the
          two candidates' object-vs-target diffs
    The claim predicts dS << dO for a nudge that changes allocation.

CONTROLS MATTER MORE THAN THE TREATMENTS HERE.
    A blank-line insertion must produce dS = dO = 0 -- the project builds
    without -g, and the same document reports blank lines giving a
    byte-identical object. If a control shows movement, the harness is
    measuring noise and no treatment number means anything.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from solver import asmlayer, workspace


def residual_lines(att) -> set[str]:
    return {l for l in (att.diff or "").splitlines()
            if l[:1] in "+-" and not l.startswith(("+++", "---"))}


NUDGES = [
    # --- controls: these must not move anything ---
    ("CONTROL blank line",
     lambda s: s.replace("{\n    /* 1.", "{\n\n    /* 1.", 1)),
    ("CONTROL rename local",
     lambda s: s.replace("RegionBlock *blk", "RegionBlock *blockPtr", 1)
               .replace("blk->", "blockPtr->").replace("(u8 *)blk",
                                                       "(u8 *)blockPtr")
               .replace("blk = ", "blockPtr = ")),
    # --- treatments: catalogued allocation nudges ---
    ("do not cache gRegionAllocPtr",
     lambda s: s.replace(
         "RegionBlock *blk = (RegionBlock *)gRegionAllocPtr;\n"
         "        gRegionAllocPtr = (u8 *)blk + 8;",
         "RegionBlock *blk = (RegionBlock *)gRegionAllocPtr;\n"
         "        gRegionAllocPtr = (u8 *)gRegionAllocPtr + 8;", 1)),
    ("compound assignment for the bump",
     lambda s: s.replace(
         "gRegionAllocPtr = (u8 *)blk + 8;",
         "gRegionAllocPtr += 8;", 1)),
]


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
    score, src = row

    ws = workspace.bootstrap(repo, args.func)
    base_att = workspace.score(ws, repo, args.func, src)
    base_s, err = asmlayer.compile_s(repo, src)
    if not base_s:
        print(f"baseline -S failed: {err[:200]}")
        return 1
    base_res = residual_lines(base_att)
    print(f"{args.func}: baseline {base_att.score:.3f}, "
          f"{len(base_res)} residual lines, "
          f"{len(asmlayer.instructions(base_s))} -S instructions\n")

    print(f"{'nudge':34} {'score':>8} {'dS':>5} {'dO':>5} {'regs':>5}")
    print("-" * 62)
    results = []
    for label, fn in NUDGES:
        cand = fn(src)
        if cand == src:
            print(f"{label:34} {'(no change to source)':>26}")
            continue
        att = workspace.score(ws, repo, args.func, cand)
        cs, cerr = asmlayer.compile_s(repo, cand)
        if not att.compiled or not cs:
            print(f"{label:34} {'did not build':>26}")
            continue
        dS = len(asmlayer.diff(base_s, cs))
        dO = len(residual_lines(att) ^ base_res)
        regs = len(asmlayer.register_changes(base_s, cs))
        print(f"{label:34} {att.score:8.3f} {dS:5} {dO:5} {regs:5}")
        results.append({"nudge": label, "score": att.score, "dS": dS,
                        "dO": dO, "reg_changes": regs,
                        "control": label.startswith("CONTROL")})

    ctrl = [r for r in results if r["control"]]
    treat = [r for r in results if not r["control"]]
    bad = [r for r in ctrl if r["dS"] or r["dO"]]
    print(f"\ncontrols that moved (must be none): {len(bad)}")
    for r in bad:
        print(f"   {r['nudge']}: dS={r['dS']} dO={r['dO']}")
    if bad:
        print("   -> the harness is measuring noise; treatment numbers are void")
    amplifying = [r for r in treat if r["dO"] > r["dS"]]
    print(f"treatments where the object diff is LARGER than the -S diff: "
          f"{len(amplifying)} of {len(treat)}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
