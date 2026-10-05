"""Branch-shape census over every unsolved function (exploration, before any protocol).

For each unsolved function's best node in the restart round-3 run (the same population as the frame census), the
candidate object dump is rebuilt by applying that node's recorded unified diff to the target's normalized dump (no
compiles). Both are cut into basic blocks (leaders: entry, branch targets, the instruction after a delay slot), and
the control-flow skeleton is compared independently of instruction counts, by expressing branch targets as block
ordinals.

Per function, one structural class, first match wins:
  same-skeleton     branch opcodes and block-ordinal targets identical (any branch diff is offset knock-on)
  delay-fill        identical once as1's fill-from-target is undone (delay slot = copy of the instruction
                    before the target, branch retargeted past it): a scheduling knock-on, not C control flow
  inverted          same branch count and targets up to condition sense (beq<->bne, blez<->bgtz, ...) or arm order
  loop-shape        the number of backward branches differs (loop form: guard, rotation, do/while)
  jump-table        a `jr` on a non-ra register on exactly one side (switch as table vs chain)
  likely            branch-likely opcodes differ (beqzl/bnel...) but the rest agrees
  count             conditional branch count differs by forward branches only (extra/missing tests or arms)
  other             same count, different opcodes or targets not explained above
Plus per-function counts, so classes can be weighted by how much else differs.

    python3 census.py            -> census.json
"""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments/restart-round3-20260923"

COND = {"beq", "bne", "beqz", "bnez", "blez", "bgtz", "bltz", "bgez", "bc1t", "bc1f",
        "beql", "bnel", "beqzl", "bnezl", "blezl", "bgtzl", "bltzl", "bgezl", "bc1tl", "bc1fl"}
LIKELY = {"beql", "bnel", "beqzl", "bnezl", "blezl", "bgtzl", "bltzl", "bgezl", "bc1tl", "bc1fl"}
UNCOND = {"b", "j"}
INVERSE = {"beq": "bne", "bne": "beq", "beqz": "bnez", "bnez": "beqz", "blez": "bgtz", "bgtz": "blez",
           "bltz": "bgez", "bgez": "bltz", "bc1t": "bc1f", "bc1f": "bc1t"}


def parse(lines):
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        op, _, rest = line.partition(" ")
        out.append((op, [x.strip() for x in rest.strip().split(",")] if rest.strip() else []))
    return out


def apply_diff(target_lines, diff):
    """Rebuild the candidate from the target and a unified diff of target -> candidate."""
    out, i = [], 0
    lines = diff.splitlines()
    k = 0
    while k < len(lines) and not lines[k].startswith("@@"):
        k += 1
    while k < len(lines):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[k])
        start = int(m.group(1)) - (0 if m.group(2) == "0" else 1)
        out.extend(target_lines[i:start])
        i = start
        k += 1
        while k < len(lines) and not lines[k].startswith("@@"):
            s = lines[k]
            if s.startswith("-"):
                if target_lines[i] != s[1:]:
                    raise ValueError(f"deleted line {i} does not match target")
                i += 1
            elif s.startswith("+"):
                out.append(s[1:])
            elif s.startswith(" "):
                if target_lines[i] != s[1:]:
                    raise ValueError(f"context line {i} does not match target")
                out.append(s[1:])
                i += 1
            elif s.startswith("\\"):
                pass
            k += 1
    out.extend(target_lines[i:])
    return out


def target_of(op, args):
    if op in COND or op in UNCOND:
        try:
            return int(args[-1], 16) // 4
        except ValueError:
            return None
    return None


def canonical_target(insns, idx, t):
    """Undo as1's fill-from-target: the delay slot holds a copy of the instruction just before the target."""
    if t is not None and idx + 1 < t - 1 < len(insns) and insns[t - 1] == insns[idx + 1]:
        return t - 1
    if t is not None and 0 <= t - 1 < idx and insns[t - 1] == insns[idx + 1]:
        return t - 1
    return t


