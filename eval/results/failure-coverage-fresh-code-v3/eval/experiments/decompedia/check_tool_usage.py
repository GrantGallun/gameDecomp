"""Record installed transitive tool usage, without executing their pipelines."""
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re

rows = []
for package in ("splat", "m2c"):
    spec = importlib.util.find_spec(package)
    if spec is None:
        continue
    root = Path(spec.origin).parent
    for file in root.rglob("*.py"):
        for number, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
            if re.search(r"\b(import|from)\s+(spimdisasm|n64img|pygfxd)\b", line):
                rows.append({"package": package, "file": str(file), "line": number, "text": line.strip()})
out = Path(__file__).parent / "tool-usage.json"
out.write_text(json.dumps(rows, indent=2) + "\n")
print(json.dumps(rows, indent=2))
