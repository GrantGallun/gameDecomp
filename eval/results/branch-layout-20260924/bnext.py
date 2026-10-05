"""Count unconditional branches to the block right after their delay slot ("b-next") on each side, and where the
target's extra unconditional branches go (next block / epilogue / elsewhere). Exploration."""
import collections
import json
from pathlib import Path

import census


def uncond(insns):
    out = []
    for idx, (op, args) in enumerate(insns):
        if op in census.UNCOND:
            t = census.target_of(op, args)
            epi = t is not None and any(o == "jr" and a[:1] == ["ra"] for o, a in insns[t:t + 6])
            kind = "next" if t == idx + 2 else "epilogue" if epi else "back" if t is not None and t <= idx else "forward"
            out.append(kind)
    return collections.Counter(out)


def main():
    d = json.loads((census.HERE / "census.json").read_text())
    agg = collections.Counter()
    rows = []
    for r in d["rows"]:
        row = json.loads((census.E / "rows" / f"{r['function']}--routed.json").read_text())
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        tl = (census.E / "ws" / r["function"] / "routed" / "nonmatchings" / r["function"] / "target_object_dump_normalized.s").read_text().splitlines()
        cl = census.apply_diff(tl, node["verdict"]["raw_diff"])
        tu, cu = uncond(census.parse(tl)), uncond(census.parse(cl))
        if tu != cu:
            extra = {k: tu[k] - cu[k] for k in set(tu) | set(cu) if tu[k] != cu[k]}
            rows.append({"function": r["function"], "class": r["class"], "score": r["score"], "t": dict(tu), "c": dict(cu), "t_minus_c": extra,
                         "recipe": node["verdict"].get("compiler_recipe")})
            for k, v in extra.items():
                agg[(k, "target-more" if v > 0 else "candidate-more")] += 1
    for x in rows:
        print(x["function"][:40], x["class"], x["score"], x["t_minus_c"], x["recipe"])
    print(dict(agg))
    (census.HERE / "bnext.json").write_text(json.dumps({"agg": {f"{a}/{b}": n for (a, b), n in agg.items()}, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
