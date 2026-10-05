"""List draft-lowering compiles by best label: python3 lowering_wins.py"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in sorted((HERE / "draft-lowering").glob("*.json")):
    entry = json.loads(path.read_text())
    if entry.get("outcome") == "compiles":
        best = entry["best"]
        print(f"{best['label']:40} score={best['score']:7} frontend={best['frontend']} {entry['function']}")
