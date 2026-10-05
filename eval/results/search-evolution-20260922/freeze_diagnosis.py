"""Reuse the measured engine; overlay only the reviewed proposer and new driver."""
import hashlib
import json
from pathlib import Path
import shutil

SHARED = Path(__file__).resolve().parents[3]
BASE = Path.home() / "decomp/experiments/search-evolution-20260922/code-v3"
DEST = BASE.parent / "code-diagnosis-v1"


def main():
    assert not DEST.exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    overlays = ["eval/search_evolution.py", "eval/results/search-evolution-20260922/diagnose.py"]
    for relative in overlays:
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SHARED / relative).read_bytes())
    frozen = json.loads((BASE / "snapshot.json").read_text())
    for relative, digest in frozen["files"].items():
        if relative not in overlays:
            assert hashlib.sha256((DEST / relative).read_bytes()).hexdigest() == digest, relative
    (DEST / "diagnosis-snapshot.json").write_text(json.dumps({"base": str(BASE),
        "overlays": {p: hashlib.sha256((DEST / p).read_bytes()).hexdigest() for p in overlays}}, indent=2))
    print(str(DEST))


if __name__ == "__main__":
    main()
