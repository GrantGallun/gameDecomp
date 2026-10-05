"""Census (exploration): backward-branch exit tests, target vs candidate, in every unsolved function.
Kinds: `ne` = bne/beq on two registers, `slt` = bnez/beqz whose operand was set by slt/slti/sltu/sltiu just before,
`z` = bnez/beqz/bgtz/... on a plain value. A mismatch per aligned backward branch (k-th backward branch each side)."""
import collections
import json
import re
from pathlib import Path

import census


def kinds(insns):
    out = []
    for idx, (op, args) in enumerate(insns):
        t = census.target_of(op, args)
        if t is None or op in census.UNCOND or t > idx:
            continue
        if op in ("bne", "beq"):
            out.append("ne")
        elif op in ("bnez", "beqz"):
            src = args[0]
            setter = next((o for o, a in reversed(insns[max(0, idx - 4):idx]) if a[:1] == [src]), None)
            out.append("slt" if setter and setter.startswith("slt") else "z")
        else:
            out.append(op)
    return out


def main():
    d = json.loads((census.HERE / "census.json").read_text())
    pairs, funcs, rows = collections.Counter(), collections.Counter(), []
    for r in d["rows"]:
        row = json.loads((census.E / "rows" / f"{r['function']}--routed.json").read_text())
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        tl = (census.E / "ws" / r["function"] / "routed" / "nonmatchings" / r["function"] / "target_object_dump_normalized.s").read_text().splitlines()
        cl = census.apply_diff(tl, node["verdict"]["raw_diff"])
        tk, ck = kinds(census.parse(tl)), kinds(census.parse(cl))
        if len(tk) != len(ck):
            funcs["backward-count-differs"] += 1
            continue
        mism = [(a, b) for a, b in zip(tk, ck) if a != b]
        for m in mism:
            pairs[m] += 1
        if mism:
            funcs["exit-test-mismatch"] += 1
            rows.append({"function": r["function"], "class": r["class"], "score": r["score"], "mismatch": mism})
        elif tk:
            funcs["loops-agree"] += 1
        else:
            funcs["no-loops"] += 1
    print(dict(funcs))
    print({f"T {a} / C {b}": n for (a, b), n in pairs.items()})
    for x in rows:
        print(x["function"][:40], x["class"], x["score"], x["mismatch"])
    (census.HERE / "exit_tests.json").write_text(json.dumps({"funcs": funcs, "pairs": {f"{a}/{b}": n for (a, b), n in pairs.items()}, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
