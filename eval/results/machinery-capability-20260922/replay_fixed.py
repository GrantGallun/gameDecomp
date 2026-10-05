"""Replay the fixed generators on every recorded parent where a defective mechanism emitted uncompilable C.

No compiles here. For each recorded refused child: does the fixed generator still emit that exact source?
For each such parent: which candidates does the fixed generator emit that were never compiled? Those are
written to probes-fixed.json for compilation through the population-transfer probe driver.
"""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import regalloc_mutations, rewrites, owner_rewrites  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]
FAMILIES = {"single_use", "typed_index", "owner:reloc_symbol", "owner:pointer_table_deref"}


def emitted(family, source, function, diff):
    if family == "single_use":
        return {c for _l, _k, c in regalloc_mutations.single_use_local_inlines(source, function)}
    if family == "typed_index":
        return {c for _l, _k, c in regalloc_mutations.typed_index_scales(source, function)}
    name = family.replace("owner:", "") + "_rewrites"
    return {c for _l, _k, c in owner_rewrites.candidates(name, source, diff)}


def main():
    stats = collections.defaultdict(collections.Counter)
    probes, seen = [], set()
    compiled_before = collections.defaultdict(set)
    records = []
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                compiled_before[row["function"]].add(n["source"])
                if n["parent"] is None or n["family"] not in FAMILIES:
                    continue
                if n["verdict"]["compiled"]:
                    # Collateral check: a compiled child must still be emitted by the fixed generator.
                    parent = nodes[n["parent"]]
                    now = emitted(n["family"], parent["source"], row["function"], parent["verdict"].get("diff") or "")
                    good = n["verdict"]["exact"] or n["verdict"]["score"] > parent["verdict"]["score"]
                    key = "compiled_improving" if good else "compiled_other"
                    stats[n["family"]][key] += 1
                    if n["source"] not in now:
                        stats[n["family"]][key + "_lost"] += 1
                    continue
                records.append((row["function"], n["family"], nodes[n["parent"]], n))
    for function, family, parent, child in records:
        now = emitted(family, parent["source"], function, parent["verdict"].get("diff") or "")
        stats[family]["refused_children"] += 1
        stats[family]["still_emitted" if child["source"] in now else "no_longer_emitted"] += 1
        for candidate in now - compiled_before[function]:
            key = (function, candidate)
            if key not in seen:
                seen.add(key)
                stats[family]["new_candidates"] += 1
                probes.append({"function": function, "label": f"fixed:{family}", "source": candidate})
    (HERE / "probes-fixed.json").write_text(json.dumps(probes, indent=1))
    print(json.dumps({k: dict(v) for k, v in stats.items()}, indent=1))
    print("new candidates to compile:", len(probes))


if __name__ == "__main__":
    main()
