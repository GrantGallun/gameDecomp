"""How much of the unsolved residual is frame layout? From each unsolved function's best node diff (restart round 3).

A changed line is `frame` if it touches sp: the prologue/epilogue `addiu sp,sp,N`, a `(sp)` access, or `addiu r,sp,N`.
"""
import collections
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS = Path.home() / "decomp/experiments/restart-round3-20260923/rows"
SP = re.compile(r"\(sp\)|\bsp,sp,|,sp,")


def changed(diff):
    return [l for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def main():
    out, per = collections.Counter(), []
    for path in sorted(ROWS.glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        lines = changed(node["verdict"].get("diff") or "")
        if not lines:
            out["no_diff"] += 1
            continue
        sp = [l for l in lines if SP.search(l)]
        size = sorted({l for l in lines if re.search(r"addiu\s+sp,sp,", l)})
        rec = {"function": row["function"], "score": row.get("best_score"), "changed": len(lines), "sp": len(sp),
               "frame_size_differs": bool(size), "sp_only": len(sp) == len(lines), "size_lines": size[:4]}
        per.append(rec)
        out["functions"] += 1
        out["with_sp_lines"] += bool(sp)
        out["frame_size_differs"] += bool(size)
        out["sp_only"] += rec["sp_only"]
        out["sp_lines"] += len(sp)
        out["changed_lines"] += len(lines)
    summary = dict(out)
    summary["sp_share_of_changed_lines"] = round(out["sp_lines"] / max(1, out["changed_lines"]), 3)
    summary["sp_only_functions"] = [r["function"] for r in per if r["sp_only"]]
    summary["size_differs_top"] = sorted((r for r in per if r["frame_size_differs"]), key=lambda r: r["changed"])[:15]
    print(json.dumps(summary, indent=1))
    (HERE / "frame_census.json").write_text(json.dumps({"summary": summary, "rows": per}, indent=1))


if __name__ == "__main__":
    main()
