"""What KIND of change actually closed a function, across the whole corpus?

The provenance miner recovered 384 functions with incremental Improve/Match
histories -- real refinement trajectories, showing what a human or agent
changed to move a function from 90% to 96% to exact. That is a corpus of
VALIDATED rewrites, and it is exactly what the rewrite generators lack: every
generator so far was built from one function's residual and then generalised on
hope.

WHAT THIS READS, AND WHY IT IS NOT CONTAMINATION
    Only the DISTRIBUTION of transformation kinds, aggregated across the
    corpus. "37% of closing changes adjust a constant" is methodology -- it
    says which generator to build next. It is not a target's answer, and no
    per-function fix for anything we have not matched is printed.

    Commit SUBJECTS are the primary signal here rather than diffs, for the same
    reason: a subject says "fix the loop bound", a diff says what the answer is.

The point is to stop guessing which generator matters. We have five, built
from three functions; this says what the other 380 needed.

    python3 -m eval.trajectory_kinds --provenance /tmp/prov.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# Vocabulary of what a matching change is ABOUT, matched against commit
# subjects and bodies. Deliberately coarse: the question is which generator to
# build, not a taxonomy.
KINDS = [
    ("struct layout / padding",
     r"\bpad(ding)?\b|\boffset\b|\bstruct (size|layout)\b|\balign"),
    ("field type / width",
     r"\b(u8|s8|u16|s16|u32|s32|f32)\b|\bwiden|\bnarrow|\btype\b"),
    ("constant / literal",
     r"\bconstant\b|\bliteral\b|\bmagic\b|\bimmediate\b|\bbound\b"),
    ("argument order / call",
     r"\bargument\b|\bparam(eter)?\b|\bcall\b|\bswap\b|\bprototype\b"),
    ("register allocation",
     r"\bregister\b|\bregalloc\b|\ballocation\b|\btemp(orary)?\b|\bcolou?r"),
    ("control flow / loop shape",
     r"\bloop\b|\bbranch\b|\bcontrol flow\b|\bgoto\b|\bwhile\b|\bfor\b|"
     r"\bif\b|\bswitch\b|\bcondition"),
    ("inline / helper split",
     r"\binline\b|\bhelper\b|\bsplit\b|\bextract\b|\bmerge\b"),
    ("sibling / analogue copy",
     r"\bsibling\b|\banalogue\b|\banalog\b|\bcopy\b|\bmirror\b|\blike\b"),
    ("cast / signedness",
     r"\bcast\b|\bsigned\b|\bunsigned\b|\bsign[- ]exten"),
    ("volatile / scheduling",
     r"\bvolatile\b|\bschedul|\bnop\b|\bdelay slot\b|\breorder"),
]
COMPILED = [(name, re.compile(rx, re.I)) for name, rx in KINDS]


def classify(text: str) -> list[str]:
    return [name for name, rx in COMPILED if rx.search(text)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provenance", required=True)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    data = json.loads(Path(args.provenance).read_text(errors="replace"))
    funcs = data if isinstance(data, list) else data.get("functions", data)
    if isinstance(funcs, dict):
        funcs = list(funcs.values())

    kinds: Counter = Counter()
    unclassified = 0
    with_history = 0
    subjects: list[str] = []

    for fn in funcs:
        if not isinstance(fn, dict):
            continue
        commits = fn.get("history") or []
        rec = fn.get("reasoning_record") or {}
        if not commits or not rec.get("improvement_commits"):
            continue
        with_history += 1
        for c in commits:
            subj = (c.get("subject", "") if isinstance(c, dict) else str(c))
            body = (c.get("body", "") if isinstance(c, dict) else "")
            text = f"{subj} {body}"
            hits = classify(text)
            if hits:
                kinds.update(hits)
            else:
                unclassified += 1
                if len(subjects) < 40:
                    subjects.append(subj[:90])

    total = sum(kinds.values()) or 1
    print(f"functions with a refinement history: {with_history}")
    print(f"classified mentions: {total}   unclassified commits: "
          f"{unclassified}\n")
    print("WHAT CLOSING CHANGES ARE ABOUT (a commit may touch several):")
    for name, n in kinds.most_common():
        have = "have" if name in HAVE_GENERATOR else "MISSING"
        print(f"  {n:5}  ({100*n/total:4.1f}%)  {name:28} generator: {have}")

    if subjects:
        print(f"\nsample of unclassified subjects (vocabulary gaps):")
        for s in subjects[:args.show]:
            print(f"   {s}")
    return 0


# Generators that exist in solver/rewrites.py today.
HAVE_GENERATOR = {
    "struct layout / padding",
    "field type / width",
    "constant / literal",
    "argument order / call",
}

if __name__ == "__main__":
    raise SystemExit(main())
