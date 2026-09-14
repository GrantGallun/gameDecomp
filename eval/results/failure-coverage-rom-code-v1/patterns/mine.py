"""Mine recurring instruction idioms from a finished decomp.

A matched decomp is a corpus of confirmed (C, asm) pairs. Every idiom a human
decompiler learns over months -- "that multiply-shift-add sequence is a divide
by ten", "that lui/lw/jr is a switch" -- is a frequent asm shape with a stable
C counterpart. Those are findable by counting rather than by waiting to trip
over them.

This is deliberately a *discovery* tool, not a decision tool. It proposes
candidates ranked by how idiom-like they look; nothing it emits changes
behaviour until a human or model confirms it against source and writes a
catalog entry with provenance. Frequency is not correctness -- the most common
sequence in any MIPS binary is the function prologue, which is not an idiom.

Run:
    python3 -m patterns.mine --repo ~/decomp/sbk1 --n 4 --top 25
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from miner.evidence import disassemble

# Sequences that are structure, not idiom. Counting these swamps everything
# interesting -- every function has a prologue and an epilogue.
BORING_OPS = {"nop", "addiu", "sw", "lw", "jr", "move", "or"}

# An idiom usually involves at least one of these: arithmetic that a human
# would not write directly, or control flow with a distinctive shape.
INTERESTING_OPS = {
    "multu", "mult", "mfhi", "mflo", "div", "divu", "sra", "srl", "sll",
    "mtc1", "mfc1", "cvt.s.w", "cvt.w.s", "cvt.d.s", "cvt.s.d", "trunc.w.s",
    "c.lt.s", "c.le.s", "c.eq.s", "bc1t", "bc1f", "neg.s", "abs.s",
    "sltiu", "slti", "sltu", "xori", "andi", "nor", "srav", "sllv",
}


def ngrams(funcs, n: int):
    """Count opcode n-grams, remembering one example site for each."""
    counts: Counter = Counter()
    examples: dict[tuple, tuple[str, int]] = {}

    for func in funcs:
        ops = [i.decoded.getOpcodeName() for i in func.insns]
        for idx in range(len(ops) - n + 1):
            gram = tuple(ops[idx:idx + n])
            counts[gram] += 1
            examples.setdefault(gram, (func.name, func.insns[idx].addr))

    return counts, examples


def score(gram: tuple) -> float:
    """How idiom-like is this sequence?

    Rewards sequences containing arithmetic or float ops a human would not
    write literally, penalises those made only of structural filler.
    """
    interesting = sum(1 for op in gram if op in INTERESTING_OPS)
    boring = sum(1 for op in gram if op in BORING_OPS)
    if interesting == 0:
        return 0.0
    return interesting / len(gram) - 0.3 * (boring / len(gram))


def mine(repo: Path, n: int, top: int, min_count: int) -> None:
    elf = next(repo.glob("build/*.elf"))
    funcs = disassemble(elf)
    counts, examples = ngrams(funcs, n)

    print(f"corpus: {len(funcs)} functions, "
          f"{sum(len(f.insns) for f in funcs)} instructions")
    print(f"distinct {n}-grams: {len(counts)}\n")

    ranked = [
        (gram, cnt, score(gram))
        for gram, cnt in counts.items()
        if cnt >= min_count and score(gram) > 0
    ]
    ranked.sort(key=lambda r: (r[2], r[1]), reverse=True)

    print(f"{'count':>6}  {'score':>5}  sequence")
    print("-" * 78)
    for gram, cnt, sc in ranked[:top]:
        name, addr = examples[gram]
        seq = " ".join(gram)
        print(f"{cnt:6}  {sc:5.2f}  {seq}")
        print(f"{'':13}  e.g. {name} @ {addr:#x}")

    if not ranked:
        print("(nothing scored above zero -- widen --n or lower --min-count)")

    print("\nCandidates only. Confirm against source before adding to "
          "patterns/catalog.py with provenance.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--n", type=int, default=4, help="n-gram length")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-count", type=int, default=8)
    args = ap.parse_args()
    mine(args.repo.expanduser(), args.n, args.top, args.min_count)


if __name__ == "__main__":
    main()
