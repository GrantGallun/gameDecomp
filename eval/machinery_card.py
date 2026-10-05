"""Measured ability of each repair mechanism to perform its action, from recorded searches. No compiles.

A declared contract says what a mechanism should do; this measures what it did. For every edge where a
mechanism (mutation family) was applied, the parent's compiled object is compared with the child's:

  broke    the child does not compile: the mechanism emitted C the compiler rejects (a mechanism bug; a
           rewrite that only re-expresses the same C should never do this)
  no_op    the child's instruction diff equals the parent's: the source changed and the object did not
  exact    the child is certified exact
  acted    the child compiled to a different object

Of acted edges: the share that raised the similarity score, the share that was clean (some residual axis of
`solver.signals` shrank and none grew), and which axes shrank or grew. Nothing is declared per mechanism, so
a mechanism that mostly breaks or does nothing shows up as that whatever its docstring intends. The same
parent/child source pair recompiled in another arm counts once.

First run, 2026-09-22 (eval/results/machinery-capability-20260922/): about 9,500 applications over 224
functions found four mechanisms emitting uncompilable C (single_use inlined into a comment, typed_index took
an integer for the table, reloc_symbol swapped a scalar for an array or re-declared a symbol, pointer_table_deref
dereferenced a struct table), `register_storage` a no-op on every game-recipe application (IDO honours
`register` only under the libultra recipe), and `commutative` a no-op on 89% of applications.

    python -m eval.machinery_card ROWS_DIR_OR_WORLD... [--json OUT]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from solver import signals

AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def _axes(verdict: dict) -> dict[str, int]:
    s = signals.analyse(verdict.get("diff") or "", verdict["score"], verdict["exact"], verdict["compiled"])
    return {a: int(getattr(s, a)) for a in AXES}


def _worlds(paths):
    """(function, world) for world files, or rows directories whose rows name a world file."""
    for path in map(Path, paths):
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for file in files:
            data = json.loads(file.read_text())
            if "world" in data and isinstance(data["world"], dict):
                yield data["world"]["context"]["task"], data["world"]
            elif isinstance(data.get("world"), str) and Path(data["world"]).is_file():
                yield data["function"], json.loads(Path(data["world"]).read_text())["world"]


def card(worlds) -> dict:
    rows = collections.defaultdict(lambda: {"edges": 0, "functions": set(), "broke": 0, "no_op": 0, "exact": 0,
                                            "acted": 0, "improved": 0, "clean": 0, "introduced": 0,
                                            "shrank": collections.Counter(), "grew": collections.Counter(),
                                            "refusals": collections.Counter()})
    seen = set()
    for function, world in worlds:
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["parent"] is None:
                continue
            parent = nodes[n["parent"]]
            key = (function, parent["source_sha256"], n["source_sha256"])
            if key in seen:
                continue
            seen.add(key)
            r = rows[n["family"]]
            r["edges"] += 1
            r["functions"].add(function)
            pv, cv = parent["verdict"], n["verdict"]
            if not cv["compiled"]:
                r["broke"] += 1
                first = next((l for l in (cv.get("stderr") or "").splitlines() if "rror" in l), "")
                r["refusals"][first.split(":", 2)[-1].strip()[:90]] += 1
            elif cv["exact"]:
                r["exact"] += 1
            elif (cv.get("diff") or "") == (pv.get("diff") or ""):
                r["no_op"] += 1
            else:
                r["acted"] += 1
                r["improved"] += cv["score"] > pv["score"]
                before, after = _axes(pv), _axes(cv)
                shrank = [a for a in AXES if after[a] < before[a]]
                grew = [a for a in AXES if after[a] > before[a]]
                r["shrank"].update(shrank)
                r["grew"].update(grew)
                r["clean"] += bool(shrank) and not grew
                r["introduced"] += bool(grew)
    out = {}
    for family, r in sorted(rows.items(), key=lambda kv: -kv[1]["edges"]):
        n, acted = r["edges"], r["acted"] or 1
        out[family] = {"edges": n, "functions": len(r["functions"]), "broke": round(r["broke"] / n, 3),
                       "no_op": round(r["no_op"] / n, 3), "exact": r["exact"], "acted": round(r["acted"] / n, 3),
                       "of_acted_improved": round(r["improved"] / acted, 3),
                       "of_acted_clean": round(r["clean"] / acted, 3),
                       "of_acted_introduced": round(r["introduced"] / acted, 3),
                       "shrank": {a: round(v / acted, 3) for a, v in r["shrank"].most_common(3)},
                       "grew": {a: round(v / acted, 3) for a, v in r["grew"].most_common(3)},
                       "top_refusals": r["refusals"].most_common(3)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sources", nargs="+", help="rows directories or world JSON files")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    table = card(_worlds(args.sources))
    if args.json:
        args.json.write_text(json.dumps(table, indent=1))
    print(f"{'mechanism':28} {'edges':>6} {'fns':>4} {'broke':>6} {'no-op':>6} {'acted':>6} {'impr':>5} {'clean':>6} exact")
    for family, t in table.items():
        print(f"{family:28} {t['edges']:6} {t['functions']:4} {t['broke']:6} {t['no_op']:6} {t['acted']:6} "
              f"{t['of_acted_improved']:5} {t['of_acted_clean']:6} {t['exact']:5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
