import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from brief import build, example_index  # noqa: E402

RUN = Path.home() / "decomp/experiments/locality-population-20260923/rows"
index = example_index([RUN, Path.home() / "decomp/experiments/narrow-population-20260923/rows"])
(HERE / "example_index.json").write_text(json.dumps(index, indent=1))
print("example edits indexed:", len(index))
row = json.loads((RUN / "updateCharacterSelectMenu--locality.json").read_text())
world = json.loads(Path(row["world"]).read_text())["world"]
best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
text = build(row["function"], best["verdict"], best["source"], index)
print(len(text), "chars\n")
print(text[:2500])
