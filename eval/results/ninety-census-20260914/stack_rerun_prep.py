"""Move first-pass non-exact stack-probe results aside so probe_stack.py reruns them with the current module."""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
probe, keep = HERE / "stack-probe", HERE / "stack-probe-pass1"
keep.mkdir(exist_ok=True)
moved = 0
for path in probe.glob("*.json"):
    if path.name == "summary.json":
        shutil.move(str(path), keep / path.name)
        continue
    if json.loads(path.read_text()).get("outcome") != "exact":
        shutil.move(str(path), keep / path.name)
        source = path.with_suffix(".c")
        if source.exists():
            shutil.move(str(source), keep / source.name)
        moved += 1
print("moved", moved)
