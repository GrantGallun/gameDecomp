"""For each target b-next the candidate lacks, classify the site (exploration):
  select        the delay slot writes register R, and R is also written in the 3 instructions before the
                preceding conditional branch or in its delay slot (the else value hoisted; H1' / E1 shape)
  return-arm    the b-next lands on the epilogue (`lw ra` / `jr ra` within 3), so the arm is a return
  other         anything else"""
import collections
import json
import re
from pathlib import Path

import census


def dest(ins):
    op, args = ins
    return args[0] if args and not op.startswith(("s", "b", "j")) else None


def sites(insns):
    out = []
    for idx, (op, args) in enumerate(insns):
        if op != "b" or census.target_of(op, args) != idx + 2:
            continue
        slot = insns[idx + 1] if idx + 1 < len(insns) else None
        after = [o for o, _ in insns[idx + 2:idx + 5]]
        cb = next((k for k in range(idx - 1, max(-1, idx - 12), -1) if insns[k][0] in census.COND), None)
        # the else value sits above the conditional branch, or in its delay slot (as1 fill-from-target)
        before = [dest(insns[k]) for k in range(max(0, (cb or 0) - 3), (cb or 0) + 2) if k != cb] if cb is not None else []
        if slot and dest(slot) and dest(slot) in before:
            kind = "select"
        elif "jr" in after or any(o == "lw" and a[:1] == ["ra"] for o, a in insns[idx + 2:idx + 5]):
            kind = "return-arm"
        else:
            kind = "other"
        out.append(kind)
    return collections.Counter(out)


def main():
    d = json.loads((census.HERE / "census.json").read_text())
    total = collections.Counter()
    for r in d["rows"]:
        row = json.loads((census.E / "rows" / f"{r['function']}--routed.json").read_text())
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        tl = (census.E / "ws" / r["function"] / "routed" / "nonmatchings" / r["function"] / "target_object_dump_normalized.s").read_text().splitlines()
        cl = census.apply_diff(tl, node["verdict"]["raw_diff"])
        ts, cs = sites(census.parse(tl)), sites(census.parse(cl))
        extra = {k: ts[k] - cs[k] for k in ts if ts[k] > cs[k]}
        if extra:
            print(f"{r['function'][:40]:40s} {r['score']:6.2f} target-extra {extra}  (T {dict(ts)} C {dict(cs)})")
            total.update(extra)
    print(dict(total))


if __name__ == "__main__":
    main()
