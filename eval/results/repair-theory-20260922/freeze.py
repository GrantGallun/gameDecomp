"""Freeze theory implementation and the exposed regression panel before trials."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent / "repair-transition-20260922"
DEST = Path.home() / "decomp/experiments/repair-theory-20260922/code-v1"


def main():
    assert not DEST.exists() and not (OUT / "freeze.json").exists()
    old = json.loads((PRIOR / "freeze.json").read_text())
    shutil.copytree(old["code_root"],DEST,ignore=shutil.ignore_patterns("__pycache__"))
    # Use the current tested owners; all are frozen equally in every arm.
    overlays = sorted(p for directory in ("solver","eval","kb") for p in (ROOT/directory).glob("*.py"))
    for path in overlays:
        (DEST / path.relative_to(ROOT)).write_bytes(path.read_bytes())
    files = sorted(p for directory in ("solver","eval","kb") for p in (DEST/directory).glob("*.py"))
    files += [DEST / "kb/schema.sql",DEST / "eval/results/dream-search-20260922/pilot.py"]
    for name in ("model.json","development-graph.json"):
        shutil.copyfile(PRIOR/name,OUT/name)
    panel = json.loads((PRIOR/"panel.json").read_text())
    for row in panel["rows"]:
        if row["status"] == "generated":
            row["source"] = "../repair-transition-20260922/"+row["source"]
    panel["selection"] = "revisit all three previously failed timer drafts; five previously unavailable names remain unavailable; not fresh holdout"
    (OUT/"panel.json").write_text(json.dumps(panel,indent=2))
    manifest = {**old,"code_root":str(DEST),"base":old["code_root"],
        "files":{p.relative_to(DEST).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "budget_per_arm":16,"arms":["baseline","intake","theory"],
        "nproc":int(subprocess.check_output(["nproc"],text=True)),"workers":1,
        "claim_scope":"three exposed failed drafts plus seven exact retention cases; theory/adapter ablation, no held-out transfer"}
    (OUT/"freeze.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"code_root":str(DEST),"fixed_files":len(files),"nproc":manifest["nproc"],"budget_per_arm":16}))


if __name__ == "__main__":
    main()