def skeleton(insns, canonical=False):
    """Blocks and branch list: (block ordinal of branch, opcode, target block ordinal, backward?)."""
    n = len(insns)
    leaders = {0}
    targets = {}
    for idx, (op, args) in enumerate(insns):
        t = target_of(op, args)
        if canonical and idx + 1 < n:
            t = canonical_target(insns, idx, t)
        targets[idx] = t
        if t is not None:
            if 0 <= t < n:
                leaders.add(t)
            if idx + 2 < n:
                leaders.add(idx + 2)
        elif op == "jr" and idx + 2 < n:
            leaders.add(idx + 2)
    order = sorted(leaders)
    block_of = {}
    b = -1
    for idx in range(n):
        if idx in leaders:
            b += 1
        block_of[idx] = b
    branches = []
    for idx, (op, args) in enumerate(insns):
        t = targets[idx]
        if t is not None:
            branches.append((block_of[idx], op, block_of.get(t, -1), t <= idx))
        elif op == "jr" and args and args[0] != "ra":
            branches.append((block_of[idx], "jr-table", -1, False))
    return len(order), branches


def classify(t_ins, c_ins):
    tb, tbr = skeleton(t_ins)
    cb, cbr = skeleton(c_ins)
    t_ops = [o for _, o, _, _ in tbr]
    c_ops = [o for _, o, _, _ in cbr]
    info = {
        "t_blocks": tb, "c_blocks": cb,
        "t_cond": sum(o in COND for o in t_ops), "c_cond": sum(o in COND for o in c_ops),
        "t_back": sum(bk for *_, bk in tbr), "c_back": sum(bk for *_, bk in cbr),
        "t_uncond": sum(o in UNCOND for o in t_ops), "c_uncond": sum(o in UNCOND for o in c_ops),
        "t_table": t_ops.count("jr-table"), "c_table": c_ops.count("jr-table"),
        "t_likely": sum(o in LIKELY for o in t_ops), "c_likely": sum(o in LIKELY for o in c_ops),
        "t_len": len(t_ins), "c_len": len(c_ins),
    }
    if tbr == cbr:
        cls = "same-skeleton"
    elif skeleton(t_ins, True)[1] == skeleton(c_ins, True)[1]:
        cls = "delay-fill"
    elif (info["t_table"] > 0) != (info["c_table"] > 0):
        cls = "jump-table"
    elif info["t_back"] != info["c_back"]:
        cls = "loop-shape"
    elif len(tbr) == len(cbr) and all(a[1] == b[1] or INVERSE.get(a[1]) == b[1] for a, b in zip(tbr, cbr)):
        cls = "inverted"
    elif info["t_likely"] != info["c_likely"] and info["t_cond"] == info["c_cond"]:
        cls = "likely"
    elif info["t_cond"] != info["c_cond"] or info["t_uncond"] != info["c_uncond"]:
        cls = "count"
    else:
        cls = "other"
    info["inverted_pairs"] = sum(INVERSE.get(a[1]) == b[1] for a, b in zip(tbr, cbr)) if len(tbr) == len(cbr) else None
    return cls, info


def main():
    rows, counts = [], collections.Counter()
    for path in sorted((E / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        name = row["function"]
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        v = node["verdict"]
        if not v.get("compiled") or not v.get("raw_diff"):
            counts["no-diff"] += 1
            continue
        ws = E / "ws" / name / row["arm"] / "nonmatchings" / name / "target_object_dump_normalized.s"
        if not ws.exists():
            counts["no-target"] += 1
            continue
        tl = ws.read_text().splitlines()
        try:
            cl = apply_diff(tl, v["raw_diff"])
        except (ValueError, IndexError):
            counts["diff-does-not-apply"] += 1
            continue
        t_ins, c_ins = parse(tl), parse(cl)
        cls, info = classify(t_ins, c_ins)
        counts[cls] += 1
        rows.append({"function": name, "class": cls, "score": v.get("score"), **info})
    size = collections.Counter()
    for r in rows:
        bucket = "small" if r["t_len"] < 50 else "medium" if r["t_len"] < 150 else "large"
        size[(bucket, r["class"])] += 1
    out = {"counts": dict(counts), "by_size": {f"{a}/{b}": n for (a, b), n in sorted(size.items())}, "rows": rows}
    (HERE / "census.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"counts": out["counts"], "by_size": out["by_size"]}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
