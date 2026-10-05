"""Round 7, EXPLORATION on half A only (TU name hash parity 0). Half B is untouched until PROTOCOL-locals.md.

1. Decomposition check (both halves; it tests arithmetic, not a prediction): locals bytes L = distinct stack
   words accessed in [outgoing top, saved-register floor); frame == align8(outgoing + L + saves)?
2. Half A: how does L/4 relate to trace counts?
"""
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import frame_size  # noqa: E402
from solver import uopt_trace  # noqa: E402

TRACES = Path.home() / "decomp/tools-src/uopt-trace-census/traces"
REPO = Path.home() / "decomp/sbk1"
ACCESS = re.compile(r"^(?:l[bhw]u?|s[bhw]|lwc1|swc1|ldc1|sdc1|addiu)\s+\$?\w+,(?:\$?\w+,)?(0x[0-9a-f]+|\d+)\(sp\)", re.M)
ADDR = re.compile(r"^addiu\s+\$?\w+,sp,(0x[0-9a-f]+|\d+)$", re.M)


def half(tu: str) -> int:
    return int(hashlib.sha256(tu.encode()).hexdigest(), 16) % 2


def rows():
    for l5 in sorted(TRACES.glob("*.l5")):
        l6 = l5.with_suffix(".l6")
        if not l6.exists():
            continue
        procs = uopt_trace.join(l5.read_text(errors="replace"), l6.read_text(errors="replace"))
        for name, proc in procs.items():
            dump = REPO / "nonmatchings" / name / "target_object_dump_normalized.s"
            if not dump.exists():
                continue
            text = "\n".join(l.strip() for l in dump.read_text(errors="replace").splitlines())
            f = frame_size.facts(text, proc)
            floor = f["frame"] - 4 * f["saved_int"] - 8 * f["saved_float"]
            slots = [int(m.group(1), 0) for m in re.finditer(r"^s[bhw]c?1?\s+\$?\w+,(0x[0-9a-f]+|\d+)\(sp\)", text, re.M)]
            outgoing = max([16] + [s + 4 for s in slots if 16 <= s < floor]) if f["calls"] else 0
            words = {int(m.group(1), 0) // 4 for m in ACCESS.finditer(text)} | {int(m.group(1), 0) // 4 for m in ADDR.finditer(text)}
            local_words = {w for w in words if outgoing <= 4 * w < floor}
            L = 4 * len(local_words)
            not_colored = sum(1 for d in proc.decisions if d.outcome == "not_colored")
            by_node = collections.defaultdict(list)
            for r in proc.ranges.values():
                if r.kind == "M":
                    by_node[r.node].append(r.color)
            uncoloured_m = sum(1 for c in by_node.values() if all(x <= 0 for x in c))
            partly_m = sum(1 for c in by_node.values() if any(x <= 0 for x in c) and any(x > 0 for x in c))
            yield {"tu": l5.stem, "half": half(l5.stem), "function": name, "frame": f["frame"],
                   "decomposed": frame_size.align8(outgoing + L + 4 * f["saved_int"] + 8 * f["saved_float"]),
                   "L_words": L // 4, "not_colored": not_colored, "splits": len(proc.splits),
                   "uncoloured_m": uncoloured_m, "partly_m": partly_m}


def main():
    data = list(rows())
    ok = sum(r["frame"] == r["decomposed"] for r in data)
    print(f"decomposition check: {ok}/{len(data)} frames == align8(outgoing + L + saves)")
    a = [r for r in data if r["half"] == 0]
    for key in ("not_colored", "uncoloured_m", "splits", "partly_m"):
        agree = sum(r["L_words"] == r[key] for r in a)
        print(f"half A ({len(a)}): L_words == {key}: {agree}")
    combo = collections.Counter((r["L_words"] - r["not_colored"]) for r in a)
    print("half A: L_words - not_colored:", combo.most_common(8))
    combo2 = collections.Counter((r["L_words"] - r["uncoloured_m"] - r["splits"]) for r in a)
    print("half A: L_words - uncoloured_m - splits:", combo2.most_common(8))
    Path("/mnt/c/Code/gameDecomp/eval/results/frame-size-20260923/locals_rows.json").write_text(json.dumps(data))


if __name__ == "__main__":
    main()
