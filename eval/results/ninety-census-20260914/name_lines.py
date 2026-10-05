"""Print every stored-source line of a non-compiling node that mentions the given names.

    python3 name_lines.py FUNCTION NAME...
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = {r["function"]: r for r in json.loads((HERE / "not_compiled.json").read_text())}
source = Path(rows[sys.argv[1]]["source"]).read_text()
for name in sys.argv[2:]:
    print("==", name)
    for found in re.finditer(rf"(?m)^.*\b{re.escape(name)}\b.*$", source):
        print("   ", found.group(0).strip()[:150])
