"""What is the residual on the functions that nearly matched?

Match count is static, but eight functions have come within 5% and three within
a few instructions. Those are the ones a deterministic repair pass could
plausibly finish, so the useful question is not "how many matched" but "what
exactly is still wrong with the ones that nearly did".

For each near-miss this recompiles its BEST stored candidate, reads the
instruction diff, and classifies the residual. No model, no GPU.

    python3 -m eval.nearmiss --db ~/decomp/kb-sbk1.sqlite --repo ~/decomp/sbk1
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from pathlib import Path

from eval import matched as matched_mod
from solver import workspace

# Classifying a diff line ALONE is wrong, and it produced a wrong answer:
#
#     -lh a2,0x26(s0)      -lh a1,0x24(s0)
#     +lh a1,0x26(s0)      +lh a2,0x24(s0)
#
# reads as four "struct offset" lines, but the offsets are IDENTICAL and only
# the destination registers swapped -- that is argument order, which tracefix
# owns, not layout, which repad owns. A residual is a PAIR, and the signal is
# what differs BETWEEN expected and produced.
MEM = re.compile(r"^([a-z][a-z0-9.]*)\s+(\$?\w+),\s*(-?(?:0x)?[0-9a-f]+)\((\$?\w+)\)")
REG = re.compile(r"^([a-z][a-z0-9.]*)\s+(.*)$")
SHIFT = re.compile(r"^(sll|srl|sra)\s+(\$?\w+),\s*(\$?\w+),\s*(-?(?:0x)?[0-9a-f]+)")


def _num(text: str) -> int | None:
    try:
        return int(text, 16) if text.startswith(("0x", "-0x")) else int(text)
    except ValueError:
        return None


def classify_pair(minus: str, plus: str) -> str:
    """What differs between one expected instruction and one produced."""
    a, b = minus.lstrip("-").strip(), plus.lstrip("+").strip()
    ma, mb = MEM.match(a), MEM.match(b)
    if ma and mb:
        if ma.group(1) != mb.group(1):
            return "access WIDTH wrong (lw vs lh vs lb)"
        if ma.group(3) != mb.group(3):
            return "struct OFFSET wrong (repad owns this)"
        if ma.group(2) != mb.group(2) or ma.group(4) != mb.group(4):
            return "register choice / argument order"
        return "memory access, other"
    sa, sb = SHIFT.match(a), SHIFT.match(b)
    if sa and sb and sa.group(4) != sb.group(4):
        na, nb = _num(sa.group(4)), _num(sb.group(4))
        if na is not None and nb is not None:
            return f"shift amount ({na} vs {nb}) -- element SIZE wrong"
    oa = REG.match(a).group(1) if REG.match(a) else ""
    ob = REG.match(b).group(1) if REG.match(b) else ""
    if oa != ob:
        if {oa, ob} & {"b", "beq", "bne", "beqz", "bnez", "j", "jr"}:
            return "branch shape (b vs beqz etc)"
        return f"different opcode ({oa} vs {ob})"
    if "%hi" in a or "%lo" in a:
        return "relocation / jump-table symbol"
    return "same opcode, different operands"


def pair_up(diff: list[str]) -> list[tuple[str, str]]:
    """Pair each expected line with the produced line opposite it."""
    minus = [l for l in diff if l.startswith("-")]
    plus = [l for l in diff if l.startswith("+")]
    pairs = list(zip(minus, plus))
    for extra in minus[len(plus):]:
        pairs.append((extra, ""))          # instruction missing entirely
    for extra in plus[len(minus):]:
        pairs.append(("", extra))          # instruction added
    return pairs


def classify(line: str) -> str:
    """Single-line fallback, used only for unpaired instructions."""
    if not line.strip("+- "):
        return "other"
    return ("instruction MISSING from candidate" if line.startswith("-")
            else "EXTRA instruction in candidate")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=95.0)
    ap.add_argument("--show", type=int, default=8,
                    help="diff lines to print per function")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))

    rows = conn.execute(
        "select f.name, max(a.score) as best from functions f"
        " join attempts a on a.func_addr = f.addr"
        " group by f.addr having best >= ? and best < 100.0"
        " order by best desc", (args.floor,)).fetchall()

    # A function matched only on disk still has sub-100 attempts logged, so
    # max(score) < 100 lists it as a near miss when it is already solved.
    done = matched_mod.already_matched(conn)
    hidden = [n for n, _b in rows if n in done]
    rows = [(n, b) for n, b in rows if n not in done]
    print(f"{len(rows)} functions between {args.floor} and byte-exact")
    if hidden:
        print(f"(excluded {len(hidden)} already matched elsewhere: "
              f"{', '.join(hidden[:4])})")
    print()
    overall: Counter = Counter()

    for name, best in rows:
        src = conn.execute(
            "select a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.score = ? and a.source_code is not null"
            " limit 1", (name, best)).fetchone()
        if not src:
            continue
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, src[0])
        print("=" * 72)
        print(f"{name}   stored {best:.3f}   re-scored {att.score:.3f}"
              f"   exact={att.exact}")
        if not att.compiled:
            print("  did not reproduce -- candidate no longer compiles")
            continue

        diff = [l for l in (att.diff or "").splitlines()
                if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        print(f"  {len(diff)} differing lines"
              f" ({len(pair_up(diff))} expected/produced pairs)")
        pairs = pair_up(diff)
        kinds = Counter(
            classify_pair(m, pl) if m and pl else classify(m or pl)
            for m, pl in pairs)
        for k, n in kinds.most_common():
            print(f"     {n:3}  {k}")
            overall[k] += n
        for m, pl in pairs[:args.show]:
            print(f"       {m[:88]}")
            print(f"       {pl[:88]}")
        if len(pairs) > args.show:
            print(f"       ... {len(pairs) - args.show} more pairs")

    print("\n" + "=" * 72)
    print("RESIDUAL ACROSS ALL NEAR-MISSES, by what owns the fix:")
    tot = sum(overall.values()) or 1
    for k, n in overall.most_common():
        print(f"  {n:4}  ({100*n/tot:4.1f}%)  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
