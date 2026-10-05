"""Read-only: the reloc_symbol refusals the type-preservation fix does not remove. What do they share?"""
import collections
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import owner_rewrites  # noqa: E402

SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]


def main():
    shown, errors = 0, collections.Counter()
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                if n["parent"] is None or n["family"] != "owner:reloc_symbol" or n["verdict"]["compiled"]:
                    continue
                parent = nodes[n["parent"]]
                now = {c for _l, _k, c in owner_rewrites.candidates("reloc_symbol_rewrites", parent["source"],
                                                                     parent["verdict"].get("diff") or "")}
                if n["source"] not in now:
                    continue
                err = next((l for l in (n["verdict"].get("stderr") or "").splitlines() if "rror" in l), "")
                errors[re.sub(r"line \d+", "line N", err.split(":", 2)[-1].strip())[:100]] += 1
                if shown < 3:
                    shown += 1
                    got, want = re.search(r"reloc symbol (\w+) -> (\w+)", n["label"]).groups()
                    decls = [l.strip() for l in parent["source"].splitlines() if re.search(rf"\b({got}|{want})\b", l)
                             and ("extern" in l or "#include" in l)][:4]
                    print(row["function"], n["label"], "\n   decls:", decls, "\n   error:", err[:160])
    print(errors.most_common(6))


if __name__ == "__main__":
    main()
