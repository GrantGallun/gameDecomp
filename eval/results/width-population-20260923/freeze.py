"""Freeze code-v4 = code-v3 + measured gates, before any compile. Validation of the roadmap's proposed gates.

Acceptance (fixed now): paired against the promoted run on the same 224 sources and budget, gates lose no
exact. Only then are they installed into solver/family_gates.json.
"""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PREV = json.loads((HERE.parent / "member-offset-population-20260923/freeze.json").read_text())
SHARED = HERE.parents[2]
BASE = Path(PREV["code_root"])
DEST = BASE.parent / "code-v7"
OVERLAYS = ["solver/evidence_site.py"]
GATES = HERE.parent / "mechanism-roadmap-20260923/proposed-gates.json"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    assert not DEST.exists() and not (HERE / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    (DEST / "solver/family_gates.json").write_bytes((SHARED / "solver/family_gates.json").read_bytes())
    files = dict(PREV["files"])
    files.update({rel: sha((DEST / rel).read_bytes()) for rel in OVERLAYS + ["solver/family_gates.json"]})
    manifest = {**{k: PREV[k] for k in ("budget_per_arm", "max_depth", "scheduler", "min_free_gb_on_c")},
                "code_root": str(DEST), "files": files, "overlays": OVERLAYS + ["solver/family_gates.json"],
                "gates": json.loads((SHARED / "solver/family_gates.json").read_text())["gates"], "arm": "width", "training_eligible": False,
                "model_calls": 0, "acceptance": "no exact lost vs member_offset, paired"}
    (HERE / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["gates"], indent=1))


if __name__ == "__main__":
    main()
