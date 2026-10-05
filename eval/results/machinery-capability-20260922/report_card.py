"""Measured ability of each repair mechanism to perform its action. No compiles; recorded searches only.

For every edge where a mechanism (mutation family) was applied, compare the parent's compiled object with
the child's:
  broke      the child does not compile: the mechanism emitted C the compiler rejects
  no_op      the child's instruction diff is identical to the parent's: the source changed, the object did not
  exact      the child is certified exact
  acted      the child compiled and its diff differs from the parent's
Of the acted edges, per residual axis (solver.signals): how often that axis shrank or grew, and how often
the edge was clean (some axis shrank and none grew). Nothing is declared about what a mechanism is meant to
fix; each one's effect signature is measured, so a mechanism that mostly breaks or mostly does nothing shows
up as that, whatever its docstring intends. Refusal stderr is sampled for the broke column.
"""
import collections
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FREEZE = json.loads((HERE.parent / "population-transfer-20260922/freeze2.json").read_text())
sys.path.insert(0, FREEZE["code_root"])
from solver import signals  # noqa: E402

AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")
SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]


def axes(verdict):
    s = signals.analyse(verdict.get("diff") or "", verdict["score"], verdict["exact"], verdict["compiled"])
    return {a: int(getattr(s, a)) for a in AXES}


def main():
    card = collections.defaultdict(lambda: {"edges": 0, "functions": set(), "broke": 0, "no_op": 0, "exact": 0,
                                            "acted": 0, "improved": 0, "clean": 0, "introduced_any": 0,
                                            "shrank": collections.Counter(), "grew": collections.Counter(),
                                            "stderr": collections.Counter()})
    seen = set()
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                if n["parent"] is None:
                    continue
                parent = nodes[n["parent"]]
                key = (row["function"], parent["source_sha256"], n["source_sha256"])
                if key in seen:                          # the same edit recompiled in another arm is one observation
                    continue
                seen.add(key)
                c = card[n["family"]]
                c["edges"] += 1
                c["functions"].add(row["function"])
                pv, cv = parent["verdict"], n["verdict"]
                if not cv["compiled"]:
                    c["broke"] += 1
                    first = next((l.strip() for l in (cv.get("stderr") or "").splitlines() if "error" in l.lower()), "")
                    c["stderr"][first.split(":", 3)[-1].strip()[:90]] += 1
                    continue
                if cv["exact"]:
                    c["exact"] += 1
                    continue
                if (cv.get("diff") or "") == (pv.get("diff") or ""):
                    c["no_op"] += 1
                    continue
                c["acted"] += 1
                c["improved"] += cv["score"] > pv["score"]
                a, b = axes(pv), axes(cv)
                shrank = [x for x in AXES if b[x] < a[x]]
                grew = [x for x in AXES if b[x] > a[x]]
                c["shrank"].update(shrank)
                c["grew"].update(grew)
                c["clean"] += bool(shrank) and not grew
                c["introduced_any"] += bool(grew)
    table = {}
    for fam, c in sorted(card.items(), key=lambda kv: -kv[1]["edges"]):
        n = c["edges"]
        acted = c["acted"] or 1
        table[fam] = {"edges": n, "functions": len(c["functions"]),
                      "broke": round(c["broke"] / n, 3), "no_op": round(c["no_op"] / n, 3),
                      "exact": c["exact"], "acted": round(c["acted"] / n, 3),
                      "of_acted_improved": round(c["improved"] / acted, 3), "of_acted_clean": round(c["clean"] / acted, 3),
                      "of_acted_introduced": round(c["introduced_any"] / acted, 3),
                      "shrank": {x: round(v / acted, 3) for x, v in c["shrank"].most_common(3)},
                      "grew": {x: round(v / acted, 3) for x, v in c["grew"].most_common(3)},
                      "top_refusals": c["stderr"].most_common(3)}
    (HERE / "report_card.json").write_text(json.dumps(table, indent=1))
    print(f"{'mechanism':28} {'edges':>6} {'fns':>4} {'broke':>6} {'no-op':>6} {'acted':>6} {'impr':>5} {'clean':>6} {'intro':>6} exact  shrank-most")
    for fam, t in table.items():
        top = ",".join(f"{k}:{v}" for k, v in list(t["shrank"].items())[:2])
        print(f"{fam:28} {t['edges']:6} {t['functions']:4} {t['broke']:6} {t['no_op']:6} {t['acted']:6} "
              f"{t['of_acted_improved']:5} {t['of_acted_clean']:6} {t['of_acted_introduced']:6} {t['exact']:5}  {top}")


if __name__ == "__main__":
    main()
