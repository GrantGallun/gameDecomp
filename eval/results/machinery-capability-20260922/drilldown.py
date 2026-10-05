"""Read-only drill-down: why the broken mechanisms break, and whether no-ops depend on the compiler recipe."""
import collections
import json
from pathlib import Path
import sqlite3
import difflib

SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BROKE = {"owner:reloc_symbol", "typed_index", "owner:pointer_table_deref", "single_use"}
NOOP = {"register_storage", "commutative", "decl_order", "truth_test"}


def recipes():
    """Function name -> optimisation flags from the compiler recipe the workspace build uses."""
    out = {}
    for path in (Path.home() / "decomp/sbk1/nonmatchings").glob("*/.compiler-target.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        # The workspace names only the target object; libultra objects build under src/ultra.
        out[path.parent.name] = "libultra" if "/ultra/" in data.get("target", "") else "game"
    return out


def main():
    rec = recipes()
    broke_examples = collections.defaultdict(list)
    noop_by_recipe = collections.defaultdict(collections.Counter)
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
                parent, v = nodes[n["parent"]], n["verdict"]
                fam = n["family"]
                if fam in BROKE and not v["compiled"] and len(broke_examples[fam]) < 3:
                    edit = "".join(l for l in difflib.unified_diff(parent["source"].splitlines(True),
                                   n["source"].splitlines(True), n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---"))
                    errors = [l for l in (v.get("stderr") or "").splitlines() if "error" in l.lower()][:2]
                    broke_examples[fam].append({"function": row["function"], "label": n["label"],
                                                "edit": edit[:600], "errors": errors})
                if fam in NOOP and v["compiled"] and not v["exact"]:
                    same = (v.get("diff") or "") == (parent["verdict"].get("diff") or "")
                    noop_by_recipe[fam][(rec.get(row["function"], "unknown"), "no_op" if same else "acted")] += 1
    print("recipes known:", collections.Counter(rec.values()))
    for fam, counts in noop_by_recipe.items():
        print(fam, dict(counts))
    for fam, examples in broke_examples.items():
        print("=" * 80, "\n", fam)
        for e in examples:
            print(f"--- {e['function']} {e['label']}\n{e['edit']}\nERRORS: {e['errors']}")


if __name__ == "__main__":
    main()
