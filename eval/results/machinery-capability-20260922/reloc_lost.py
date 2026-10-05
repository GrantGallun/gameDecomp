"""Read-only: which reloc_symbol guard removed the compiled, improving candidates?"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import c89, owner_rewrites  # noqa: E402

SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]


def main():
    seen = set()
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                if n["parent"] is None or n["family"] != "owner:reloc_symbol" or not n["verdict"]["compiled"]:
                    continue
                parent = nodes[n["parent"]]
                if not (n["verdict"]["exact"] or n["verdict"]["score"] > parent["verdict"]["score"]):
                    continue
                now = {c for _l, _k, c in owner_rewrites.candidates("reloc_symbol_rewrites", parent["source"],
                                                                     parent["verdict"].get("diff") or "")}
                if n["source"] in now or n["source_sha256"] in seen:
                    continue
                seen.add(n["source_sha256"])
                got, want = re.search(r"reloc symbol (\w+) -> (\w+)", n["label"]).groups()
                masked = c89._mask(parent["source"])
                decl = lambda s: [l.strip() for l in parent["source"].splitlines() if re.search(rf"\b{s}\b", l) and "extern" in l]
                print(row["function"], n["label"], f"{parent['verdict']['score']} -> {n['verdict']['score']}")
                print("   got decl:", decl(got), "\n   want decl:", decl(want),
                      "\n   want mentioned in parent:", len(re.findall(rf"\b{want}\b", masked)),
                      "\n   child adds decl:", [l.strip() for l in n["source"].splitlines() if want in l and "extern" in l])


if __name__ == "__main__":
    main()
