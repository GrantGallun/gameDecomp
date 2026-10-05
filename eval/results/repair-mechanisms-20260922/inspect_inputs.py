"""Inspect development drafts, recorded attempts, and immutable target assembly."""
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
NAMES = ["Fvibup", "Fvibdown", "FrandPan", "releaseMenuAssetHandles",
         "allocTranslationOnlyFixedMatrix", "loadMusicSequenceBank",
         "osEPiRawReadIo", "osEPiRawWriteIo"]


def main():
    db = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    rows = []
    for name in NAMES:
        source = ROOT / "eval/results/clean-residuals-20260922/states" / name / "final.c"
        if not source.exists():
            source = ROOT / "eval/results/residual-repair-20260922" / f"{name}--baseline.c"
        ws = REPO / "nonmatchings" / name
        target = workspace.target_asm(ws, name)
        path = OUT / "inputs" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "initial.c").write_text(source.read_text())
        (path / "target.s").write_text(target)
        prior = db.execute("SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                           "WHERE f.name=? AND a.exact=1", (name,)).fetchone()[0]
        rows.append({"function": name, "source_origin": str(source), "prior_object_exact_attempts": prior})
    (OUT / "inspection.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))
    print("OTHER MMIO WORKSPACES", [p.name for p in (REPO / "nonmatchings").glob("*Raw*Io")])
    db.close()


if __name__ == "__main__":
    main()
