"""Read current DB/tool state for the claims in the follow-up message."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.zero_token_harvest import heldout_names

repo = Path.home() / "decomp/sbk1"
db = Path.home() / "decomp/kb-sbk1.sqlite"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
names = ["checkMainMenuSecretCode", "updateCourseSelectCourseDescription", "bootThreadMain",
         "initControllerPakRaceRecordSaveFlow", "compressRaceRecordReplayData",
         "drawPulsingAssetTableSprite", "drawMenuSpriteWithAlphaClipped"]
out = ROOT / "eval/experiments/decompedia/current-claims-v1"
out.mkdir(exist_ok=False)
heldout = heldout_names(ROOT / "eval/sets")
report = {"scope": "read-only current state; stored scores are not fresh verification",
          "state_counts": [dict(r) for r in conn.execute("SELECT state,COUNT(*) count FROM functions GROUP BY state")],
          "tools": {}, "functions": []}
for package in ["spimdisasm", "splat64", "m2c", "pygfxd", "n64img"]:
    try:
        report["tools"][package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        report["tools"][package] = None
for name in names:
    row = conn.execute("SELECT f.name, f.addr, f.state, f.best_score, f.attempts, t.name tu "
                       "FROM functions f LEFT JOIN tus t ON t.id=f.tu_id WHERE f.name=?", (name,)).fetchone()
    if not row:
        report["functions"].append({"function": name, "status": "missing"})
        continue
    item = dict(row)
    item["heldout"] = name in heldout
    item["actual_attempt_count"] = conn.execute("SELECT COUNT(*) FROM attempts WHERE func_addr=?", (row["addr"],)).fetchone()[0]
    if name not in heldout:
        best = conn.execute("SELECT id,source_code,score,strategy,compiled FROM attempts WHERE func_addr=? "
                            "ORDER BY compiled DESC,score DESC,id DESC LIMIT 1", (row["addr"],)).fetchone()
        if best:
            source = best["source_code"]
            item["best_stored"] = {k: best[k] for k in best.keys() if k != "source_code"}
            item["best_stored"]["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
            (out / f"{name}.c").write_text(source)
        target = repo / "nonmatchings" / name / "target.s"
        item["workspace_exists"] = target.exists()
        if target.exists():
            (out / f"{name}.s").write_bytes(target.read_bytes())
    report["functions"].append(item)
(out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
