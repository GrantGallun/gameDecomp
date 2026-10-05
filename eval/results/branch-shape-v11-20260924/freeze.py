"""Freeze code-v12 = code-v10 + the working copy's solver/branch_shape.py, before any compile."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PREV = json.loads((HERE.parent / "branch-shape-population-20260924/freeze.json").read_text())
SHARED = HERE.parents[2]
BASE = Path(PREV["code_root"])
DEST = BASE.parent / "code-v12"
OVERLAYS = ["solver/branch_shape.py"]


def main():
    assert not DEST.exists() and not (HERE / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    files = dict(PREV["files"])
    files.update({rel: hashlib.sha256((DEST / rel).read_bytes()).hexdigest() for rel in OVERLAYS})
    manifest = {**{k: PREV[k] for k in ("budget_per_arm", "max_depth", "scheduler", "min_free_gb_on_c", "gates")},
                "code_root": str(DEST), "files": files, "overlays": PREV["overlays"] + OVERLAYS,
                "arm": "branch_shape_v12", "training_eligible": False, "model_calls": 0,
                "acceptance": "no exact lost vs code-v10 and locality, paired"}
    (HERE / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"code_root": str(DEST), "files": len(files)}))


if __name__ == "__main__":
    main()
