"""Freeze code-v3 = stage-2 code-v2 + today's solver changes, before any compile. One arm, `promoted`.

Compared, paired per function, with stage-2 `routed` (same 224 frozen sources, scheduler, 32-call budget,
depth 4). The difference is exactly the overlay list: the four machinery fixes (rewrites.py,
regalloc_mutations.py), the two derived mechanisms (evidence_site.py, frontend_type_repair.py) and the
scheduler passing each node's verdict to generators that ask for it.
"""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
SHARED = HERE.parents[2]
STAGE2 = json.loads((PT / "freeze2.json").read_text())
BASE = Path(STAGE2["code_root"])
DEST = BASE.parent / "code-v3"
OVERLAYS = ["solver/regalloc_mutations.py", "solver/rewrites.py", "solver/evidence_site.py",
            "solver/frontend_type_repair.py", "solver/source_attribution.py", "solver/residual_sites.py",
            "solver/regalloc_search.py", "eval/search_scheduler.py"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    assert not DEST.exists() and not (HERE / "freeze.json").exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OVERLAYS:
        (DEST / rel).write_bytes((SHARED / rel).read_bytes())
    files = {k: sha((DEST / k).read_bytes()) for k in STAGE2["files"]}
    files.update({rel: sha((DEST / rel).read_bytes()) for rel in OVERLAYS})
    changed = sorted(k for k in files if STAGE2["files"].get(k) != files[k])
    assert set(changed) <= set(OVERLAYS), changed
    manifest = {"stage2_freeze_sha256": sha((PT / "freeze2.json").read_bytes()), "code_root": str(DEST),
                "files": files, "overlays": OVERLAYS, "changed_vs_stage2": changed,
                "arm": "promoted: code-v3 variants() with the parent verdict as evidence",
                "budget_per_arm": STAGE2["budget_per_arm"], "max_depth": STAGE2["max_depth"],
                "scheduler": STAGE2["scheduler"], "min_free_gb_on_c": 3.0, "training_eligible": False,
                "model_calls": 0, "comparison": "paired per function against stage-2 routed rows"}
    (HERE / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"changed": changed}, indent=1))


if __name__ == "__main__":
    main()
