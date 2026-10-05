"""L3: declaration edits (local_type, decl_order): do they improve more when the declared variable is used on a
faulty line? Recorded edges only. Supported if >= 2x with >= 100 edges each side (same bar as L1)."""
import collections
import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from locality import RUNS, residual_lines  # noqa: E402

DECL = re.compile(r"^\s*(?:[A-Za-z_]\w*\s+)+\**\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:=[^;]*)?;\s*$")


def declared_names(parent, child):
    names = set()
    a, b = parent.split("\n"), child.split("\n")
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        for line in a[a0:a1] + b[b0:b1]:
            m = DECL.match(line)
            if m:
                names.add(m.group(1))
    return names


def main():
    seen, cells = set(), collections.Counter()
    home = Path.home() / "decomp/experiments"
    for run in RUNS + ["locality-population-20260923/rows"]:
        for path in sorted((home / run).glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world") or not Path(row["world"]).exists():
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            cache = {}
            for n in world["nodes"]:
                if n["parent"] is None or n["family"] not in ("local_type", "decl_order"):
                    continue
                p = nodes[n["parent"]]
                key = (row["function"], p["source_sha256"], n["source_sha256"])
                if key in seen or not p["verdict"]["compiled"] or p["verdict"]["exact"]:
                    continue
                seen.add(key)
                if p["id"] not in cache:
                    lines = residual_lines(p["verdict"], p["source"])
                    src = p["source"].split("\n")
                    cache[p["id"]] = None if not lines else " ".join(src[l - 1] for l in lines if 0 < l <= len(src))
                faulty_text = cache[p["id"]]
                if faulty_text is None:
                    continue
                names = declared_names(p["source"], n["source"])
                on_fault = any(re.search(rf"\b{re.escape(x)}\b", faulty_text) for x in names)
                v = n["verdict"]
                improved = v["exact"] or (v["compiled"] and v["score"] > p["verdict"]["score"])
                cells[(n["family"], on_fault, improved)] += 1
    out = {}
    for fam in ("local_type", "decl_order"):
        row = {}
        for side in (True, False):
            k, n = cells[(fam, side, True)], cells[(fam, side, True)] + cells[(fam, side, False)]
            row["on_faulty_line" if side else "not_on_faulty_line"] = {"n": n, "improve_rate": round(k / n, 4) if n else None}
        a, b = row["on_faulty_line"], row["not_on_faulty_line"]
        row["L3"] = "supported" if a["improve_rate"] and b["n"] >= 100 and a["n"] >= 100 and \
            (b["improve_rate"] == 0 or a["improve_rate"] >= 2 * b["improve_rate"]) else "not supported"
        out[fam] = row
    (HERE / "decl_locality.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
