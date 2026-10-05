"""Split rodata_where_named differences by what the target names: jump table, float/double constant, or address (string/data).

    python3 eval/results/ninety-census-20260914/rodata_kinds.py
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
classes = json.loads((HERE / "classes.json").read_text())
per_function = defaultdict(set)
examples = defaultdict(list)
for row in classes["rows"]:
    if "rodata_where_named" not in row["families"]:
        continue
    entry = json.loads((HERE / "diffs" / f"{row['function']}.json").read_text())
    for d in (entry.get("compare") or {}).get("differences", []):
        if "kind" not in d:
            continue
        for want, got in zip(d["target"], d["candidate"]):
            w, g = re.search(r"%lo\(([^)]+)\)", want), re.search(r"%lo\((\.[^)+]+)", got)
            if not (w and g):
                continue
            mnemonic = want.split()[0]
            kind = ("jump_table" if w.group(1).startswith("jtbl") or mnemonic == "lw" and "jtbl" in want else
                    "float" if mnemonic in ("lwc1", "ldc1") else
                    "address" if mnemonic == "addiu" else f"load:{mnemonic}")
            per_function[row["function"]].add(kind)
            if len(examples[kind]) < 6:
                examples[kind].append(f"{row['function']}: {want} / {got}")
others = {r["function"]: [f for f in r["families"] if f not in ("register", "rodata_where_named")] for r in classes["rows"]}
print(json.dumps({"functions": len(per_function),
                  "kinds": dict(Counter(k for kinds in per_function.values() for k in kinds)),
                  "only_rodata_plus_register_by_kind": dict(Counter("+".join(sorted(k)) for f, k in per_function.items() if not others[f])),
                  "examples": examples}, indent=1))
