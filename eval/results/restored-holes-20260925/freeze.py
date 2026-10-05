"""Freeze code-v15 = code-v14 (at-inline-20260924) + main's solver/regalloc_search.py (tie rule restored 2026-09-25).

stack_layout.py and address_symbols.py in code-v14 are byte-identical to main, so the restored arm needs no other
overlay: what was missing in the Sept 23-24 searches was the wiring, not the modules.
"""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PREV = json.loads((HERE.parent / "at-inline-20260924/freeze.json").read_text())
SHARED = HERE.parents[2]
BASE = Path(PREV["code_root"])
DEST = BASE.parent / "code-v15"
OVERLAYS = ["solver/regalloc_search.py"]


def main():
    assert not DEST.exists() and not (HERE / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    files = dict(PREV["files"])
    for rel in OVERLAYS + ["solver/stack_layout.py", "solver/address_symbols.py", "solver/regalloc_signature.py"]:
        files[rel] = hashlib.sha256((DEST / rel).read_bytes()).hexdigest()
    manifest = {**{k: PREV[k] for k in ("budget_per_arm", "max_depth", "scheduler", "min_free_gb_on_c", "gates")},
                "code_root": str(DEST), "files": files, "overlays": PREV["overlays"] + OVERLAYS,
                "arms": ["control", "restored"], "training_eligible": False, "model_calls": 0,
                "acceptance": "coverage: which residual classes the restored generators reduce, paired against control"}
    (HERE / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"code_root": str(DEST), "files": len(files)}))


if __name__ == "__main__":
    main()
