"""Save the first recorded failing edge of each defective mechanism as a test fixture (source + diff + label)."""
import json
from pathlib import Path

SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]
FIXTURES = Path("/mnt/c/Code/gameDecomp/tests/fixtures")
WANT = {"single_use": "copyPackedMatrixTranslation", "typed_index": "__osPfsRWInode",
        "owner:reloc_symbol": "MusStartEffect", "owner:pointer_table_deref": "tryStartRacePlayerCourseObjectMode"}


def main():
    found = {}
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world") or row["function"] not in WANT.values():
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                fam = n["family"]
                if n["parent"] is None or WANT.get(fam) != row["function"] or fam in found or n["verdict"]["compiled"]:
                    continue
                parent = nodes[n["parent"]]
                stem = "machinery_" + fam.replace("owner:", "").replace(":", "_")
                (FIXTURES / f"{stem}.c").write_text(parent["source"])
                (FIXTURES / f"{stem}.diff").write_text(parent["verdict"].get("diff") or "")
                found[fam] = {"function": row["function"], "label": n["label"], "stem": stem,
                              "bad_candidate_sha256": n["source_sha256"]}
    (FIXTURES / "machinery_failures.json").write_text(json.dumps(found, indent=1))
    print(json.dumps(found, indent=1))


if __name__ == "__main__":
    main()
