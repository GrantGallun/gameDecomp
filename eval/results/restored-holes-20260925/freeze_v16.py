"""Freeze code-v16 = code-v15 + main's solver/regalloc_mutations.py (adds pure_local_inlines, 2026-09-25)."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PREV = json.loads((HERE / "freeze.json").read_text())
SHARED = HERE.parents[2]
BASE = Path(PREV["code_root"])
DEST = BASE.parent / "code-v16"
OVERLAYS = ["solver/regalloc_mutations.py"]


def main():
    assert not DEST.exists() and not (HERE / "freeze-v16.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    files = dict(PREV["files"])
    files.update({rel: hashlib.sha256((DEST / rel).read_bytes()).hexdigest() for rel in OVERLAYS})
    manifest = {**PREV, "code_root": str(DEST), "files": files, "overlays": PREV["overlays"] + OVERLAYS,
                "arms": ["pure_inline"],
                "acceptance": "paired against the restored arm on code-v15: no exact lost; which classes pure_inline closes"}
    (HERE / "freeze-v16.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"code_root": str(DEST), "files": len(files)}))


if __name__ == "__main__":
    main()
