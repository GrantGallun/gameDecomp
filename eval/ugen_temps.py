"""How does IDO 5.3's ugen choose expression temporaries? Queue models scored on matched target code (no compiles).

Protocol: eval/results/ugen-temps-20260923/PROTOCOL.md.

    python -m eval.ugen_temps TRACE_DIR --repo ~/decomp/sbk1 --out DIR
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from eval.allocator_rules import procedures
from solver import uopt_attribution

ORDER = ["t6", "t7", "t8", "t9", "t0", "t1", "t2", "t3", "t4", "t5"]
REG = re.compile(r"\$?\b(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")
NO_DEST = re.compile(r"^(s[bhw]|sd|sw[lr]|swc1|sdc1|b\w*|j|jr|jal|jalr|mult|multu|div|divu|mt\w+|nop|break|syscall)$")


def parse(dump: str):
    out = []
    for line in dump.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or parts[0].endswith(":") or parts[0].startswith("."):
            continue
        op, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        regs = REG.findall(rest)
        dest = None if NO_DEST.match(op) or not regs else regs[0]
        reads = regs[1:] if dest else regs
        out.append((op, dest, reads, rest))
    return out


def leaders(insns) -> set[int]:
    lead = {0}
    for i, (op, _d, _r, _rest) in enumerate(insns):
        if op.startswith("b") or op in ("j", "jr", "jal", "jalr"):
            lead.add(i + 2)                          # after the delay slot
    return lead


def simulate(insns, pool: list[str], model: str) -> tuple[int, int, list]:
    lead = leaders(insns) if model == "fifo_block" else set()
    free = collections.deque(pool)
    live: dict[str, int] = {}
    last_read = {}
    for i, (_op, dest, reads, _rest) in enumerate(insns):          # last read of each value, per def
        for r in reads:
            last_read[(r, i)] = True
    hits = total = 0
    misses = []

    def next_read_end(r, start):
        end = None
        for j in range(start + 1, len(insns)):
            if r in insns[j][2]:
                end = j
            if insns[j][1] == r and r not in insns[j][2]:
                break
        return end

    frees_at = collections.defaultdict(list)
    for i, (op, dest, reads, rest) in enumerate(insns):
        if i in lead and i:
            free = collections.deque(r for r in pool)
            live.clear()
        for r in frees_at.pop(i, []):
            if r in live and live[r] <= i:
                del live[r]
                if r in free:
                    free.remove(r)
                free.append(r)
        if dest in pool and dest not in reads and dest not in live:
            candidates = [r for r in free if r not in live]
            predicted = (min(candidates, key=ORDER.index) if model == "lowest" else candidates[0]) if candidates else None
            total += 1
            hits += predicted == dest
            if predicted != dest and len(misses) < 5:
                misses.append({"at": i, "insn": f"{op} {rest}", "predicted": predicted, "actual": dest})
            if dest in free:
                free.remove(dest)
            end = next_read_end(dest, i)
            live[dest] = end if end is not None else i
            frees_at[(end if end is not None else i) + 1].append(dest)
    return hits, total, misses


def allocated(insns, pool):
    """Registers of fresh temporary writes (a pool register written while not live), in emitted order."""
    out, live_until = [], {}
    for i, (_op, dest, reads, _rest) in enumerate(insns):
        if dest in pool and dest not in reads and live_until.get(dest, -1) < i:
            out.append(dest)
            end = i
            for j in range(i + 1, len(insns)):
                if dest in insns[j][2]:
                    end = j
                if insns[j][1] == dest and dest not in insns[j][2]:
                    break
            live_until[dest] = end
    return out


def cyclic_multiset(insns, pool) -> tuple[int, int, bool]:
    """AMENDMENT (after a synthetic probe showed as1 interleaves ugen's allocation order): order-free test.
    FIFO with frees at the tail allocates the cycle pool[0], pool[1], ... in ugen's order; the emitted order is
    as1's. So the n allocated registers must be the first n of the cycle as a multiset."""
    actual = collections.Counter(allocated(insns, pool))
    n = sum(actual.values())
    predicted = collections.Counter(pool[k % len(pool)] for k in range(n)) if pool else collections.Counter()
    overlap = sum((actual & predicted).values())
    return overlap, n, actual == predicted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("traces", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    score = {m: [0, 0] for m in ("fifo", "fifo_block", "lowest")}
    sample = {m: [] for m in score}
    functions = 0
    cyc, cyc_misses = [0, 0, 0, 0], []
    miss_kinds = collections.Counter()
    for proc in procedures(args.traces):
        dump = args.repo / "nonmatchings" / proc.name / "target_object_dump_normalized.s"
        if not dump.exists():
            continue
        numbers = {uopt_attribution.colour_register(r.color) for r in proc.ranges.values() if r.color > 0}
        coloured = {uopt_attribution.REGISTER_NAMES[n] for n in numbers if n is not None}   # float colours: None
        pool = [r for r in ORDER if r not in coloured]
        insns = parse(dump.read_text(errors="replace"))
        functions += 1
        overlap, n, same = cyclic_multiset(insns, pool)
        cyc[0] += overlap
        cyc[1] += n
        cyc[2] += same and n > 0            # two empty multisets are equal; that is not a match
        cyc[3] += n > 0
        if not same and n:
            # skips only: the allocated registers are a sub-multiset of a longer cycle prefix whose extra
            # members were allocated and never emitted; otherwise a register came back before the cycle wrapped
            got = collections.Counter(allocated(insns, pool))
            prefix = collections.Counter()
            m = 0
            while not (got <= prefix) and m < 10 * len(pool) + n:
                prefix[pool[m % len(pool)]] += 1
                m += 1
            kind = "skips_only" if got <= prefix and all(prefix[r] <= got[r] + (m - n) for r in got) else "early_reuse"
            miss_kinds[kind] += 1
            miss_kinds["skipped_registers"] += (m - n) if kind == "skips_only" else 0
        if not same and n and len(cyc_misses) < 15:
            got = allocated(insns, pool)
            cyc_misses.append({"function": proc.name, "pool": pool, "allocated": got,
                               "cycle": [pool[k % len(pool)] for k in range(len(got))]})
        for m in score:
            h, t, miss = simulate(insns, pool, m)
            score[m][0] += h
            score[m][1] += t
            if miss and len(sample[m]) < 12:
                sample[m].append({"function": proc.name, "pool": pool, "misses": miss})
    rates = {m: round(h / t, 4) if t else None for m, (h, t) in score.items()}
    fifo = rates["fifo"] or 0
    verdict = "confirmed" if fifo >= 0.90 and fifo - max(rates["fifo_block"] or 0, rates["lowest"] or 0) >= 0.10 \
        else "not confirmed"
    result = {"functions": functions, "allocations": score["fifo"][1], "rates": rates, "U1_verdict": verdict,
              "amendment_cyclic_multiset": {"allocation_overlap": round(cyc[0] / max(1, cyc[1]), 4),
                                            "functions_exact": cyc[2], "functions_with_temporaries": cyc[3],
                                            "function_rate": round(cyc[2] / max(1, cyc[3]), 4),
                                            "miss_kinds": dict(miss_kinds),
                                            "misses": cyc_misses},
              "miss_samples": sample}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "miss_samples"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
