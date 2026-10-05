"""Freeze code-v10 = code-v9 + solver.branch_shape wired into the search stream, before any compile."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PREV = json.loads((HERE.parent / "locality-population-20260923/freeze.json").read_text())
SHARED = HERE.parents[2]
BASE = Path(PREV["code_root"])
DEST = BASE.parent / "code-v10"
OVERLAYS = ["solver/regalloc_mutations.py", "solver/branch_shape.py"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    assert not DEST.exists() and not (HERE / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    files = dict(PREV["files"])
    files.update({rel: sha((DEST / rel).read_bytes()) for rel in OVERLAYS})
    manifest = {**{k: PREV[k] for k in ("budget_per_arm", "max_depth", "scheduler", "min_free_gb_on_c")},
                "code_root": str(DEST), "files": files, "overlays": OVERLAYS, "gates": PREV.get("gates"),
                "arm": "branch_shape", "training_eligible": False, "model_calls": 0,
                "acceptance": "no exact lost vs locality, paired"}
    (HERE / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"code_root": str(DEST), "files": len(files)}))


if __name__ == "__main__":
    main()
