"""Mine the reference decomp's commit history for matching technique.

842 of the 4,015 commits are "Improve <fn> match to <score>%", and their
messages are unusually rich: they say what was changed, why it worked, and --
rarely available anywhere -- what was tried and found INERT. One commit records
roughly 60,000 variants searched across statement topological order, expression
spelling, initialisation order, operand order, clamp direction, the volatile
qualifier, declaration order, lookup form, padding locals and dimension types.

Two things worth extracting:

  levers    source-level knobs a human actually turns, which is what the solver
            should be told to consider
  inert     knobs recorded as making no difference, which is what the solver
            should NOT waste draws on

Negative results are the scarcer half and the more valuable. Nobody else
publishes "these 9,504 statement orderings were all identical".

Run:
    python3 -m patterns.mine_commits --repo ~/decomp/sbk1 --top 25
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import Counter
from pathlib import Path

# Vocabulary of source-level levers, drawn from reading the commit corpus.
LEVERS = {
    "statement order": r"\b(?:topological|statement|declaration)\s+order",
    "operand order": r"\boperand order|argument order",
    "expression spelling": r"\bspelling\b",
    "local variable": r"\b(?:padding locals?|extra local|intermediate local|"
                      r"named local|local alias)",
    "volatile": r"\bvolatile\b",
    "type width": r"\b(?:dimension types?|narrow(?:ing)?|widen|s16 vs s32|"
                  r"signedness)\b",
    "loop form": r"\b(?:do-while|while loop|for loop|loop shape|unroll)",
    "frame size": r"\bframe (?:size|layout)|stack (?:home|slot|layout)",
    "register allocation": r"\bregister alloc|regalloc|coloring|spill",
    "branch shape": r"\b(?:else|ternary|early return|branch|delay slot)\b",
    "struct layout": r"\bstruct (?:layout|field|size)|field (?:order|signedness)",
    "pointer form": r"\b(?:ptr\+\+|pointer arithmetic|&base->|array index)",
    "constant form": r"\b(?:macro|#define|constant|literal|magic number)",
    "inlining": r"\b(?:inline|helper|common subexpression|CSE)\b",
}

INERT_CUE = re.compile(
    r"(?:found inert|were inert|is inert|made no difference|no improvement|"
    r"no valid improvement|did not (?:help|work|move)|inert:)", re.IGNORECASE)

WORKED_CUE = re.compile(
    r"(?:this is the only source shape|which fixes|now exact|takes .*cost from|"
    r"beats the previous|improves the match|resolves the)", re.IGNORECASE)


def commits(repo: Path) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x01%B%x02"], cwd=repo,
        capture_output=True, text=True).stdout
    result = []
    for chunk in out.split("\x02"):
        if "\x01" not in chunk:
            continue
        h, body = chunk.split("\x01", 1)
        result.append((h.strip(), body.strip()))
    return result


def analyse(repo: Path, top: int) -> None:
    all_commits = commits(repo)
    improve = [(h, b) for h, b in all_commits
               if re.match(r"(?i)improve .* match", b.splitlines()[0] if b else "")]
    match = [(h, b) for h, b in all_commits
             if re.match(r"(?i)^match ", b.splitlines()[0] if b else "")]

    print(f"commits total     : {len(all_commits)}")
    print(f"  'Improve X match': {len(improve)}")
    print(f"  'Match X'        : {len(match)}")

    lever_hits: Counter = Counter()
    inert_hits: Counter = Counter()
    examples: dict[str, str] = {}

    for h, body in improve:
        inert_msg = bool(INERT_CUE.search(body))
        for name, pattern in LEVERS.items():
            m = re.search(pattern, body, re.IGNORECASE)
            if not m:
                continue
            # Classify by the surrounding window, falling back to the whole
            # message. Splitting on '.' fails here -- the corpus is full of
            # 'dist.py' and '90.704%'.
            lo, hi = max(0, m.start()-200), min(len(body), m.end()+200)
            window = body[lo:hi]
            if INERT_CUE.search(window):
                inert_hits[name] += 1
            else:
                lever_hits[name] += 1
                if not inert_msg:
                    examples.setdefault(name, f"{h[:8]}: {window.strip()[:200]}")

    print(f"\n{'lever':22} {'worked':>7} {'inert':>7}")
    print("-" * 40)
    for name in sorted(set(lever_hits) | set(inert_hits),
                       key=lambda n: -(lever_hits[n] + inert_hits[n])):
        print(f"{name:22} {lever_hits[name]:7} {inert_hits[name]:7}")

    print("\nExamples of levers that MOVED a score:")
    for name, ex in list(examples.items())[:top]:
        print(f"\n  [{name}]\n    {ex}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    analyse(args.repo.expanduser(), args.top)


if __name__ == "__main__":
    main()
